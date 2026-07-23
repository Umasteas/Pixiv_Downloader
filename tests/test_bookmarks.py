"""Issue #15 收藏任务领域与集成测试。"""
from datetime import date

import pytest


class Proxy:
    def aiohttp_options(self):
        return {}, {}


def artwork(artwork_id, *, created="2026-01-01T00:00:00+09:00", bookmarks=0):
    return {
        "id": artwork_id,
        "type": "illust",
        "x_restrict": 0,
        "total_bookmarks": bookmarks,
        "create_date": created,
        "title": f"作品 {artwork_id}",
        "user": {"id": 1, "name": "画师"},
    }


def test_normalize_bookmarks_query_applies_stable_defaults():
    from src.pixiv_downloader.bookmarks import normalize_bookmarks_query

    assert normalize_bookmarks_query({"visibility": "public"}) == {
        "visibility": "public",
        "tag": None,
        "sort": "bookmark_time",
        "limit": None,
    }
    assert normalize_bookmarks_query({
        "visibility": "private",
        "tag": "  风景  ",
        "sort": "bookmarks",
        "limit": 1000,
    }) == {
        "visibility": "private",
        "tag": "风景",
        "sort": "bookmarks",
        "limit": 1000,
    }


def test_bookmarks_resource_budgets_are_explicit():
    from src.pixiv_downloader.bookmarks import (
        MAX_BOOKMARK_ITEMS,
        MAX_BOOKMARK_PAGES,
        MAX_BOOKMARK_TAGS,
    )

    assert MAX_BOOKMARK_ITEMS == 5000
    assert MAX_BOOKMARK_PAGES == 200
    assert MAX_BOOKMARK_TAGS == 1000


@pytest.mark.parametrize(
    "query",
    [
        None,
        {},
        {"visibility": "friends"},
        {"visibility": True},
        {"visibility": "public", "sort": "popular"},
        {"visibility": "public", "limit": True},
        {"visibility": "public", "limit": "all"},
        {"visibility": "public", "limit": 0},
        {"visibility": "public", "limit": 1001},
        {"visibility": "public", "tag": 42},
        {"visibility": "public", "tag": "x" * 101},
        {"visibility": "public", "tag": "bad\nlabel"},
        {"visibility": "public", "extra": "bypass"},
    ],
)
def test_normalize_bookmarks_query_rejects_invalid_values(query):
    from src.pixiv_downloader.bookmarks import (
        BookmarksValidationError,
        normalize_bookmarks_query,
    )

    with pytest.raises(BookmarksValidationError, match="收藏条件无效"):
        normalize_bookmarks_query(query)


