"""Issue #11 错误分类、轮转日志、失败项重试与实时日志契约。"""
import asyncio
import errno
import logging
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler

import pytest


def test_error_classification_is_safe_and_stable():
    from src.pixiv_downloader.errors import (
        ErrorCategory,
        FatalDownloadError,
        RecoverableDownloadError,
        SkippableDownloadError,
        classify_error,
    )

    assert classify_error(RecoverableDownloadError()).category is ErrorCategory.RECOVERABLE
    assert classify_error(SkippableDownloadError()).category is ErrorCategory.SKIPPED
    assert classify_error(FatalDownloadError()).category is ErrorCategory.FATAL
    disk_full = classify_error(OSError(errno.ENOSPC, "secret path or token"))
    assert disk_full.category is ErrorCategory.FATAL
    assert disk_full.message == "磁盘空间不足。"
    assert "secret" not in disk_full.message
    invalid_name = classify_error(OSError(errno.ENAMETOOLONG, "secret path"))
    assert invalid_name.category is ErrorCategory.SKIPPED
    assert "secret" not in invalid_name.message
    assert classify_error(RuntimeError("unknown secret")).category is ErrorCategory.SKIPPED


def test_logging_uses_configured_level_daily_rotation_and_no_duplicate_handlers(tmp_path):
    from src.pixiv_downloader.logging_setup import configure_logging

    logger = logging.getLogger("pixiv-downloader-test-rotation")
    logger.handlers.clear()
    published = []

    configure_logging(
        tmp_path,
        {"level": "DEBUG", "max_days": 7},
        publish=published.append,
        logger=logger,
    )
    configure_logging(
        tmp_path,
        {"level": "DEBUG", "max_days": 7},
        publish=published.append,
        logger=logger,
    )

    rotating = [handler for handler in logger.handlers if isinstance(handler, TimedRotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].backupCount == 7
    assert rotating[0].when == "MIDNIGHT"
    assert logger.level == logging.DEBUG

    logger.debug("debug-to-file")
    logger.info("safe-info")
    for handler in logger.handlers:
        handler.flush()

    assert "debug-to-file" in (tmp_path / "pixiv_downloader.log").read_text(encoding="utf-8")
    assert published == [{
        "timestamp": published[0]["timestamp"],
        "level": "INFO",
        "message": "safe-info",
    }]


def test_invalid_log_level_falls_back_to_info_and_publisher_failure_is_isolated(tmp_path):
    from src.pixiv_downloader.logging_setup import configure_logging

    logger = logging.getLogger("pixiv-downloader-test-publisher-failure")
    logger.handlers.clear()

    def broken_publish(payload):
        raise RuntimeError("socket disconnected")

    configure_logging(
        tmp_path,
        {"level": "not-a-level", "max_days": 7},
        publish=broken_publish,
        logger=logger,
    )

    logger.debug("debug-filtered")
    logger.info("task-keeps-running")
    for handler in logger.handlers:
        handler.flush()

    text = (tmp_path / "pixiv_downloader.log").read_text(encoding="utf-8")
    assert logger.level == logging.INFO
    assert "task-keeps-running" in text
    assert "debug-filtered" not in text


def test_database_records_failed_artwork_once_and_exposes_retry_ids(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id = database.create_task(task_type="artist", query={"artist_id": 123})
        database.transition_task(task_id, "pending", "running")

        first = database.record_task_failure(
            task_id,
            artwork_id=456,
            category="skipped",
            message="作品或文件不存在。",
        )
        duplicate = database.record_task_failure(
            task_id,
            artwork_id=456,
            category="skipped",
            message="作品或文件不存在。",
        )

        assert first["failed_count"] == 1
        assert duplicate["failed_count"] == 1
        assert database.list_task_failures(task_id) == [{
            "failure_id": 1,
            "task_id": task_id,
            "artwork_id": 456,
            "category": "skipped",
            "message": "作品或文件不存在。",
            "created_time": database.list_task_failures(task_id)[0]["created_time"],
        }]
        assert database.failed_artwork_ids(task_id) == [456]
    finally:
        database.close()


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_database_rejects_results_and_failure_details_after_terminal_status(
    tmp_path,
    terminal_status,
):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id = database.create_task(task_type="artist", query={"artist_id": 123})
        database.transition_task(task_id, "pending", "running")
        database.transition_task(task_id, "running", terminal_status)

        result = database.record_task_result(task_id, "downloaded")
        failure = database.record_task_failure(
            task_id,
            artwork_id=99,
            category="skipped",
            message="作品或文件不存在。",
        )

        assert result is None
        assert failure is None
        assert database.get_task(task_id)["downloaded_count"] == 0
        assert database.get_task(task_id)["failed_count"] == 0
        assert database.list_task_failures(task_id) == []
    finally:
        database.close()


def test_database_deduplicates_only_active_retry_tasks(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        source_task_id = database.create_task(
            task_type="artist",
            query={"artist_id": 123},
        )
        first_id, first_created = database.create_retry_task(
            source_task_id=source_task_id,
            artwork_ids=[8, 13],
        )
        duplicate_id, duplicate_created = database.create_retry_task(
            source_task_id=source_task_id,
            artwork_ids=[8, 13],
        )

        assert first_created is True
        assert (duplicate_id, duplicate_created) == (first_id, False)

        database.transition_task(first_id, "pending", "running")
        database.transition_task(first_id, "running", "completed")
        next_id, next_created = database.create_retry_task(
            source_task_id=source_task_id,
            artwork_ids=[8, 13],
        )

        assert next_created is True
        assert next_id != first_id
    finally:
        database.close()


@pytest.mark.asyncio
async def test_skippable_failure_is_persisted_and_next_item_continues(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.errors import SkippableDownloadError

    @asynccontextmanager
    async def session_factory(**options):
        yield object()

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Control:
        def __init__(self):
            self.failures = []
            self.results = []

        def set_total(self, total):
            self.total = total

        def checkpoint(self):
            return True

        def record_failure(self, artwork_id, category, message):
            self.failures.append((artwork_id, category, message))

        def record_result(self, result):
            self.results.append(result)

    downloader = ArtistDownloader(object(), object(), Proxy(), session_factory=session_factory)

    async def download(artwork, **options):
        if artwork["id"] == 1:
            raise SkippableDownloadError("must not leak")
        return "downloaded"

    downloader.download_artwork = download
    control = Control()
    result = await downloader._download_items(
        [{"id": 1}, {"id": 2}],
        lambda artwork: artwork,
        task_id=9,
        start_index=0,
        control=control,
    )

    assert result == {"total": 2, "downloaded": 1, "skipped": 0, "failed": 1}
    assert control.failures == [(1, "skipped", "作品下载失败，已跳过。")]
    assert control.results == ["downloaded"]


@pytest.mark.asyncio
async def test_fatal_failure_stops_task_instead_of_becoming_item_failure(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.errors import FatalDownloadError

    @asynccontextmanager
    async def session_factory(**options):
        yield object()

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Control:
        def set_total(self, total):
            pass

        def checkpoint(self):
            return True

        def record_failure(self, *args):
            raise AssertionError("致命错误不得记录为可跳过失败项")

        def record_result(self, result):
            raise AssertionError("致命错误后不得完成任务")

    downloader = ArtistDownloader(object(), object(), Proxy(), session_factory=session_factory)

    async def download(artwork, **options):
        raise FatalDownloadError("磁盘空间不足。")

    downloader.download_artwork = download
    with pytest.raises(FatalDownloadError, match="磁盘空间不足"):
        await downloader._download_items(
            [{"id": 1}],
            lambda artwork: artwork,
            task_id=9,
            start_index=0,
            control=Control(),
        )


@pytest.mark.asyncio
async def test_concurrent_fatal_cancels_inflight_and_never_starts_queued_items(tmp_path):
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.errors import FatalDownloadError

    @asynccontextmanager
    async def session_factory(**options):
        yield object()

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Control:
        def set_total(self, total):
            pass

        def checkpoint(self):
            return True

        def record_failure(self, *args):
            raise AssertionError("fatal 不得记录为作品失败")

        def record_result(self, result):
            raise AssertionError("fatal 批次不得提交正常完成计数")

    downloader = ArtistDownloader(
        object(),
        object(),
        Proxy(),
        session_factory=session_factory,
        policy=DownloadPolicy(max_concurrent=2),
    )
    started = []
    cancelled = []
    never_finishes = asyncio.Event()

    async def download(artwork, **options):
        artwork_id = artwork["id"]
        started.append(artwork_id)
        if artwork_id == 1:
            try:
                await never_finishes.wait()
            except asyncio.CancelledError:
                cancelled.append(artwork_id)
                raise
        if artwork_id == 2:
            raise FatalDownloadError("磁盘空间不足。")
        return "downloaded"

    downloader.download_artwork = download

    with pytest.raises(FatalDownloadError, match="磁盘空间不足"):
        await downloader._download_items(
            [{"id": 1}, {"id": 2}, {"id": 3}],
            lambda artwork: artwork,
            task_id=9,
            start_index=0,
            control=Control(),
        )

    assert started == [1, 2]
    assert cancelled == [1]


def test_retry_failed_api_creates_task_with_only_persisted_failed_ids(monkeypatch):
    from src.pixiv_downloader import app as app_module

    captured = {}

    class Database:
        def get_task(self, task_id):
            return {"task_id": task_id, "status": "completed"}

        def failed_artwork_ids(self, task_id):
            return [8, 13]

    class Manager:
        def create_retry_task(self, **values):
            captured.update(values)
            return 99, True

    monkeypatch.setattr(app_module, "database_manager", Database())
    monkeypatch.setattr(app_module, "task_manager", Manager())

    response = app_module.app.test_client().post("/api/tasks/7/retry-failed")

    assert response.status_code == 201
    assert response.get_json() == {
        "success": True,
        "task_id": 99,
        "selected_count": 2,
        "status": "pending",
    }
    assert captured == {
        "source_task_id": 7,
        "artwork_ids": [8, 13],
    }


def test_retry_failed_api_rejects_duplicate_active_retry(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Database:
        def get_task(self, task_id):
            return {"task_id": task_id, "status": "completed"}

        def failed_artwork_ids(self, task_id):
            return [8]

    class Manager:
        def create_retry_task(self, **values):
            return 44, False

    monkeypatch.setattr(app_module, "database_manager", Database())
    monkeypatch.setattr(app_module, "task_manager", Manager())

    response = app_module.app.test_client().post("/api/tasks/7/retry-failed")

    assert response.status_code == 409
    assert response.get_json() == {
        "success": False,
        "error": "该任务已有失败项重试任务正在处理。",
        "existing_task_id": 44,
    }


def test_task_control_persists_failure_detail_and_task_continues(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database = DatabaseManager(tmp_path / "history.db")

    def execute(task, control):
        control.set_total(2)
        control.record_failure(8, "skipped", "作品或文件不存在。")
        control.record_result("downloaded")

    try:
        manager = TaskManager(database, execute)
        task_id = manager.create_task(task_type="artist", query={"artist_id": 123})
        completed = manager.start_task(task_id)

        assert completed["status"] == "completed"
        assert completed["downloaded_count"] == 1
        assert completed["failed_count"] == 1
        assert database.failed_artwork_ids(task_id) == [8]
    finally:
        database.close()


def test_fatal_error_marks_task_failed_and_logs_error_without_exception_text(
    tmp_path,
    caplog,
):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.errors import FatalDownloadError
    from src.pixiv_downloader.task_manager import TaskManager

    database = DatabaseManager(tmp_path / "history.db")

    def execute(task, control):
        raise FatalDownloadError("secret-token-value")

    try:
        events = []

        def publish(event, payload):
            events.append((event, payload, database.get_task(task_id)["status"]))

        manager = TaskManager(database, execute, publish=publish)
        task_id = manager.create_task(task_type="artist", query={"artist_id": 123})
        with caplog.at_level("ERROR", logger="src.pixiv_downloader.task_manager"):
            with pytest.raises(FatalDownloadError):
                manager.start_task(task_id)

        assert database.get_task(task_id)["status"] == "failed"
        assert f"任务执行失败：task_id={task_id}" in caplog.text
        assert "secret-token-value" not in caplog.text
        assert events == [(
            "error",
            {
                "task_id": task_id,
                "status": "failed",
                "message": "任务执行失败，请检查网络、代理或登录状态。",
            },
            "failed",
        )]
    finally:
        database.close()


@pytest.mark.parametrize(
    ("status", "failed_ids", "expected_error"),
    [
        ("running", [8], "只能重试已结束任务的失败作品。"),
        ("completed", [], "该任务没有失败作品。"),
    ],
)
def test_retry_failed_api_rejects_active_or_empty_tasks(
    monkeypatch,
    status,
    failed_ids,
    expected_error,
):
    from src.pixiv_downloader import app as app_module

    class Database:
        def get_task(self, task_id):
            return {"task_id": task_id, "status": status}

        def failed_artwork_ids(self, task_id):
            return failed_ids

    monkeypatch.setattr(app_module, "database_manager", Database())

    response = app_module.app.test_client().post("/api/tasks/7/retry-failed")

    assert response.status_code == 409
    assert response.get_json() == {"success": False, "error": expected_error}


def test_task_detail_api_returns_counts_and_failed_artworks(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Database:
        def get_task(self, task_id):
            return {
                "task_id": task_id,
                "downloaded_count": 2,
                "skipped_count": 1,
                "failed_count": 1,
            }

        def list_task_failures(self, task_id):
            return [{"artwork_id": 8, "category": "skipped", "message": "作品或文件不存在。"}]

    monkeypatch.setattr(app_module, "database_manager", Database())

    response = app_module.app.test_client().get("/api/tasks/7")

    assert response.status_code == 200
    assert response.get_json()["failed_artworks"] == [{
        "artwork_id": 8,
        "category": "skipped",
        "message": "作品或文件不存在。",
    }]


def test_retry_failed_executor_downloads_only_persisted_ids(monkeypatch, tmp_path):
    from src.pixiv_downloader import app as app_module

    captured = {}

    class Downloader:
        def __init__(self, *args, **kwargs):
            pass

        async def download_artwork_ids(self, artwork_ids, **options):
            captured["artwork_ids"] = artwork_ids
            captured["options"] = options

    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)
    monkeypatch.setattr(app_module.auth_manager, "get_access_token", lambda: "token")
    monkeypatch.setattr(app_module.auth_manager, "api", object())
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(tmp_path)}},
    )

    control = object()
    app_module._execute_download_task(
        {
            "task_id": 99,
            "task_type": "retry_failed",
            "query": {"source_task_id": 7, "artwork_ids": [8, 13]},
            "downloaded_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        },
        control,
    )

    assert captured == {
        "artwork_ids": [8, 13],
        "options": {"task_id": 99, "start_index": 0, "control": control},
    }


def test_log_entry_uses_real_socketio_transport():
    from src.pixiv_downloader import app as app_module

    client = app_module.socketio.test_client(app_module.app)
    try:
        app_module._publish_log_entry({
            "timestamp": "2026-07-22T00:00:00+00:00",
            "level": "INFO",
            "message": "任务开始",
        })
        assert client.get_received() == [{
            "name": "log_entry",
            "args": [{
                "timestamp": "2026-07-22T00:00:00+00:00",
                "level": "INFO",
                "message": "任务开始",
            }],
            "namespace": "/",
        }]
    finally:
        client.disconnect()


def test_frontend_renders_failure_details_retry_button_and_log_console():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")

    for marker in ("task-log-console", "实时日志", "INFO 及以上"):
        assert marker in page
    for marker in (
        "socket.on('log_entry'",
        "data-task-detail",
        "data-retry-failed",
        "failed_artworks",
        "retry-failed",
    ):
        assert marker in script
