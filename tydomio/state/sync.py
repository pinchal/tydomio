"""Keeps the cache in sync: snapshot and refresh on each connection, then pushes."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final

from ..client import AsyncTydomClient
from ..error import CommunicationError
from ..model.config import Config
from ..requests import (
    GetAreasData,
    GetConfigFile,
    GetDevicesData,
    RefreshAll,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)
from .cache import Cache, CacheError

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = 10  # seconds, per GET, as capture uses on the real Tydom
BACKOFF: Final = (10, 20, 40, 80, 160, 300)  # seconds, the last one repeated


class Sync:
    """The connection routine and the push handlers that keep a cache current."""

    def __init__(
        self,
        cache: Cache,
        sleep: Callable[[float], Awaitable[None]],
        now: Callable[[], datetime],
    ) -> None:
        """Keep the cache, waiting with the sleep of the client between failures."""
        self._cache = cache
        self._sleep = sleep
        self._now = now
        self._config_date: int | None = None

    async def routine(self, client: AsyncTydomClient) -> None:
        """Bootstrap the cache, backing off on failure, then wait until cancelled."""
        failures = 0
        try:
            while not await self._bootstrap(client):
                await self._sleep(BACKOFF[min(failures, len(BACKOFF) - 1)])
                failures += 1
            # the client's TaskGroup cancels the routine when the connection ends
            await asyncio.get_running_loop().create_future()
        finally:
            self._cache.mark_disconnected()

    async def _bootstrap(self, client: AsyncTydomClient) -> bool:
        """Apply the config and the snapshots; False on a communication error."""
        self._cache.mark_connected()
        config_request = GetConfigFile()
        devices_request = GetDevicesData()
        areas_request = GetAreasData()
        step = config_request.url
        # caught here: escaping the routine, it would make the client reconnect at once
        try:
            self._apply_config(
                (await client.send(config_request, REQUEST_TIMEOUT)).config
            )
            step = devices_request.url
            # no await between send returning and the replace: pushes stay ordered
            devices = await client.send(devices_request, REQUEST_TIMEOUT)
            self._cache.replace_devices(devices.data, devices.invalid)
            step = areas_request.url
            area_data = await client.send(areas_request, REQUEST_TIMEOUT)
            self._cache.replace_areas(area_data.data, area_data.invalid)
        except CommunicationError as error:
            _logger.warning("Sync failed at %s: %s", step, type(error).__name__)
            return False
        self._cache.mark_synced(self._now())
        await self._refresh(client)
        return True

    @staticmethod
    async def _refresh(client: AsyncTydomClient) -> None:
        """Start the conso pushes, which last as long as this connection does."""
        try:
            await client.send(RefreshAll(), REQUEST_TIMEOUT)
        except CommunicationError as error:
            # the snapshot stands: only the conso values go stale, so no retry
            _logger.warning("Refresh failed: %s", type(error).__name__)

    def _apply_config(self, config: Config) -> None:
        if self._config_date is not None and config.date != self._config_date:
            _logger.info("Config changed")
        self._config_date = config.date
        self._cache.apply_config(config)

    async def on_devices_push(self, request: TydomPutDevicesDataRequest) -> None:
        """Merge a devices push, with no await so it is atomic and ordered."""
        try:
            self._cache.merge_devices(request.data)
        except Exception:
            # not a RuntimeError, so the client stops rather than go on half-merged
            raise CacheError("Merging a devices push failed") from None

    async def on_areas_push(self, request: TydomPutAreasDataRequest) -> None:
        """Merge an areas push, with no await so it is atomic and ordered."""
        try:
            self._cache.merge_areas(request.data)
        except Exception:
            # not a RuntimeError, so the client stops rather than go on half-merged
            raise CacheError("Merging an areas push failed") from None
