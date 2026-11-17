import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from tests.builders import config, writable_cache
from tests.conftest import FakeClock, FakeSend
from tydomio.error import CommunicationError, InvalidWrite
from tydomio.installation import Installation
from tydomio.model.usages import WRITES
from tydomio.model.usages.area import AreaWrite
from tydomio.model.usages.boiler import BoilerWrite
from tydomio.model.usages.shutter import ShutterWrite
from tydomio.requests import ClientRequest
from tydomio.response import Ok
from tydomio.state.cache import Cache
from tydomio.state.writer import (
    EVERYTHING,
    HEATING,
    HEATING_USAGES,
    SHUTTER,
    SHUTTER_USAGES,
    NotSent,
    Policy,
    Sent,
    Writer,
    write_model,
)

SHUTTER_ID = "e:10:1"
SHUTTER_GROUP = "g:5"
SENT_AT = datetime(2026, 11, 3, 8, 9, 10, tzinfo=UTC)
LABEL = "tool set_shutter"


@pytest.fixture(name="cache")
def synced_cache() -> Cache:
    return writable_cache()


def refused(outcome: Sent | NotSent) -> str:
    assert isinstance(outcome, NotSent)
    return outcome.reason


async def test_a_write_while_not_synced_is_not_sent(cache: Cache) -> None:
    cache.mark_disconnected()
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(
        SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL
    )

    assert refused(outcome) == "not_synced"
    assert send.requests == []


@pytest.mark.parametrize(
    ("target", "policy"),
    [
        ("e:10:2", SHUTTER),
        ("e:99:1", SHUTTER),
        ("g:99", SHUTTER),
        ("e:99:1", HEATING),
        ("a:99", HEATING),
        ("e:10", EVERYTHING),
        ("x:1", EVERYTHING),
        ("a:1:2", EVERYTHING),
        ("e:1:2/../x", EVERYTHING),
    ],
)
async def test_an_unknown_or_malformed_target_is_not_sent(
    cache: Cache, target: str, policy: Policy
) -> None:
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(
        target, {"positionCmd": "UP"}, policy, LABEL
    )

    assert refused(outcome) == "unknown_target"
    assert send.requests == []


async def test_a_write_after_drift_removed_the_target_is_not_sent(
    cache: Cache,
) -> None:
    cache.apply_config(config({(20, 1): ("Boiler", "boiler")}))
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(
        SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL
    )

    assert refused(outcome) == "unknown_target"
    assert send.requests == []


@pytest.mark.parametrize("target", [SHUTTER_ID, SHUTTER_GROUP])
async def test_a_target_the_installation_refuses_is_not_sent(
    cache: Cache, target: str
) -> None:
    # the installation checks again, apart from the config lookup
    cache.installation = Installation.from_config(config({}))
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(
        target, {"positionCmd": "UP"}, SHUTTER, LABEL
    )

    assert refused(outcome) == "unknown_target"
    assert send.requests == []


@pytest.mark.parametrize(
    ("target", "policy"),
    [
        ("e:20:1", SHUTTER),
        ("g:6", SHUTTER),
        ("e:10:1", HEATING),
        ("g:6", HEATING),
        # a group is refused for heating before the config lookup
        ("g:99", HEATING),
        ("a:8", HEATING),
        ("g:6", EVERYTHING),
        ("a:8", EVERYTHING),
    ],
)
async def test_a_target_the_policy_does_not_allow_is_not_sent(
    cache: Cache, target: str, policy: Policy
) -> None:
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(
        target, {"setpoint": 19.5}, policy, LABEL
    )

    assert refused(outcome) == "wrong_usage"
    assert send.requests == []


@pytest.mark.parametrize(
    ("target", "fields", "policy"),
    [
        (SHUTTER_ID, {"positionCmd": "DOWN"}, EVERYTHING),
        (SHUTTER_GROUP, {"position": 40}, EVERYTHING),
        ("e:20:1", {"localMode": "NORMAL"}, EVERYTHING),
        ("a:7", {"setpoint": 19.5}, EVERYTHING),
        ("e:30:1", {"selectedProg": 2}, HEATING),
    ],
)
async def test_a_target_the_policy_allows_is_sent_once(
    cache: Cache, target: str, fields: dict[str, object], policy: Policy
) -> None:
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0, now=lambda: SENT_AT).write(
        target, fields, policy, LABEL
    )

    assert outcome == Sent("acknowledged", fields, SENT_AT)
    assert len(send.requests) == 1


