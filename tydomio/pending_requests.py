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
        """Give the response to the request waiting for it, False if none waits.

        A request whose future is already done, i.e. cancelled with its waiting
        task, waits no more: it is forgotten and the response dropped.
        """
        future = self._futures.pop(response.transac_id, None)
        if future is None or future.done():
            return False
        future.set_result(response)
        return True

    def abandon(self, transaction_id: int, future: asyncio.Future[Response]) -> None:
        """Stop waiting for the response to this request, if it still waits.

        The future identifies the request: once resolved or flushed, its
        transaction id may have been registered again by another request, which
        is left pending.
        """
        if self._futures.get(transaction_id) is future:
            del self._futures[transaction_id]

    def flush(self, error: Exception) -> None:
        """Fail every pending request with the error, then forget them.

        A future already done, i.e. cancelled with its waiting task, is left as is.
        """
        for future in self._futures.values():
            if not future.done():
                future.set_exception(error)
        self._futures.clear()
