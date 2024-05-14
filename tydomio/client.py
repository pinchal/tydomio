"""Client implementation."""

import asyncio.exceptions
import logging
import ssl

import httpx
import websockets.auth


class AsyncTydomClient:
    """Asynchronous client for Tydom.

    The authentication process is heavily inspired by tydom2mqtt
    - https://github.com/fmartinou/tydom2mqtt


    """

    def __init__(
        self,
        tydom_password: str,
        tydom_ip: str | None,
        tydom_mac: str,
        deltadore_remote: str = "mediation.tydom.com",
    ):
        """AsyncTydomClient constructor.

        :param tydom_password: Tydom password,
        It can be retrieved using 'tydomio get-tydom-password'
        :param tydom_ip: The local access to your Tydom (optional)
        :param tydom_mac: The mac address of your Tydom
        :param deltadore_remote: hostname for remote access, used when no ip is provided
        """
        self.logger = logging.getLogger("tydomio.client")
        self._tydom_password = tydom_password
        self._tydom_ip = tydom_ip
        self._tydom_mac = tydom_mac

        self._local_access = self._tydom_ip is not None
        self._tydom_target = tydom_ip if self._local_access else deltadore_remote
        self._cmd_prefix = "\x02" if self._local_access else ""
        self._ssl_context = ssl._create_unverified_context()
        if self._local_access:
            self._ssl_context.options |= 0x4

    async def _get_authorization(self) -> str:
        self.logger.info(
            "Get authorization from Tydom #%s on %s",
            self._tydom_mac,
            self._tydom_target,
        )

        auth = httpx.DigestAuth(username=self._tydom_mac, password=self._tydom_password)

        async with httpx.AsyncClient(verify=self._ssl_context) as client:
            # build the get request on the endpoint
            request = client.build_request(
                method="GET",
                url=f"https://{self._tydom_target}/mediation/client?mac={self._tydom_mac}",
            )

            # initiate auth flow
            auth_flow = auth.auth_flow(request)

            # send the first request to get the challenge
            response = await client.send(next(auth_flow))
            # forward the rejected response to the auth flow to parse the challenge
            request = auth_flow.send(response)

            # do not perform the authentication at this point,
            # instead, keep the authorization for the websocket and drop the connection
            authorization = request.headers["authorization"]
            assert isinstance(authorization, str)
            return authorization

    async def run_websocket(self) -> None:
        """Authenticate initiate the connection on the Tydom websocket."""
        wait_between_retry = 5

        while True:
            # try to get authorization, retry every 5 seconds on failure
            try:
                authorization = await self._get_authorization()

                # open websocket
                async with websockets.connect(
                    uri=f"wss://{self._tydom_target}:443"
                    f"/mediation/client?mac={self._tydom_mac}&appli=1",
                    extra_headers={"Authorization": authorization},
                    ssl=self._ssl_context,
                    ping_timeout=None,
                ) as websocket:
                    self.logger.info("Connected to tydom")
                    try:
                        async for message in websocket:
                            self.logger.info("NEW MESSAGE: %s", message)

                    except websockets.ConnectionClosed:
                        self.logger.exception("Connection lost")

                    except asyncio.exceptions.CancelledError:
                        self.logger.info("Exiting...")
                        return

            except httpx.ConnectError:
                # wait and retry
                self.logger.warning("Unable to reach %s", self._tydom_target)
                await asyncio.sleep(wait_between_retry)
                continue

            except websockets.exceptions.InvalidStatusCode:
                # this is probably due to a 401, break
                self.logger.error("Invalid authentication")
                break
