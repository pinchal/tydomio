"""CLI tool implementation, mainly for testing purpose."""

import argparse
import asyncio
import logging
import os
import sys
from pprint import pformat

from tydomio.client import AsyncTydomClient
from tydomio.requests import (
    GetConfigFile,
    RefreshAll,
    TydomPutAreasDataRequest,
    TydomPutDevicesDataRequest,
)


def main() -> int:
    """Entry point for the CLI application.

    This function parses command-line arguments, configures logging, and
    executes the specified action.

    Returns:
        int: Exit code of the application. Returns 0 on successful execution.

    Command-line Arguments:
        action (str): The action to perform. Choices are:
            - "run": Executes the main application logic.
            - "get-tydom-password": Retrieves the Tydom password.
        -v, --verbose (bool): Enables verbose logging if specified.
        --tydom-ip (str): The IP address for direct access. If not provided,
            remote access will be used, and the Tydom MAC address will be required.
            Defaults to the value of the environment variable `TYDOM_IP`.
        --tydom-password (str): The Tydom password. This is not the account
            password. Use the "get-tydom-password" action to retrieve it.
            Defaults to the value of the environment variable `TYDOM_PASSWORD`.
        --tydom-mac (str): The Tydom MAC address, required only for remote access.
            Defaults to the value of the environment variable `TYDOM_MAC_ADDRESS`.

    """
    parser = argparse.ArgumentParser()

    parser.add_argument("action", choices=["run", "get-tydom-password"])

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logs",
    )

    parser.add_argument(
        "--debug-asyncio",
        action="store_true",
        help="Enable asyncio debug mode",
    )

    parser.add_argument(
        "--tydom-ip",
        type=str,
        default=os.environ.get("TYDOM_IP", None),
        help="IP for direct access, without the remote access will "
        "be used instead and the Tydom MAC address will be required",
    )
    parser.add_argument(
        "--tydom-password",
        type=str,
        default=os.environ.get("TYDOM_PASSWORD", None),
        help="The Tydom password. This is not your accout password, "
        "use the action 'get-tydom-password' to retrieve it",
    )
    parser.add_argument(
        "--tydom-mac",
        type=str,
        default=os.environ.get("TYDOM_MAC_ADDRESS", None),
        help="The Tydom MAC address, only required on remote access",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if args.debug_asyncio:
        os.environ["PYTHONASYNCIODEBUG"] = "1"

    if args.action == "run":
        return _do_run(args)

    return 0


async def _poll_config_then_refresh_all(client: AsyncTydomClient) -> None:
    while True:
        response = await client.send(
            GetConfigFile(),
        )
        logging.info(pformat(response.config.__dict__))
        await client.send(RefreshAll())
        await asyncio.sleep(300)


async def _handle_put_device_data(put_devices: TydomPutDevicesDataRequest) -> None:
    logging.info("Received put devices data")
    logging.info(put_devices.data)


async def _handle_put_areas_data(put_areas: TydomPutAreasDataRequest) -> None:
    logging.info("Received put areas data")
    logging.info(put_areas.data)


def _do_run(args: argparse.Namespace) -> int:
    client = AsyncTydomClient(
        tydom_password=args.tydom_password,
        tydom_ip=args.tydom_ip,
        tydom_mac=args.tydom_mac,
        on_connection_routines=(_poll_config_then_refresh_all,),
        tydom_request_handlers=(
            _handle_put_device_data,
            _handle_put_areas_data,
        ),
    )

    try:
        asyncio.run(client.run_websocket())
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
