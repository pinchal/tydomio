from tydomio.pending_requests import PendingRequests
from tydomio.response import Response


def response_to(transaction_id: int) -> Response:
    return Response(
        status=200, headers={"transac-id": str(transaction_id)}, content=None
    )


async def test_register_makes_request_pending() -> None:
    pending = PendingRequests()

    pending.register(1)

    assert 1 in pending


async def test_resolve_gives_response_to_its_request() -> None:
    pending = PendingRequests()
    future = pending.register(1)
    response = response_to(1)

    pending.resolve(response)

    assert future.result() is response


async def test_resolve_reports_expected_response() -> None:
    pending = PendingRequests()
    pending.register(1)

    assert pending.resolve(response_to(1))


async def test_resolved_request_is_no_longer_pending() -> None:
    pending = PendingRequests()
    pending.register(1)

    pending.resolve(response_to(1))

    assert 1 not in pending


async def test_resolve_reports_unexpected_response() -> None:
    pending = PendingRequests()
    pending.register(1)

    assert not pending.resolve(response_to(2))


async def test_unexpected_response_resolves_nothing() -> None:
    pending = PendingRequests()
    future = pending.register(1)

    pending.resolve(response_to(2))

    assert not future.done()


async def test_abandoned_request_is_no_longer_pending() -> None:
    pending = PendingRequests()
    future = pending.register(1)

    pending.abandon(1, future)

    assert 1 not in pending


async def test_len_counts_pending_requests() -> None:
    pending = PendingRequests()
    pending.register(1)
    pending.register(2)

    assert len(pending) == 2


async def test_flush_ignores_cancelled_request() -> None:
    pending = PendingRequests()
    pending.register(1).cancel()

    pending.flush(RuntimeError())

    assert 1 not in pending


async def test_abandon_ignores_request_no_longer_pending() -> None:
    pending = PendingRequests()
    future = pending.register(1)
    pending.resolve(response_to(1))

    pending.abandon(1, future)

    assert 1 not in pending


async def test_abandon_keeps_new_request_with_same_transaction_id() -> None:
    pending = PendingRequests()
    first = pending.register(1)
    pending.flush(RuntimeError())
    pending.register(1)

    pending.abandon(1, first)

    assert 1 in pending


async def test_resolve_reports_cancelled_request_as_not_waiting() -> None:
    pending = PendingRequests()
    pending.register(1).cancel()

    assert not pending.resolve(response_to(1))


async def test_resolve_forgets_cancelled_request() -> None:
    pending = PendingRequests()
    pending.register(1).cancel()

    pending.resolve(response_to(1))

    assert 1 not in pending


async def test_flush_fails_waiting_request() -> None:
    pending = PendingRequests()
    future = pending.register(1)
    error = RuntimeError()

    pending.flush(error)

    assert future.exception() is error
