"""State management for Tydom devices."""

import asyncio
import itertools
import logging
import pprint

from .client import AsyncTydomClient
from .model import config
from .requests import (
    GetConfigFile,
    RefreshAll,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)

STATE_LOGGER = logging.getLogger("tydomio.state")

Stuff = config.Area | config.Endpoint | config.Group | config.Scenario | config.Moment


class TydomState:
    """Class to manage the state of the Tydom device."""

    def __init__(self) -> None:
        """Initialize the Tydom state management."""
        self._unformated_stuff: dict[int, Stuff] = {}
        self._config: config.Config | None = None
        self._endpoints: dict[
            int, tuple[config.Endpoint, dict[str, str | float | None]]
        ] = {}

    async def on_connection(self, client: AsyncTydomClient) -> None:
        """Start the tasks to update the state and display it."""
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(self._update_config(client))
            task_group.create_task(self._display())

    async def _update_config(self, client: AsyncTydomClient) -> None:
        for i in itertools.count():
            response = await client.send(GetConfigFile())

            if hash(response.config) != hash(self._config):
                STATE_LOGGER.info("Configuration has changed, updating state.")

                self._unformated_stuff = {
                    item.id: item
                    for item in response.config.areas
                    + response.config.groups
                    + response.config.scenarios
                    + response.config.moments
                }
                self._endpoints = {
                    item.id_endpoint: (item, {}) for item in response.config.endpoints
                }
                self._config = response.config

            # On first iteration, we need to refresh all values
            if i == 0:
                await client.send(RefreshAll())

            # await asyncio.sleep(60)  # Sleep for 5 minutes
            return

    async def _display(self) -> None:
        while True:
            print("\n\n")
            pprint.pprint(
                {config.name: data for config, data in self._endpoints.values()},
                sort_dicts=True,
                indent=4,
            )
            print("\n\n")
            await asyncio.sleep(5)

    async def handle_put_devices_data(
        self, request: TydomPutDevicesDataRequest
    ) -> None:
        """Handle the put devices data event."""
        for device in request.data.root:
            if device.id in self._endpoints:
                for endpoint_data in device.endpoints:
                    self._endpoints[device.id][1].update(
                        {data.name: data.value for data in endpoint_data.data}
                    )
            elif device.id in self._unformated_stuff:
                STATE_LOGGER.info(
                    "Device id #%s not found in endpoints, but in unformatted stuff.",
                    device.id,
                )
                STATE_LOGGER.info(self._unformated_stuff[device.id])
                STATE_LOGGER.info(device.endpoints)

    async def handle_put_areas_data(self, request: TydomPutAreasDataRequest) -> None:
        """Handle the put areas data event."""
        for endpoints_data in request.data.root:
            if endpoints_data.id in self._endpoints:
                self._endpoints[endpoints_data.id][1].update(
                    {data.name: data.value for data in endpoints_data.data}
                )
            elif endpoints_data.id in self._unformated_stuff:
                STATE_LOGGER.info(
                    "Area id #%s not found in endpoints, but in unformatted stuff.",
                    endpoints_data.id,
                )
                STATE_LOGGER.info(self._unformated_stuff[endpoints_data.id])
                STATE_LOGGER.info(endpoints_data.data)
