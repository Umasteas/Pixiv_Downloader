"""下载错误的稳定分类与脱敏用户消息。"""
import asyncio
import errno
from dataclasses import dataclass
from enum import Enum
from typing import Any

import aiohttp


class ErrorCategory(str, Enum):
    RECOVERABLE = "recoverable"
    SKIPPED = "skipped"
    FATAL = "fatal"


class RecoverableDownloadError(RuntimeError):
    """重试预算耗尽后可记录并跳过的网络错误。"""


class SkippableDownloadError(RuntimeError):
    """单个作品不可下载，但任务可继续。"""


class FatalDownloadError(RuntimeError):
    """任务无法安全继续。"""


@dataclass(frozen=True)
class ClassifiedError:
    category: ErrorCategory
    message: str


def _http_status(error: BaseException) -> int | None:
    status: Any = getattr(error, "status", None)
    return status if type(status) is int else None


def classify_error(error: BaseException) -> ClassifiedError:
    """把任意异常映射为有限类别和不含原始异常文本的安全消息。"""
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return ClassifiedError(ErrorCategory.FATAL, "磁盘空间不足。")
    if isinstance(error, FatalDownloadError):
        message = str(error)
        if message in {"磁盘空间不足。", "Pixiv 登录状态失效。"}:
            return ClassifiedError(ErrorCategory.FATAL, message)
        return ClassifiedError(ErrorCategory.FATAL, "任务遇到致命错误，已停止。")
    if error.__class__.__name__ == "AuthError":
        return ClassifiedError(ErrorCategory.FATAL, "登录或本地存储状态异常，任务已停止。")
    if isinstance(error, RecoverableDownloadError):
        return ClassifiedError(ErrorCategory.RECOVERABLE, "网络错误重试耗尽。")
    if isinstance(error, SkippableDownloadError):
        return ClassifiedError(ErrorCategory.SKIPPED, "作品下载失败，已跳过。")

    status = _http_status(error)
    if status in {401, 403}:
        return ClassifiedError(ErrorCategory.FATAL, "Pixiv 登录状态失效。")
    if status == 404:
        return ClassifiedError(ErrorCategory.SKIPPED, "作品或文件不存在。")
    if status is not None and 500 <= status <= 599:
        return ClassifiedError(ErrorCategory.RECOVERABLE, "Pixiv 服务暂时不可用。")
    if isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError)):
        return ClassifiedError(ErrorCategory.RECOVERABLE, "网络请求失败。")
    if isinstance(error, OSError) and error.errno in {errno.EINVAL, errno.ENAMETOOLONG}:
        return ClassifiedError(ErrorCategory.SKIPPED, "文件名或路径无效。")
    return ClassifiedError(ErrorCategory.SKIPPED, "作品下载失败，已跳过。")
