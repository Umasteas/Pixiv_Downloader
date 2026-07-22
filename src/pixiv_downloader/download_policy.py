"""下载任务的并发与重试配置契约。"""
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DownloadPolicy:
    """已校验、已归一化且可安全用于单个事件循环的下载策略。"""

    max_concurrent: int = 5
    retry_times: int = 3
    retry_delays: tuple[float, ...] = (1.0, 3.0, 5.0)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "DownloadPolicy":
        values = values or {}
        max_concurrent = values.get("max_concurrent", 5)
        retry_times = values.get("retry_times", 3)
        raw_delays = values.get("retry_delays", [1, 3, 5])

        if type(max_concurrent) is not int or max_concurrent < 1:
            raise ValueError("download.max_concurrent 必须是大于等于 1 的整数")
        if type(retry_times) is not int or retry_times < 0:
            raise ValueError("download.retry_times 必须是大于等于 0 的整数")
        if not isinstance(raw_delays, (list, tuple)):
            raise ValueError("download.retry_delays 必须是数值列表")
        if retry_times == 0:
            return cls(max_concurrent=max_concurrent, retry_times=0, retry_delays=())
        if not raw_delays:
            raise ValueError("启用重试时 download.retry_delays 不能为空")
        if any(type(delay) not in (int, float) or delay < 0 for delay in raw_delays):
            raise ValueError("download.retry_delays 只能包含非负数值")

        delays = tuple(float(delay) for delay in raw_delays[:retry_times])
        if len(delays) < retry_times:
            delays += (delays[-1],) * (retry_times - len(delays))
        return cls(
            max_concurrent=max_concurrent,
            retry_times=retry_times,
            retry_delays=delays,
        )
