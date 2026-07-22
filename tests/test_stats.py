"""下载历史统计查询与 Web 图表测试。"""
from datetime import datetime, timezone


def test_download_stats_all_and_recent_ranges(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        artist_task = database.create_task(task_type="artist", query="10", total_count=1)
        search_task = database.create_task(task_type="search", query="风景", total_count=1)
        database.mark_as_downloaded(
            1, artist_id=10, artist_name="画师 A", task_id=artist_task,
            file_path="1.jpg", file_size=100, tags=["风景", "原创"],
        )
        database.mark_as_downloaded(
            2, artist_id=10, artist_name="画师 A", task_id=search_task,
            file_path="2.jpg", file_size=200, tags=["风景"],
        )
        database.mark_as_downloaded(
            3, artist_id=20, artist_name="画师 B",
            file_path="3.jpg", file_size=300, tags=["人物"],
        )
        with database.connection:
            database.connection.execute(
                "UPDATE downloads SET download_time = ? WHERE artwork_id = 1",
                ("2026-07-21 09:00:00",),
            )
            database.connection.execute(
                "UPDATE downloads SET download_time = ? WHERE artwork_id = 2",
                ("2026-07-20 09:00:00",),
            )
            database.connection.execute(
                "UPDATE downloads SET download_time = ? WHERE artwork_id = 3",
                ("2026-07-01 09:00:00",),
            )

        all_stats = database.get_download_stats(
            "all", as_of=datetime(2026, 7, 22, tzinfo=timezone.utc)
        )
        assert all_stats == {
            "range": "all",
            "totals": {"downloads": 3, "artists": 2, "file_size": 600},
            "top_artists": [
                {"artist_id": 10, "artist_name": "画师 A", "count": 2},
                {"artist_id": 20, "artist_name": "画师 B", "count": 1},
            ],
            "top_tags": [
                {"tag": "风景", "count": 2},
                {"tag": "人物", "count": 1},
                {"tag": "原创", "count": 1},
            ],
            "trend": [
                {"date": "2026-07-01", "count": 1},
                {"date": "2026-07-20", "count": 1},
                {"date": "2026-07-21", "count": 1},
            ],
            "task_types": [
                {"task_type": "artist", "count": 2},
                {"task_type": "search", "count": 1},
            ],
        }

        recent = database.get_download_stats(
            "7", as_of=datetime(2026, 7, 22, tzinfo=timezone.utc)
        )
        assert recent["totals"] == {"downloads": 2, "artists": 1, "file_size": 300}
        assert recent["trend"] == [
            {"date": "2026-07-20", "count": 1},
            {"date": "2026-07-21", "count": 1},
        ]
        assert recent["task_types"] == [
            {"task_type": "artist", "count": 1},
            {"task_type": "search", "count": 1},
        ]
    finally:
        database.close()


def test_existing_database_adds_file_size_column(tmp_path):
    import sqlite3
    from src.pixiv_downloader.database import DatabaseManager

    database_path = tmp_path / "legacy.db"
    legacy_file = tmp_path / "legacy.jpg"
    legacy_file.write_bytes(b"legacy-bytes")
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE downloads (
            artwork_id INTEGER PRIMARY KEY,
            artist_id INTEGER,
            artist_name TEXT,
            title TEXT,
            download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            task_id INTEGER,
            file_path TEXT,
            page_count INTEGER,
            bookmarks INTEGER,
            tags TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO downloads (artwork_id, file_path) VALUES (?, ?)",
        (1, str(legacy_file)),
    )
    connection.commit()
    connection.close()

    database = DatabaseManager(database_path)
    try:
        columns = {
            row[1] for row in database.connection.execute("PRAGMA table_info(downloads)")
        }
        assert "file_size" in columns
        assert database.get_download_stats()["totals"]["file_size"] == len(b"legacy-bytes")
    finally:
        database.close()


def test_stats_api_and_chart_frontend(monkeypatch):
    from pathlib import Path
    from src.pixiv_downloader import app as app_module

    calls = []
    stats = {
        "range": "7",
        "totals": {"downloads": 2, "artists": 1, "file_size": 300},
        "top_artists": [{"artist_id": 10, "artist_name": "画师 A", "count": 2}],
        "top_tags": [{"tag": "风景", "count": 2}],
        "trend": [{"date": "2026-07-21", "count": 2}],
        "task_types": [{"task_type": "artist", "count": 2}],
    }

    class Database:
        def get_download_stats(self, time_range):
            calls.append(time_range)
            return {**stats, "range": time_range}

    class Auth:
        def has_saved_token(self):
            return True

    monkeypatch.setattr(app_module, "database_manager", Database())
    monkeypatch.setattr(app_module, "auth_manager", Auth())
    client = app_module.app.test_client()

    response = client.get("/api/stats?range=7")
    invalid = client.get("/api/stats?range=365")
    page = client.get(f"/?token={app_module.SECURITY_TOKEN}").get_data(as_text=True)
    script = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert response.status_code == 200
    assert response.get_json() == {"success": True, "stats": stats}
    assert invalid.status_code == 400
    assert calls == ["7"]
    for element_id in (
        "stats-panel", "stats-range", "stats-total-downloads",
        "stats-total-artists", "stats-total-size", "stats-artists-chart",
        "stats-tags-chart", "stats-trend-chart", "stats-types-chart",
    ):
        assert f'id="{element_id}"' in page
    assert '/static/vendor/chart.umd.min.js' in page
    assert "/api/stats?range=" in script
    assert "new Chart" in script


def test_stats_group_renamed_artist_by_stable_id(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        database.mark_as_downloaded(1, artist_id=10, artist_name="旧名称")
        database.mark_as_downloaded(2, artist_id=10, artist_name="新名称")
        with database.connection:
            database.connection.execute(
                "UPDATE downloads SET download_time = '2026-07-20 09:00:00' WHERE artwork_id = 1"
            )
            database.connection.execute(
                "UPDATE downloads SET download_time = '2026-07-21 09:00:00' WHERE artwork_id = 2"
            )

        stats = database.get_download_stats("all")
        assert stats["totals"]["artists"] == 1
        assert stats["top_artists"] == [
            {"artist_id": 10, "artist_name": "新名称", "count": 2}
        ]
    finally:
        database.close()


def test_stats_ignore_json_tags_that_are_not_arrays(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        database.mark_as_downloaded(1, tags=[])
        database.mark_as_downloaded(2, tags=[])
        database.mark_as_downloaded(3, tags=[])
        with database.connection:
            database.connection.execute(
                "UPDATE downloads SET tags = '123' WHERE artwork_id = 1"
            )
            database.connection.execute(
                "UPDATE downloads SET tags = 'null' WHERE artwork_id = 2"
            )
            database.connection.execute(
                "UPDATE downloads SET tags = '{\"tag\": \"风景\"}' WHERE artwork_id = 3"
            )

        stats = database.get_download_stats("all")
        assert stats["totals"]["downloads"] == 3
        assert stats["top_tags"] == []
    finally:
        database.close()
