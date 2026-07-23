"""应用日志文件、轮转策略与实时日志事件适配器。"""
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Mapping


_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class _RealtimeLogHandler(logging.Handler):
    def __init__(self, publish: Callable[[dict[str, str]], None]) -> None:
        super().__init__(logging.INFO)
        self.publish = publish
        self._pixiv_downloader_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.publish({
                "timestamp": datetime.fromtimestamp(
                    record.created,
                    tz=timezone.utc,
                ).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
            })
        except Exception:
            return


class _PackageLogFilter(logging.Filter):
    """排除 Werkzeug/Socket.IO 访问日志，只保留应用包日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "pixiv_downloader" or record.name.startswith(
            ("pixiv_downloader.", "src.pixiv_downloader.")
        )


def configure_logging(
    logs_dir: Path | str,
    settings: Mapping[str, Any] | None = None,
    *,
    publish: Callable[[dict[str, str]], None] | None = None,
    logger: logging.Logger | None = None,
) -> logging.Logger:
    """幂等配置按天轮转日志；实时通道仅发送 INFO 及以上。"""
    settings = settings or {}
    level_name = str(settings.get("level", "INFO")).upper()
    level = _LEVELS.get(level_name, logging.INFO)
    max_days = settings.get("max_days", 7)
    if type(max_days) is not int or max_days < 1:
        max_days = 7

    package_only = logger is None
    target = logger or logging.getLogger()
    target.setLevel(level)
    for handler in list(target.handlers):
        if getattr(handler, "_pixiv_downloader_handler", False):
            target.removeHandler(handler)
            handler.close()

    directory = Path(logs_dir)
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    package_filter = _PackageLogFilter() if package_only else None

    file_handler = TimedRotatingFileHandler(
        directory / "pixiv_downloader.log",
        when="midnight",
        interval=1,
        backupCount=max_days,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._pixiv_downloader_handler = True
    if package_filter is not None:
        file_handler.addFilter(package_filter)
    target.addHandler(file_handler)

    if publish is not None:
        realtime_handler = _RealtimeLogHandler(publish)
        realtime_handler.setFormatter(formatter)
        if package_filter is not None:
            realtime_handler.addFilter(package_filter)
        target.addHandler(realtime_handler)
    return target