@pytest.mark.parametrize(
    ("target", "fields", "policy"),
    [
        (SHUTTER_ID, {"position": 101}, SHUTTER),
        (SHUTTER_ID, {"position": -1}, SHUTTER),
        (SHUTTER_ID, {"position": 50.0}, SHUTTER),
        (SHUTTER_ID, {"position": True}, SHUTTER),
        (SHUTTER_ID, {"position": None}, SHUTTER),
        (SHUTTER_ID, {"positionCmd": "zz-OPEN"}, SHUTTER),
        (SHUTTER_ID, {"positionCmd": 1}, SHUTTER),
        ("e:20:1", {"setpoint": 19.3}, HEATING),
        ("e:20:1", {"zz-field": 1}, HEATING),
        ("e:20:1", {}, HEATING),
        ("e:20:1", {"localMode": "zz-WARM"}, HEATING),
        # the Python name is not the Tydom name: only aliases are taken
        ("e:20:1", {"heat_setpoint": 19.5}, HEATING),
        ("e:20:1", {"selectedProg": 1}, HEATING),
        ("e:30:1", {"setpoint": 19.5}, HEATING),
        ("a:7", {"selectedProg": 1}, HEATING),
    ],
)
async def test_an_invalid_write_is_not_sent_and_echoes_nothing(
    cache: Cache, target: str, fields: dict[str, object], policy: Policy
) -> None:
    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(target, fields, policy, LABEL)

    assert refused(outcome) == "invalid_write"
    assert isinstance(outcome, NotSent)
    assert outcome.detail is not None
    assert "zz" not in outcome.detail
    assert send.requests == []


async def test_the_write_model_names_the_invalid_field(cache: Cache) -> None:
    outcome = await Writer(cache, FakeSend(), 1.0).write(
        SHUTTER_ID, {"position": 101}, SHUTTER, LABEL
    )

    assert isinstance(outcome, NotSent)
    assert outcome.detail is not None
    assert "position" in outcome.detail
    assert "101" not in outcome.detail


async def test_fields_given_late_are_refused_with_their_invalid_write(
    cache: Cache,
) -> None:
    def fields() -> Mapping[str, object]:
        raise InvalidWrite("give one field")

    send = FakeSend()

    outcome = await Writer(cache, send, 1.0).write(SHUTTER_ID, fields, SHUTTER, LABEL)

    assert outcome == NotSent("invalid_write", detail="give one field")
    assert send.requests == []


async def test_fields_given_late_are_taken_after_the_target_checks(
    cache: Cache,
) -> None:
    def fields() -> Mapping[str, object]:
        raise InvalidWrite("give one field")

    outcome = await Writer(cache, FakeSend(), 1.0).write(
        "e:20:1", fields, SHUTTER, LABEL
    )

    assert refused(outcome) == "wrong_usage"


@pytest.mark.parametrize("rate", [0.1, 1.0, 3.0, 10.0])
async def test_a_write_within_the_interval_is_rate_limited(
    cache: Cache, rate: float
) -> None:
    send = FakeSend()
    clock = FakeClock()
    writer = Writer(cache, send, rate, clock)
    await writer.write(SHUTTER_ID, {"positionCmd": "DOWN"}, SHUTTER, LABEL)
    clock.now += 0.5 / rate

    outcome = await writer.write(SHUTTER_ID, {"positionCmd": "STOP"}, SHUTTER, LABEL)

    assert refused(outcome) == "rate_limited"
    assert isinstance(outcome, NotSent)
    assert outcome.retry_after_s is not None
    assert 0 < outcome.retry_after_s <= 1 / rate
    assert len(send.requests) == 1


async def test_retry_after_s_never_exceeds_the_interval(cache: Cache) -> None:
    # 1 / 6 s rounds up to 0.167 at the millisecond
    writer = Writer(cache, FakeSend(), 6.0, FakeClock())
    await writer.write(SHUTTER_ID, {"positionCmd": "DOWN"}, SHUTTER, LABEL)

    outcome = await writer.write(SHUTTER_ID, {"positionCmd": "STOP"}, SHUTTER, LABEL)

    assert isinstance(outcome, NotSent)
    assert outcome.retry_after_s is not None
    assert outcome.retry_after_s <= 1 / 6


