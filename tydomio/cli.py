"""CLI tool implementation, mainly for testing purpose."""

import argparse
import asyncio
import logging
import os

from tydomio.client import AsyncTydomClient


def _main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument("action", choices=["run", "get-tydom-password"])

    parser.add_argument("-v", "--verbose", action="store_true")

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
    logging.getLogger("websockets").setLevel(logging.DEBUG)

    if args.action == "run":
        return _do_run(args)

    return 0


def _do_run(args: argparse.Namespace) -> int:
    client = AsyncTydomClient(
        tydom_password=args.tydom_password,
        tydom_ip=args.tydom_ip,
        tydom_mac=args.tydom_mac,
    )

    try:
        asyncio.run(client.run_websocket())
    except KeyboardInterrupt:
        pass

    return 0
