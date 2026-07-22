"""关注画师新作检测的领域、数据库与 Web 集成测试。"""


def test_followed_artist_state_roundtrip(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        assert database.get_followed_artist(456) is None

        database.update_followed_artist(
            456,
            artist_name="画师 A",
            last_artwork_id=100,
            checked_at="2026-07-21T12:00:00+00:00",
        )

        assert database.get_followed_artist(456) == {
            "artist_id": 456,
            "artist_name": "画师 A",
            "last_check_time": "2026-07-21T12:00:00+00:00",
            "last_artwork_id": 100,
        }
        assert database.list_followed_artists() == [
            {
                "artist_id": 456,
                "artist_name": "画师 A",
                "last_check_time": "2026-07-21T12:00:00+00:00",
                "last_artwork_id": 100,
            }
        ]
    finally:
        database.close()


def test_fetch_follow_latest_detects_new_artworks_and_updates_state(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.follow_latest import FollowLatestScanner

    database = DatabaseManager(tmp_path / "history.db")
    database.update_followed_artist(
        10,
        artist_name="画师 A",
        last_artwork_id=200,
        checked_at="2026-07-20T00:00:00+00:00",
    )

    class Api:
        user_id = 999

        def __init__(self):
            self.follow_calls = []
            self.artwork_calls = []

        def user_following(self, user_id, restrict="public", offset=None):
            self.follow_calls.append((user_id, restrict, offset))
            if restrict == "public" and offset is None:
                return {
                    "user_previews": [
                        {"user": {"id": 10, "name": "画师 A"}},
                        {"user": {"id": 20, "name": "画师 B"}},
                    ],
                    "next_url": "https://app-api.pixiv.net/v1/user/following?offset=30",
                }
            if restrict == "public":
                return {"user_previews": [], "next_url": None}
            return {
                "user_previews": [
                    {"user": {"id": 20, "name": "画师 B"}},
                    {"user": {"id": 30, "name": "画师 C"}},
                ],
                "next_url": None,
            }

        def user_illusts(self, user_id, **kwargs):
            self.artwork_calls.append((user_id, kwargs))
            if user_id == 10:
                return {
                    "illusts": [
                        follow_artwork(203, 10, "画师 A", 900),
                        follow_artwork(202, 10, "画师 A", 600),
                        follow_artwork(200, 10, "画师 A", 100),
                    ],
                    "next_url": None,
                }
            if user_id == 20:
                return {
                    "illusts": [
                        follow_artwork(50, 20, "画师 B", 700),
                        follow_artwork(49, 20, "画师 B", 400),
                    ],
                    "next_url": "https://app-api.pixiv.net/v1/user/illusts?user_id=20&offset=30",
                }
            return {"illusts": [], "next_url": None}

    api = Api()
    scanner = FollowLatestScanner(
        api=api,
        database=database,
        clock=lambda: "2026-07-21T12:34:56+00:00",
    )

    result = scanner.fetch_follow_latest()

    assert result["checked_at"] == "2026-07-21T12:34:56+00:00"
    assert result["new_count"] == 4
    assert [group["artist_id"] for group in result["artists"]] == [10, 20, 30]
    assert result["artists"][0]["last_check_time"] == "2026-07-20T00:00:00+00:00"
    assert [item["artwork_id"] for item in result["artists"][0]["artworks"]] == [203, 202]
    assert result["artists"][0]["artworks"][0] == {
        "artwork_id": 203,
        "artist_id": 10,
        "artist_name": "画师 A",
        "title": "作品 203",
        "thumbnail_url": "https://i.pximg.net/c/203.jpg",
        "bookmarks": 900,
    }
    assert [item["artwork_id"] for item in result["artists"][1]["artworks"]] == [50, 49]
    assert result["artists"][2]["artworks"] == []
    assert database.get_followed_artist(10)["last_artwork_id"] == 203
    assert database.get_followed_artist(20)["last_artwork_id"] == 50
    assert database.get_followed_artist(30)["last_artwork_id"] is None
    assert all(
        row["last_check_time"] == "2026-07-21T12:34:56+00:00"
        for row in database.list_followed_artists()
    )
    assert api.follow_calls == [
        (999, "public", None),
        (999, "public", "30"),
        (999, "private", None),
    ]
    assert api.artwork_calls == [(10, {}), (20, {}), (30, {})]
    database.close()


def follow_artwork(artwork_id, artist_id, artist_name, bookmarks):
    return {
        "id": artwork_id,
        "title": f"作品 {artwork_id}",
        "user": {"id": artist_id, "name": artist_name},
        "total_bookmarks": bookmarks,
        "image_urls": {"square_medium": f"https://i.pximg.net/c/{artwork_id}.jpg"},
    }


def test_selected_follow_artworks_create_pending_task(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id = database.create_task(
            task_type="follow_latest",
            query={"artwork_ids": [203, 50]},
            total_count=2,
        )

        assert database.get_task(task_id) == {
            "task_id": task_id,
            "task_type": "follow_latest",
            "query": {"artwork_ids": [203, 50]},
            "filters": None,
            "status": "pending",
            "created_time": database.get_task(task_id)["created_time"],
            "started_time": None,
            "completed_time": None,
            "total_count": 2,
            "downloaded_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        }
    finally:
        database.close()


def test_follow_latest_api_task_creation_and_frontend(monkeypatch):
    from pathlib import Path
    from src.pixiv_downloader import app as app_module

    calls = []
    scan_result = {
        "checked_at": "2026-07-21T12:34:56+00:00",
        "new_count": 2,
        "artists": [{
            "artist_id": 10,
            "artist_name": "画师 A",
            "last_check_time": "2026-07-20T00:00:00+00:00",
            "new_count": 2,
            "artworks": [
                {"artwork_id": 203, "artist_id": 10, "artist_name": "画师 A", "title": "作品 203", "thumbnail_url": "https://i.pximg.net/c/203.jpg", "bookmarks": 900},
                {"artwork_id": 202, "artist_id": 10, "artist_name": "画师 A", "title": "作品 202", "thumbnail_url": "https://i.pximg.net/c/202.jpg", "bookmarks": 400},
            ],
        }],
    }

    class Auth:
        api = object()
        def get_access_token(self):
            calls.append("authenticated")
            return "access"
        def has_saved_token(self):
            return True

    class Scanner:
        def __init__(self, *, api, database):
            calls.append(("scanner", api, database))
        def fetch_follow_latest(self):
            calls.append("scanned")
            return scan_result

    class Database:
        def create_task(self, **kwargs):
            calls.append(("task", kwargs))
            return 77

    database = Database()
    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "database_manager", database)
    monkeypatch.setattr(app_module, "FollowLatestScanner", Scanner, raising=False)
    client = app_module.app.test_client()

    scan = client.post("/api/follow/latest")
    task = client.post("/api/follow/download", json={"artwork_ids": [203, 50, 203]})
    empty = client.post("/api/follow/download", json={"artwork_ids": []})
    invalid = client.post("/api/follow/download", json={"artwork_ids": [203, True, "50"]})
    page = client.get(f"/?token={app_module.SECURITY_TOKEN}").get_data(as_text=True)
    script = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")

    assert scan.status_code == 200
    assert scan.get_json() == {"success": True, "result": scan_result}
    assert task.status_code == 200
    assert task.get_json() == {"success": True, "task_id": 77, "selected_count": 2}
    assert empty.status_code == 400
    assert invalid.status_code == 400
    assert calls == [
        "authenticated", ("scanner", Auth.api, database), "scanned",
        ("task", {"task_type": "follow_latest", "query": {"artwork_ids": [203, 50]}, "total_count": 2}),
    ]
    for element_id in (
        "follow-check-button", "follow-last-check", "follow-new-count",
        "follow-bookmark-filter", "follow-select-all", "follow-clear-all",
        "follow-artwork-groups", "follow-download-selected",
    ):
        assert f'id="{element_id}"' in page
    assert "/api/follow/latest" in script
    assert "/api/follow/download" in script
    assert "thumbnail_url" in script
    assert "bookmarks" in script


def test_follow_scan_failure_does_not_advance_partial_checkpoints(tmp_path):
    import pytest
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.follow_latest import FollowLatestScanner

    database = DatabaseManager(tmp_path / "history.db")
    database.update_followed_artist(
        10,
        artist_name="画师 A",
        last_artwork_id=100,
        checked_at="2026-07-20T00:00:00+00:00",
    )

    class Api:
        user_id = 999

        def user_following(self, user_id, restrict="public", offset=None):
            if restrict == "private":
                return {"user_previews": [], "next_url": None}
            return {
                "user_previews": [
                    {"user": {"id": 10, "name": "画师 A"}},
                    {"user": {"id": 20, "name": "画师 B"}},
                ],
                "next_url": None,
            }

        def user_illusts(self, user_id, **kwargs):
            if user_id == 20:
                raise RuntimeError("upstream unavailable")
            return {"illusts": [follow_artwork(101, 10, "画师 A", 1)], "next_url": None}

    scanner = FollowLatestScanner(
        api=Api(),
        database=database,
        clock=lambda: "2026-07-21T12:34:56+00:00",
    )
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        scanner.fetch_follow_latest()

    assert database.get_followed_artist(10) == {
        "artist_id": 10,
        "artist_name": "画师 A",
        "last_check_time": "2026-07-20T00:00:00+00:00",
        "last_artwork_id": 100,
    }
    assert database.get_followed_artist(20) is None
    database.close()