async def test_a_write_after_the_interval_is_sent(cache: Cache) -> None:
    send = FakeSend()
    clock = FakeClock()
    writer = Writer(cache, send, 2.0, clock)
    await writer.write(SHUTTER_ID, {"positionCmd": "DOWN"}, SHUTTER, LABEL)
    clock.now += 0.5

    outcome = await writer.write(SHUTTER_ID, {"positionCmd": "STOP"}, SHUTTER, LABEL)

    assert isinstance(outcome, Sent)
    assert outcome.outcome == "acknowledged"
    assert len(send.requests) == 2


async def test_a_refused_write_is_not_counted(cache: Cache) -> None:
    writer = Writer(cache, FakeSend(), 1.0, FakeClock())
    await writer.write(SHUTTER_ID, {"position": 101}, SHUTTER, LABEL)

    outcome = await writer.write(SHUTTER_ID, {"position": 0}, SHUTTER, LABEL)

    assert isinstance(outcome, Sent)


async def test_two_parallel_writes_send_one(cache: Cache) -> None:
    send = FakeSend()
    writer = Writer(cache, send, 1.0, FakeClock())

    outcomes = await asyncio.gather(
        writer.write(SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL),
        writer.write(SHUTTER_GROUP, {"positionCmd": "DOWN"}, SHUTTER, LABEL),
    )

    assert sorted(type(o).__name__ for o in outcomes) == ["NotSent", "Sent"]
    assert len(send.requests) == 1


async def test_a_communication_error_is_unknown_sent_once_and_counted(
    cache: Cache,
) -> None:
    sent: list[ClientRequest[Ok]] = []

    async def fail(request: ClientRequest[Ok]) -> Ok:
        sent.append(request)
        raise CommunicationError("Request timeout")

    writer = Writer(cache, fail, 1.0, FakeClock(), now=lambda: SENT_AT)
    first = await writer.write(SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL)

    second = await writer.write(SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL)

    assert first == Sent("unknown", {"positionCmd": "UP"}, SENT_AT)
    assert refused(second) == "rate_limited"
    assert len(sent) == 1


async def test_cancelled_during_the_send_is_sent_once_counted_and_logged(
    cache: Cache, caplog: pytest.LogCaptureFixture
) -> None:
    send = FakeSend(gate=asyncio.Event())
    writer = Writer(cache, send, 1.0, FakeClock())
    call = asyncio.create_task(
        writer.write(SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL)
    )
    while not send.requests:
        await asyncio.sleep(0)

    with caplog.at_level("INFO", logger="tydomio.state.writer"):
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    assert [r.getMessage() for r in caplog.records] == [
        "tool set_shutter: write cancelled, outcome unknown"
    ]
    later = await writer.write(SHUTTER_ID, {"positionCmd": "UP"}, SHUTTER, LABEL)
    assert refused(later) == "rate_limited"
    assert len(send.requests) == 1


@pytest.mark.parametrize(
    ("target", "policy", "model"),
    [
        (SHUTTER_ID, SHUTTER, ShutterWrite),
        (SHUTTER_GROUP, EVERYTHING, ShutterWrite),
        ("e:20:1", EVERYTHING, BoilerWrite),
        ("a:7", HEATING, AreaWrite),
        ("e:20:1", SHUTTER, None),
        ("g:6", EVERYTHING, None),
        ("a:8", EVERYTHING, None),
        ("e:99:1", EVERYTHING, None),
        ("zz", EVERYTHING, None),
    ],
)
def test_write_model_is_the_one_a_target_accepts_under_a_policy(
    cache: Cache, target: str, policy: Policy, model: type | None
) -> None:
    assert cache.config is not None

    assert write_model(cache.config, target, policy) is model


def test_every_writes_key_is_classified() -> None:
    assert SHUTTER_USAGES | HEATING_USAGES == set(WRITES)
    assert not SHUTTER_USAGES & HEATING_USAGES
