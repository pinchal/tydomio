"""Correlation of the responses from the Tydom with the requests waiting for them."""

import asyncio

from .response import Response


class PendingRequests:
    """Futures of the requests sent and not answered yet, by transaction id."""

    def __init__(self) -> None:
        """Start with no request pending."""
        self._futures: dict[int, asyncio.Future[Response]] = {}

    def __contains__(self, transaction_id: int) -> bool:
        """Whether a request with this transaction id is pending."""
        return transaction_id in self._futures

    def __len__(self) -> int:
        """Count the pending requests."""
        return len(self._futures)

    def register(self, transaction_id: int) -> asyncio.Future[Response]:
        """Wait for the response with this transaction id."""
        future: asyncio.Future[Response] = asyncio.Future()
        self._futures[transaction_id] = future
        return future

    def resolve(self, response: Response) -> bool:
        """Give the response to the request waiting for it, False if none waits."""
        transac_id = response.transac_id
        if transac_id not in self._futures:
            return False
        self._futures[transac_id].set_result(response)
        del self._futures[transac_id]
        return True

    def abandon(self, transaction_id: int) -> None:
        """Stop waiting for the response to this request."""
        del self._futures[transaction_id]

    def flush(self) -> None:
        """Forget every pending request.

        The exceptions of the futures are retrieved and ignored, avoiding asyncio
        error logs.
        """
        for future in self._futures.values():
            future.exception()
        self._futures.clear()
