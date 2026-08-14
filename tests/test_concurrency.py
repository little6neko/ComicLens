from __future__ import annotations

import asyncio

import pytest

from app.translation.concurrency import DynamicConcurrencyLimiter


@pytest.mark.asyncio
async def test_limiter_grants_waiters_in_fifo_order() -> None:
    limiter = DynamicConcurrencyLimiter(1)
    entered: list[int] = []
    started = [asyncio.Event() for _ in range(3)]
    release = [asyncio.Event() for _ in range(3)]

    async def worker(index: int) -> None:
        async with limiter.slot():
            entered.append(index)
            started[index].set()
            await release[index].wait()

    tasks = [asyncio.create_task(worker(index)) for index in range(3)]
    await asyncio.wait_for(started[0].wait(), timeout=1)

    assert entered == [0]
    assert limiter.active == 1
    assert limiter.waiting == 2

    release[0].set()
    await asyncio.wait_for(started[1].wait(), timeout=1)
    assert entered == [0, 1]

    release[1].set()
    await asyncio.wait_for(started[2].wait(), timeout=1)
    assert entered == [0, 1, 2]

    release[2].set()
    await asyncio.gather(*tasks)
    assert limiter.active == 0
    assert limiter.waiting == 0


@pytest.mark.asyncio
async def test_limiter_increase_immediately_fills_new_slots() -> None:
    limiter = DynamicConcurrencyLimiter(1)
    entered = [asyncio.Event() for _ in range(3)]
    release = asyncio.Event()

    async def worker(index: int) -> None:
        async with limiter.slot():
            entered[index].set()
            await release.wait()

    tasks = [asyncio.create_task(worker(index)) for index in range(3)]
    await asyncio.wait_for(entered[0].wait(), timeout=1)
    assert not entered[1].is_set()
    assert not entered[2].is_set()

    limiter.resize(3)
    await asyncio.wait_for(
        asyncio.gather(entered[1].wait(), entered[2].wait()),
        timeout=1,
    )
    assert limiter.active == 3

    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_limiter_decrease_does_not_cancel_active_permits() -> None:
    limiter = DynamicConcurrencyLimiter(3)
    entered = [asyncio.Event() for _ in range(4)]
    release = [asyncio.Event() for _ in range(4)]

    async def worker(index: int) -> None:
        async with limiter.slot():
            entered[index].set()
            await release[index].wait()

    tasks = [asyncio.create_task(worker(index)) for index in range(4)]
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in entered[:3])),
        timeout=1,
    )
    limiter.resize(1)

    release[0].set()
    release[1].set()
    await asyncio.sleep(0)
    assert not entered[3].is_set()
    assert limiter.active == 1

    release[2].set()
    await asyncio.wait_for(entered[3].wait(), timeout=1)
    assert limiter.active == 1

    release[3].set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_a_permit() -> None:
    limiter = DynamicConcurrencyLimiter(1)
    first_release = asyncio.Event()
    third_entered = asyncio.Event()

    async def first() -> None:
        async with limiter.slot():
            await first_release.wait()

    async def waiting() -> None:
        async with limiter.slot():
            raise AssertionError("cancelled waiter entered the limiter")

    async def third() -> None:
        async with limiter.slot():
            third_entered.set()

    first_task = asyncio.create_task(first())
    await asyncio.sleep(0)
    cancelled_task = asyncio.create_task(waiting())
    third_task = asyncio.create_task(third())
    await asyncio.sleep(0)

    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task

    first_release.set()
    await asyncio.wait_for(third_entered.wait(), timeout=1)
    await asyncio.gather(first_task, third_task)
    assert limiter.active == 0
    assert limiter.waiting == 0
