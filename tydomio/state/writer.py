"""The write path: checked against the cache, rate-limited, sent once."""

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import NoneType
from typing import Annotated, Final, Literal, get_args, get_origin

from pydantic import TypeAdapter
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue

from ..error import CommunicationError, InvalidTarget, InvalidWrite
from ..installation import Installation
from ..model.config import Config
from ..model.usages import (
    USAGES,
    WRITES,
    Area,
    Boiler,
    Re2020ControlBoiler,
    Shutter,
    UsageModel,
    UsageWrite,
)
from ..model.usages.area import AreaWrite
from ..requests import ClientRequest, WriteArea, WriteEndpoint, WriteGroup
from ..response import Ok
from .cache import Cache

_logger = logging.getLogger(__name__)

SHUTTER_USAGES: Final[frozenset[type[UsageModel]]] = frozenset({Shutter})
"""The usages written as shutters: endpoints and groups."""
HEATING_USAGES: Final[frozenset[type[UsageModel]]] = frozenset(
    {Boiler, Re2020ControlBoiler, Area}
)
"""The usages written as heating: endpoints of these usages, and areas."""
HEATING_AREA_USAGES: Final = frozenset({"re2020ControlBoiler"})
"""The `last_usage` of the config areas written as heating (E16)."""


@dataclass(frozen=True)
class Policy:
    """What each target kind may receive: `e:` and `g:` by usage, `a:` by last_usage."""

    endpoints: frozenset[type[UsageModel]]
    groups: frozenset[type[UsageModel]]
    areas: frozenset[str]


SHUTTER: Final = Policy(SHUTTER_USAGES, SHUTTER_USAGES, frozenset())
"""Shutter endpoints and groups only."""
HEATING: Final = Policy(HEATING_USAGES - {Area}, frozenset(), HEATING_AREA_USAGES)
"""Heating endpoints and areas; no group."""
EVERYTHING: Final = Policy(
    frozenset(WRITES) - {Area}, SHUTTER_USAGES, HEATING_AREA_USAGES
)
"""Every writable endpoint usage, shutter groups, and the heating areas."""


@dataclass(frozen=True)
class Sent:
    """A write sent once: acknowledged by the Tydom, or its outcome unknown."""

    outcome: Literal["acknowledged", "unknown"]
    fields: dict[str, object]
    sent_at: datetime


@dataclass(frozen=True)
class NotSent:
    """A write refused before the send; `detail` is the `InvalidWrite` message."""

    reason: Literal[
        "not_synced", "unknown_target", "wrong_usage", "invalid_write", "rate_limited"
    ]
    detail: str | None = None
    retry_after_s: float | None = None


_TARGET: Final = re.compile(r"e:[0-9]{1,15}:[0-9]{1,15}|[ga]:[0-9]{1,15}")
_ON: Final = {"e": "on an endpoint", "g": "on a group", "a": "on an area"}


def _field_schema(info: FieldInfo) -> JsonSchemaValue:
    """Give the JSON schema of one write field: its enum, or its type and bounds."""
    (kind,) = (arg for arg in get_args(info.annotation) if arg is not NoneType)
    if get_origin(kind) is Annotated:
        # an enum checked by member_of, which hides it from pydantic's schema
        return {"enum": [member.value for member in get_args(kind)[0]]}
    return TypeAdapter(Annotated[kind, *info.metadata]).json_schema()


def write_fields(model: type[UsageWrite[UsageModel]]) -> dict[str, JsonSchemaValue]:
    """Give the JSON schema of each field of a write model, by Tydom name."""
    return {
        info.alias: _field_schema(info)
        for info in model.model_fields.values()
        if info.alias is not None
    }


class _Refused(Exception):
    """A refusal before the send."""

    def __init__(self, not_sent: NotSent) -> None:
        super().__init__(not_sent.reason)
        self.not_sent = not_sent


def _parse(target: str) -> tuple[str, list[int]]:
    """Give the target's kind and ids; `unknown_target` if malformed."""
    if _TARGET.fullmatch(target) is None:
        raise _Refused(NotSent("unknown_target"))
    kind, *numbers = target.split(":")
    return kind, [int(n) for n in numbers]


def _usage(
    config: Config, kind: str, ids: list[int], policy: Policy
) -> type[UsageModel]:
    """Check steps 2 and 3: the usage in the config, allowed by the policy."""
    allowed = {"e": policy.endpoints, "g": policy.groups, "a": policy.areas}[kind]
    # a kind the policy gives nothing, such as a heating group, is refused unread
    if not allowed:
        raise _Refused(NotSent("wrong_usage"))
    if kind == "e":
        usages = {(e.id_device, e.id_endpoint): e.last_usage for e in config.endpoints}
        usage = usages.get((ids[0], ids[1]))
    elif kind == "g":
        usage = {g.id: g.usage for g in config.groups}.get(ids[0])
    else:
        usage = {a.id: a.last_usage for a in config.areas}.get(ids[0])
    if usage is None:
        raise _Refused(NotSent("unknown_target"))
    if kind == "a":
        if usage not in policy.areas:
            raise _Refused(NotSent("wrong_usage"))
        return Area
    model = USAGES.get(usage)
    if model is None or model not in allowed:
        raise _Refused(NotSent("wrong_usage"))
    return model


