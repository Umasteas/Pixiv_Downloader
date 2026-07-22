"""任务内自适应异步并发限制器。"""
import asyncio


class AdaptiveLimiter:
    """以 Semaphore 约束绝对上限，并允许 429 降低新请求进入阈值。"""

    def __init__(self, max_concurrent: int) -> None:
        if type(max_concurrent) is not int or max_concurrent < 1:
            raise ValueError("max_concurrent 必须是大于等于 1 的整数")
        self._maximum = max_concurrent
        self._current_limit = max_concurrent
        self._active_count = 0
        self._max_observed = 0
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._condition = asyncio.Condition()

    @property
    def current_limit(self) -> int:
        return self._current_limit

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def max_observed(self) -> int:
        return self._max_observed

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        try:
            async with self._condition:
                await self._condition.wait_for(
                    lambda: self._active_count < self._current_limit
                )
                self._active_count += 1
                self._max_observed = max(self._max_observed, self._active_count)
        except BaseException:
            self._semaphore.release()
            raise

    async def release(self) -> None:
        async with self._condition:
            if self._active_count < 1:
                raise RuntimeError("释放了未持有的并发槽位")
            self._active_count -= 1
            self._condition.notify_all()
        self._semaphore.release()

    async def reduce_limit(self) -> int:
        async with self._condition:
            self._current_limit = max(1, self._current_limit // 2)
            self._condition.notify_all()
            return self._current_limit

    async def __aenter__(self) -> "AdaptiveLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        await self.release()
        return False