def test_fetch_bookmark_tags_maps_visibility_and_safe_offset(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []
    pages = [
        {
            "bookmark_tags": [{"name": "风景"}, {"name": "原创"}],
            "next_url": "https://evil.invalid/?offset=30&token=secret",
        },
        {
            "bookmark_tags": [{"name": "风景"}, {"name": "动图"}],
            "next_url": None,
        },
    ]

    class API:
        def user_bookmark_tags_illust(self, **options):
            calls.append(options)
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    assert downloader.fetch_bookmark_tags("private") == ["风景", "原创", "动图"]
    assert calls == [
        {"restrict": "private"},
        {"restrict": "private", "offset": 30},
    ]


def test_fetch_bookmarks_maps_user_visibility_tag_and_cursor(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []
    pages = [
        {
            "illusts": [artwork(1), artwork(2)],
            "next_url": "https://evil.invalid/?max_bookmark_id=900&token=secret",
        },
        {"illusts": [artwork(2), artwork(3)], "next_url": None},
    ]

    class API:
        def user_bookmarks_illust(self, **options):
            calls.append(options)
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    result = downloader.fetch_bookmarks("private", "风景", "bookmark_time", 3)

    assert [item["id"] for item in result] == [1, 2, 3]
    assert calls == [
        {"user_id": 42, "restrict": "private", "filter": "for_ios", "tag": "风景"},
        {
            "user_id": 42,
            "restrict": "private",
            "filter": "for_ios",
            "tag": "风景",
            "max_bookmark_id": 900,
        },
    ]


@pytest.mark.parametrize(
    "sort, expected",
    [
        ("bookmark_time", [1, 2]),
        ("artwork_date", [3, 2]),
        ("bookmarks", [3, 2]),
    ],
)
def test_fetch_bookmarks_applies_deterministic_sort(sort, expected, tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    items = [
        artwork(1, created="2024-01-01T00:00:00+09:00", bookmarks=10),
        artwork(2, created="2025-01-01T00:00:00+09:00", bookmarks=30),
        artwork(3, created="2026-01-01T00:00:00+09:00", bookmarks=30),
    ]

    class API:
        def user_bookmarks_illust(self, **options):
            return {"illusts": items, "next_url": None}

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    result = downloader.fetch_bookmarks("public", None, sort, 2)

    assert [item["id"] for item in result] == expected


def test_fetch_bookmarks_rejects_unsafe_cursor_and_invalid_ids(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def user_bookmarks_illust(self, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [
                    {"id": None},
                    {"id": True},
                    {"id": -1},
                    {"id": "bad"},
                    artwork(2),
                ],
                "next_url": f"https://evil.invalid/?max_bookmark_id={'9' * 4301}",
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    result = downloader.fetch_bookmarks("public", None, "bookmark_time", None)

    assert [item["id"] for item in result] == [2]
    assert calls == 1


def test_fetch_bookmarks_all_and_pages_are_bounded(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def user_bookmarks_illust(self, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [artwork(calls)],
                "next_url": f"https://x.invalid/?max_bookmark_id={10000 - calls}",
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    result = downloader.fetch_bookmarks("public", None, "bookmark_time", None)

    assert len(result) == 200
    assert calls == 200


def test_fetch_bookmarks_checks_cancellation_before_each_page(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def user_bookmarks_illust(self, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [artwork(calls)],
                "next_url": f"https://x.invalid/?max_bookmark_id={1000 - calls}",
            }

    class Control:
        checkpoints = 0

        def checkpoint(self):
            self.checkpoints += 1
            return self.checkpoints == 1

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    result = downloader.fetch_bookmarks(
        "public", None, "bookmark_time", None, control=Control()
    )

    assert [item["id"] for item in result] == [1]
    assert calls == 1


def test_create_bookmarks_directory_uses_fixed_task_date(tmp_path):
    from src.pixiv_downloader.file_organizer import create_bookmarks_directory

    directory = create_bookmarks_directory(tmp_path, date(2026, 7, 23))

    assert directory == tmp_path / "bookmarks" / "2026-07-23"
    assert directory.is_dir()
    assert directory.resolve().is_relative_to((tmp_path / "bookmarks").resolve())


@pytest.mark.asyncio
async def test_download_bookmarks_reports_new_and_existing_and_writes_metadata(
    tmp_path, monkeypatch
):
    import json

    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.file_organizer import create_bookmarks_directory

    candidates = [
        {
            **artwork(1, bookmarks=50),
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/1.jpg"},
            "tags": [{"name": "风景"}],
        },
        {
            **artwork(2, bookmarks=20),
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/2.jpg"},
        },
    ]

    class API:
        def user_bookmarks_illust(self, **options):
            return {"illusts": candidates, "next_url": None}

    class Database:
        records = []

        def is_downloaded(self, artwork_id):
            return artwork_id == 2

        def mark_as_downloaded(self, artwork_id, **record):
            self.records.append((artwork_id, record))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    task_date = date(2026, 7, 23)
    collection_dir = create_bookmarks_directory(tmp_path, task_date)
    database = Database()
    downloader = ArtistDownloader(
        API(),
        database,
        Proxy(),
        output_dir=tmp_path,
        collection_dir=collection_dir,
        source="bookmarks",
        metadata_context={
            "visibility": "public",
            "tag": "风景",
            "sort": "bookmark_time",
        },
        user_id=42,
        session_factory=lambda **options: Session(),
    )

    async def write_file(url, save_path, **options):
        save_path.write_bytes(b"image")

    monkeypatch.setattr(downloader, "download_file", write_file)

    result = await downloader.download_bookmarks(
        "public",
        "风景",
        "bookmark_time",
        100,
        filters={
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 0,
        },
    )

    assert result == {"total": 2, "downloaded": 1, "skipped": 1, "failed": 0}
    image_path = collection_dir / "1_画师_作品 1.jpg"
    metadata = json.loads(
        image_path.with_suffix(".metadata.json").read_text(encoding="utf-8")
    )
    assert {
        key: metadata["download_info"][key]
        for key in ("source", "visibility", "tag", "sort")
    } == {
        "source": "bookmarks",
        "visibility": "public",
        "tag": "风景",
        "sort": "bookmark_time",
    }
    assert database.records[0][1]["file_path"] == str(image_path)


def test_execute_bookmarks_task_revalidates_and_uses_fixed_task_date(
    monkeypatch, tmp_path
):
    from src.pixiv_downloader import app as app_module

    captured = {}

    class Downloader:
        def __init__(
            self,
            api,
            database,
            proxy,
            *,
            output_dir,
            policy,
            ugoira_options,
            collection_dir,
            source,
            metadata_context,
            user_id,
        ):
            captured.update({
                "collection_dir": collection_dir,
                "source": source,
                "metadata_context": metadata_context,
                "user_id": user_id,
            })

        async def download_bookmarks(self, visibility, tag, sort, limit, **options):
            captured["query"] = (visibility, tag, sort, limit)
            captured["options"] = options
            options["snapshot_callback"]([9, 1, 2])

    class Auth:
        api = object()

        def get_access_token(self):
            return "ok"

        def get_user_id(self):
            return 42

    class Config:
        def load_config(self):
            return {"download": {"output_dir": str(tmp_path)}, "ugoira": {}}

    class Database:
        def set_bookmark_snapshot(self, task_id, artwork_ids):
            captured["snapshot"] = (task_id, artwork_ids)

    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)
    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "config_manager", Config())
    monkeypatch.setattr(app_module, "database_manager", Database())

    app_module._execute_download_task(
        {
            "task_id": 15,
            "task_type": "bookmarks",
            "query": {
                "visibility": "private",
                "tag": " 风景 ",
                "sort": "bookmarks",
                "limit": None,
            },
            "filters": {
                "work_types": ["illust"],
                "r18_modes": ["general"],
                "min_bookmarks": 10,
            },
            "created_time": "2026-07-23T12:34:56+00:00",
            "downloaded_count": 2,
            "skipped_count": 1,
            "failed_count": 1,
        },
        object(),
    )

    assert captured["collection_dir"] == tmp_path / "bookmarks" / "2026-07-23"
    assert captured["source"] == "bookmarks"
    assert captured["metadata_context"] == {
        "visibility": "private",
        "tag": "风景",
        "sort": "bookmarks",
    }
    assert captured["user_id"] == 42
    assert captured["query"] == ("private", "风景", "bookmarks", None)
    assert captured["options"]["start_index"] == 4
    assert captured["options"]["artwork_ids"] is None
    assert captured["snapshot"] == (15, [9, 1, 2])


def test_execute_bookmarks_task_rejects_tampered_persisted_query():
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.task_manager import TaskManagerError

    with pytest.raises(TaskManagerError, match="收藏任务缺少有效查询"):
        app_module._execute_download_task(
            {
                "task_id": 15,
                "task_type": "bookmarks",
                "query": {"visibility": "friends", "sort": "popular", "limit": -1},
                "filters": None,
                "created_time": "2026-07-23T12:34:56+00:00",
                "downloaded_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
            },
            object(),
        )


def test_bookmarks_task_api_normalizes_query_filters_and_persists(monkeypatch):
    from src.pixiv_downloader import app as app_module

    captured = []

    class Tasks:
        def create_task(self, **task):
            captured.append(task)
            return 42

    monkeypatch.setattr(app_module, "task_manager", Tasks())
    response = app_module.app.test_client().post(
        "/api/tasks",
        json={
            "task_type": "bookmarks",
            "query": {
                "visibility": "private",
                "tag": " 风景 ",
                "sort": "artwork_date",
                "limit": None,
            },
            "filters": {
                "work_types": ["ugoira", "illust"],
                "r18_modes": ["r18", "general"],
                "min_bookmarks": 25,
            },
        },
    )

    assert response.status_code == 201
    assert response.get_json()["task_id"] == 42
    assert captured == [{
        "task_type": "bookmarks",
        "query": {
            "visibility": "private",
            "tag": "风景",
            "sort": "artwork_date",
            "limit": None,
        },
        "filters": {
            "work_types": ["illust", "ugoira"],
            "r18_modes": ["general", "r18"],
            "min_bookmarks": 25,
        },
    }]


def test_bookmark_tags_api_validates_visibility_and_returns_tags(monkeypatch):
    from src.pixiv_downloader import app as app_module

    captured = {}

    class Auth:
        api = object()

        def get_user_id(self):
            return 42

    class Downloader:
        def __init__(self, api, database, proxy, *, output_dir, user_id):
            captured["user_id"] = user_id

        def fetch_bookmark_tags(self, visibility):
            captured["visibility"] = visibility
            return ["风景", "原创"]

    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)

    response = app_module.app.test_client().get(
        "/api/bookmarks/tags?visibility=private",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "visibility": "private",
        "tags": ["风景", "原创"],
    }
    assert captured == {"user_id": 42, "visibility": "private"}

    invalid = app_module.app.test_client().get(
        "/api/bookmarks/tags?visibility=invalid"
    )
    assert invalid.status_code == 400

    unauthorized_private = app_module.app.test_client().get(
        "/api/bookmarks/tags?visibility=private"
    )
    assert unauthorized_private.status_code == 403
    authorized_private = app_module.app.test_client().get(
        "/api/bookmarks/tags?visibility=private",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )
    assert authorized_private.status_code == 200
    assert authorized_private.get_json()["tags"] == ["风景", "原创"]


def test_bookmarks_form_and_frontend_task_contract_are_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")

    for marker in (
        'id="bookmarks-download-form"',
        'id="bookmarks-visibility"',
        'value="public"',
        'value="private"',
        'id="bookmarks-tag"',
        'id="bookmarks-sort"',
        'value="bookmark_time"',
        'value="artwork_date"',
        'value="bookmarks"',
        'id="bookmarks-limit-mode"',
        'value="all"',
        'value="custom"',
        'id="bookmarks-limit"',
        'max="1000"',
        '全部（最多 5000 项）',
    ):
        assert marker in page
    for marker in (
        "bookmarks-download-form",
        "'/api/bookmarks/tags?visibility=",
        "task_type: 'bookmarks'",
        "limit: limitModeInput.value === 'all' ? null : customLimit",
        "task.task_type === 'bookmarks'",
        "新增 ${task.downloaded_count}",
        "已存在 ${task.skipped_count}",
        "X-App-Token",
        "app-token",
        "作品 ID 已隐藏",
    ):
        assert marker in script


def test_bookmarks_task_survives_sqlite_close_and_reopen(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    task_id = database.create_task(
        task_type="bookmarks",
        query={
            "visibility": "private",
            "tag": "风景",
            "sort": "bookmarks",
            "limit": None,
        },
        filters={
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 10,
        },
    )
    database.close()

    reopened = DatabaseManager(database_path)
    try:
        task = reopened.get_task(task_id)
        assert task["task_type"] == "bookmarks"
        assert task["query"] == {
            "visibility": "private",
            "tag": "风景",
            "sort": "bookmarks",
            "limit": None,
        }
        assert task["filters"]["min_bookmarks"] == 10
    finally:
        reopened.close()


def test_persisted_bookmarks_query_accepts_only_bounded_stable_snapshot():
    from src.pixiv_downloader.bookmarks import (
        BookmarksValidationError,
        normalize_persisted_bookmarks_query,
    )

    query = {
        "visibility": "private",
        "tag": None,
        "sort": "bookmark_time",
        "limit": None,
        "artwork_ids": [9, 1, 2],
    }
    assert normalize_persisted_bookmarks_query(query) == query
    for artwork_ids in ([1, 1], [0], [True], ["1"], list(range(1, 5002))):
        with pytest.raises(BookmarksValidationError, match="收藏条件无效"):
            normalize_persisted_bookmarks_query({**query, "artwork_ids": artwork_ids})


def test_bookmark_tag_pagination_tolerates_one_duplicate_page(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0
    pages = [
        {"bookmark_tags": [{"name": "a"}], "next_url": "https://x/?offset=30"},
        {"bookmark_tags": [{"name": "a"}], "next_url": "https://x/?offset=60"},
        {"bookmark_tags": [{"name": "b"}], "next_url": None},
    ]

    class API:
        def user_bookmark_tags_illust(self, **options):
            nonlocal calls
            calls += 1
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    assert downloader.fetch_bookmark_tags("public") == ["a", "b"]
    assert calls == 3


def test_bookmark_pagination_tolerates_one_duplicate_page(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    pages = [
        {"illusts": [artwork(1)], "next_url": "https://x/?max_bookmark_id=900"},
        {"illusts": [artwork(1)], "next_url": "https://x/?max_bookmark_id=800"},
        {"illusts": [artwork(2)], "next_url": None},
    ]

    class API:
        def user_bookmarks_illust(self, **options):
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)

    assert [item["id"] for item in downloader.fetch_bookmarks(
        "public", None, "bookmark_time", None
    )] == [1, 2]


@pytest.mark.asyncio
async def test_download_bookmarks_persists_snapshot_before_work_and_resumes_by_ids(
    tmp_path, monkeypatch
):
    from src.pixiv_downloader.downloader import ArtistDownloader

    downloader = ArtistDownloader(object(), None, Proxy(), output_dir=tmp_path, user_id=42)
    candidates = [artwork(9), artwork(1), artwork(2)]
    monkeypatch.setattr(downloader, "fetch_bookmarks", lambda *args, **kwargs: candidates)
    snapshots = []
    captured = {}

    async def download_items(items, resolve, **options):
        captured["items"] = list(items)
        captured["resolved"] = [resolve(item)["id"] for item in items]
        captured["start_index"] = options["start_index"]
        return {"total": len(items), "downloaded": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(downloader, "_download_items", download_items)
    await downloader.download_bookmarks(
        "public", None, "bookmark_time", None,
        snapshot_callback=snapshots.append,
    )
    assert snapshots == [[9, 1, 2]]
    assert captured == {
        "items": candidates,
        "resolved": [9, 1, 2],
        "start_index": 0,
    }

    class API:
        def user_bookmarks_illust(self, **options):
            raise AssertionError("恢复必须使用冻结快照，不得重新扫描动态收藏")

        def illust_detail(self, artwork_id):
            return {"illust": artwork(artwork_id)}

    resumed = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path, user_id=42)
    monkeypatch.setattr(resumed, "_download_items", download_items)
    await resumed.download_bookmarks(
        "public", None, "bookmark_time", None,
        artwork_ids=[9, 1, 2],
        start_index=1,
    )
    assert captured == {
        "items": [9, 1, 2],
        "resolved": [9, 1, 2],
        "start_index": 1,
    }


def test_database_bookmark_snapshot_is_atomic_and_immutable(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id = database.create_task(
            task_type="bookmarks",
            query={
                "visibility": "public", "tag": None,
                "sort": "bookmark_time", "limit": None,
            },
        )
        database.transition_task(task_id, "pending", "running")
        task = database.set_bookmark_snapshot(task_id, [9, 1, 2])
        assert task["query"]["artwork_ids"] == [9, 1, 2]
        assert database.set_bookmark_snapshot(task_id, [9, 1, 2])["query"] == task["query"]
        with pytest.raises(ValueError, match="快照"):
            database.set_bookmark_snapshot(task_id, [8, 1, 2])
    finally:
        database.close()


def test_task_api_redacts_private_bookmark_query_snapshot_and_failure_ids(monkeypatch):
    from src.pixiv_downloader import app as app_module

    private_task = {
        "task_id": 81,
        "task_type": "bookmarks",
        "query": {
            "visibility": "private",
            "tag": "secret-private-tag",
            "sort": "bookmark_time",
            "limit": None,
            "artwork_ids": [987654321],
        },
        "status": "paused",
    }

    class Manager:
        def list_tasks(self):
            return [private_task]

        def pause_task(self, task_id):
            return private_task

        def cancel_task(self, task_id):
            return private_task

    class Database:
        def get_task(self, task_id):
            return private_task

        def list_task_failures(self, task_id):
            return [{
                "failure_id": 1,
                "task_id": task_id,
                "artwork_id": 987654321,
                "category": "recoverable",
                "message": "下载失败",
            }]

    monkeypatch.setattr(app_module, "task_manager", Manager())
    monkeypatch.setattr(app_module, "database_manager", Database())
    client = app_module.app.test_client()

    listed = client.get("/api/tasks").get_json()
    detailed = client.get("/api/tasks/81").get_json()
    controlled = client.post("/api/tasks/81/pause").get_json()
    serialized = __import__("json").dumps(
        [listed, detailed, controlled], ensure_ascii=False
    )

    assert "secret-private-tag" not in serialized
    assert "987654321" not in serialized
    assert listed["tasks"][0]["query"]["tag"] is None
    assert listed["tasks"][0]["query"]["tag_redacted"] is True
    assert "artwork_id" not in detailed["failed_artworks"][0]


def test_public_bookmark_task_keeps_tag_but_hides_internal_snapshot(monkeypatch):
    from src.pixiv_downloader import app as app_module

    task = {
        "task_id": 82,
        "task_type": "bookmarks",
        "query": {
            "visibility": "public",
            "tag": "风景",
            "sort": "bookmark_time",
            "limit": 10,
            "artwork_ids": [123456],
        },
        "status": "running",
    }

    class Manager:
        def list_tasks(self):
            return [task]

    monkeypatch.setattr(app_module, "task_manager", Manager())
    payload = app_module.app.test_client().get("/api/tasks").get_json()

    assert payload["tasks"][0]["query"]["tag"] == "风景"
    assert "artwork_ids" not in payload["tasks"][0]["query"]


def test_retry_task_failure_detail_never_reexposes_private_source_artwork_id(
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module

    retry_task = {
        "task_id": 90,
        "task_type": "retry_failed",
        "query": {"source_task_id": 81, "artwork_ids": [987654321]},
        "status": "failed",
    }

    class Database:
        def get_task(self, task_id):
            return retry_task

        def list_task_failures(self, task_id):
            return [{
                "failure_id": 2,
                "task_id": task_id,
                "artwork_id": 987654321,
                "category": "recoverable",
                "message": "下载失败",
            }]

    monkeypatch.setattr(app_module, "database_manager", Database())
    payload = app_module.app.test_client().get("/api/tasks/90").get_json()
    serialized = __import__("json").dumps(payload, ensure_ascii=False)

    assert "987654321" not in serialized
    assert "artwork_ids" not in payload["task"]["query"]
    assert "artwork_id" not in payload["failed_artworks"][0]
