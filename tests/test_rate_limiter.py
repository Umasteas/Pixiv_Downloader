"""Issue #9 自适应并发限制器。"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_adaptive_limiter_never_exceeds_configured_maximum():
    from src.pixiv_downloader.rate_limiter import AdaptiveLimiter

    limiter = AdaptiveLimiter(2)
    release = asyncio.Event()
    entered = 0
    all_started = asyncio.Event()

    async def worker():
        nonlocal entered
        async with limiter:
            entered += 1
            if entered == 2:
                all_started.set()
            await release.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(5)]
    await asyncio.wait_for(all_started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert limiter.active_count == 2
    assert limiter.max_observed == 2
    release.set()
    await asyncio.gather(*tasks)
    assert limiter.active_count == 0


@pytest.mark.asyncio
async def test_adaptive_limiter_halves_effective_limit_to_floor_one():
    from src.pixiv_downloader.rate_limiter import AdaptiveLimiter

    limiter = AdaptiveLimiter(5)

    assert await limiter.reduce_limit() == 2
    assert limiter.current_limit == 2
    assert await limiter.reduce_limit() == 1
    assert limiter.current_limit == 1
    assert await limiter.reduce_limit() == 1


@pytest.mark.asyncio
async def test_reduced_limit_blocks_new_entries_until_active_count_drops():
    from src.pixiv_downloader.rate_limiter import AdaptiveLimiter

    limiter = AdaptiveLimiter(3)
    first_release = asyncio.Event()
    entered = []

    async def worker(number):
        async with limiter:
            entered.append(number)
            if number in (1, 2, 3):
                await first_release.wait()

    first = [asyncio.create_task(worker(number)) for number in (1, 2, 3)]
    while limiter.active_count < 3:
        await asyncio.sleep(0)
    await limiter.reduce_limit()
    fourth = asyncio.create_task(worker(4))
    await asyncio.sleep(0)

    assert limiter.current_limit == 1
    assert 4 not in entered
    first_release.set()
    await asyncio.gather(*first, fourth)
    assert entered[-1] == 4