def write_model(
    config: Config, target: str, policy: Policy
) -> type[UsageWrite[UsageModel]] | None:
    """Give the write model a target accepts under a policy; `None` if refused."""
    try:
        return WRITES[_usage(config, *_parse(target), policy)]
    except _Refused:
        return None


def _ref[R](get: Callable[[], R]) -> R:
    """Get the ref from `Installation`, which checks again."""
    try:
        return get()
    except InvalidTarget:
        raise _Refused(NotSent("unknown_target")) from None


def _validated[W: UsageWrite[UsageModel]](
    model: type[W],
    fields: Mapping[str, object] | Callable[[], Mapping[str, object]],
) -> W:
    """Validate the fields, by Tydom name only, with the target's write model."""
    try:
        values = fields if isinstance(fields, Mapping) else fields()
        return model.model_validate(values, by_name=False)
    except InvalidWrite as error:
        # the library message names fields, never values
        raise _Refused(NotSent("invalid_write", detail=str(error))) from None


type _Prepared = tuple[ClientRequest[Ok], UsageWrite[UsageModel]]
"""The request to send and the write it carries."""


def _prepare(
    installation: Installation,
    config: Config,
    target: str,
    fields: Mapping[str, object] | Callable[[], Mapping[str, object]],
    policy: Policy,
) -> _Prepared:
    """Check steps 2 to 4: the target allowed by the policy, its ref, its write."""
    kind, ids = _parse(target)
    usage = _usage(config, kind, ids, policy)
    if kind == "a":
        area = _ref(lambda: installation.area(ids[0]))
        # AreaWrite directly: ty refuses WRITES[Area] here.
        write = _validated(AreaWrite, fields)
        return WriteArea(area, write), write
    if kind == "g":
        group = _ref(lambda: installation.group(ids[0], usage))
        write = _validated(WRITES[usage], fields)
        return WriteGroup(group, write), write
    endpoint = _ref(lambda: installation.endpoint(ids[0], ids[1], usage))
    write = _validated(WRITES[usage], fields)
    return WriteEndpoint(endpoint, write), write


class Writer:
    """Writes over a cache and a send; one send per `1 / rate` s."""

    def __init__(
        self,
        cache: Cache,
        send: Callable[[ClientRequest[Ok]], Awaitable[Ok]],
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Write through `send`, at most `rate` per second by the monotonic clock.

        `now` dates `sent_at`.
        """
        self._cache = cache
        self._send = send
        self._interval = 1 / rate
        self._clock = clock
        self._now = now
        self._last_send: float | None = None

    async def write(
        self,
        target: str,
        fields: Mapping[str, object] | Callable[[], Mapping[str, object]],
        policy: Policy,
        label: str,
    ) -> Sent | NotSent:
        """Check, rate-limit and send one write; `label` names the caller in logs.

        `fields` by Tydom name, or a callable giving them when the write model takes
        them, which may raise `InvalidWrite`.
        """
        on = _ON.get(target[:1], "on a target")
        # no await from the freshness check to the send: two calls cannot both pass
        try:
            request, write = _prepare(*self._synced(), target, fields, policy)
            self._count()
        except _Refused as refused:
            _logger.info("%s %s: %s", label, on, refused.not_sent.reason)
            return refused.not_sent
        return await self._send_once(label, on, request, write)

    def _synced(self) -> tuple[Installation, Config]:
        """Step 1: a fresh cache, with its installation and config."""
        installation = self._cache.installation
        config = self._cache.config
        if not self._cache.fresh or installation is None or config is None:
            raise _Refused(NotSent("not_synced"))
        return installation, config

    def _count(self) -> None:
        """Step 5: no send within `1 / rate` s, then count this one."""
        now = self._clock()
        if self._last_send is not None:
            wait = self._interval - (now - self._last_send)
            if wait > 0:
                # rounded up to the millisecond, never above the interval
                retry = min(math.ceil(wait * 1000) / 1000, self._interval)
                raise _Refused(NotSent("rate_limited", retry_after_s=retry))
        self._last_send = now

    async def _send_once(
        self,
        label: str,
        on: str,
        request: ClientRequest[Ok],
        write: UsageWrite[UsageModel],
    ) -> Sent:
        """Step 6: send the counted request once and give the outcome."""
        fields = {str(item["name"]): item["value"] for item in write.body()}
        sent_at = self._now()
        outcome: Literal["acknowledged", "unknown"] = "acknowledged"
        try:
            await self._send(request)
        except CommunicationError:
            outcome = "unknown"
        except asyncio.CancelledError:
            # the write may be on the wire; it is never resent
            _logger.info("%s: write cancelled, outcome unknown", label)
            raise
        _logger.info("%s %s: %s", label, on, outcome)
        return Sent(outcome, fields, sent_at)
