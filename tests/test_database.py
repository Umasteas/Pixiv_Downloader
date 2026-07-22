"""SQLite 下载历史与去重模块测试。"""
import sqlite3

from src.pixiv_downloader.database import DatabaseManager


EXPECTED_COLUMNS = {
    "tasks": [
        "task_id",
        "task_type",
        "query",
        "filters",
        "status",
        "created_time",
        "started_time",
        "completed_time",
        "total_count",
        "downloaded_count",
        "skipped_count",
        "failed_count",
    ],
    "downloads": [
        "artwork_id",
        "artist_id",
        "artist_name",
        "title",
        "download_time",
        "task_id",
        "file_path",
        "file_size",
        "page_count",
        "bookmarks",
        "tags",
    ],
    "followed_artists": [
        "artist_id",
        "artist_name",
        "last_check_time",
        "last_artwork_id",
    ],
}


def table_columns(connection, table_name):
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def test_first_start_creates_database_file_and_complete_schema(tmp_path):
    database_path = tmp_path / "history.db"

    manager = DatabaseManager(database_path)
    try:
        assert database_path.exists()
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            assert table_columns(manager.connection, table_name) == expected_columns

        downloads_info = manager.connection.execute(
            "PRAGMA table_info(downloads)"
        ).fetchall()
        artwork_id = next(row for row in downloads_info if row[1] == "artwork_id")
        assert artwork_id[5] == 1  # PRAGMA pk 标记
    finally:
        manager.close()


def test_in_memory_database_supports_deduplication_and_history():
    manager = DatabaseManager(":memory:")
    try:
        assert manager.is_downloaded(1001) is False

        manager.mark_as_downloaded(
            1001,
            artist_id=2002,
            artist_name="测试画师",
            title="测试作品",
            file_path="downloads/1001.jpg",
            file_size=1024,
            page_count=2,
            bookmarks=321,
            tags=["风景", "原创"],
        )

        assert manager.is_downloaded(1001) is True
        history = manager.get_download_history()
        assert len(history) == 1
        assert history[0] == {
            "artwork_id": 1001,
            "artist_id": 2002,
            "artist_name": "测试画师",
            "title": "测试作品",
            "download_time": history[0]["download_time"],
            "task_id": None,
            "file_path": "downloads/1001.jpg",
            "file_size": 1024,
            "page_count": 2,
            "bookmarks": 321,
            "tags": ["风景", "原创"],
        }
        assert history[0]["download_time"]
    finally:
        manager.close()


def test_marking_same_artwork_again_updates_single_history_record():
    manager = DatabaseManager(":memory:")
    try:
        manager.mark_as_downloaded(42, title="旧标题", file_path="old.jpg")
        manager.mark_as_downloaded(42, title="新标题", file_path="new.jpg")

        history = manager.get_download_history()
        assert len(history) == 1
        assert history[0]["title"] == "新标题"
        assert history[0]["file_path"] == "new.jpg"
    finally:
        manager.close()


def test_history_limit_returns_requested_number_of_latest_records():
    manager = DatabaseManager(":memory:")
    try:
        for artwork_id in range(1, 4):
            manager.mark_as_downloaded(artwork_id, title=f"作品 {artwork_id}")

        history = manager.get_download_history(limit=2)

        assert len(history) == 2
        assert [item["artwork_id"] for item in history] == [3, 2]
    finally:
        manager.close()


def test_database_enables_foreign_key_checks():
    manager = DatabaseManager(":memory:")
    try:
        enabled = manager.connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1

        try:
            manager.mark_as_downloaded(100, task_id=999)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("不存在的 task_id 应触发外键约束")
    finally:
        manager.close()
