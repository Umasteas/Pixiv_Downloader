"""Issue #13 搜索查询、Pixiv 分页与安全目录的领域测试。"""
from datetime import date

import pytest


class Proxy:
    def aiohttp_options(self):
        return {}, {}


def artwork(artwork_id, bookmarks=0):
    return {
        "id": artwork_id,
        "type": "illust",
        "x_restrict": 0,
        "total_bookmarks": bookmarks,
        "title": f"作品 {artwork_id}",
        "user": {"id": 1, "name": "画师"},
    }


def test_normalize_search_query_applies_defaults_and_rejects_invalid_values():
    from src.pixiv_downloader.search import SearchValidationError, normalize_search_query

    assert normalize_search_query({"keyword": "  风景  "}) == {
        "keyword": "风景",
        "search_type": "tag_partial",
        "sort": "date",
        "limit": 500,
    }

    invalid = (
        None,
        {},
        {"keyword": " "},
        {"keyword": "风景", "search_type": "title"},
        {"keyword": "风景", "sort": "unknown"},
        {"keyword": "风景", "limit": True},
        {"keyword": "风景", "limit": 0},
        {"keyword": "风景", "limit": 10001},
        {"keyword": "风景", "extra": "bypass"},
    )
    for query in invalid:
        with pytest.raises(SearchValidationError):
            normalize_search_query(query)


def test_fetch_search_results_maps_exact_and_popular_and_stops_at_limit(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []

    class API:
        def search_illust(self, keyword, **options):
            calls.append((keyword, options))
            return {
                "illusts": [artwork(1, 5), artwork(2, 9), artwork(3, 1)],
                "next_url": "https://app-api.pixiv.net/v1/search/illust?offset=30&word=evil",
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_search_results("风景", "tag_exact", "popular", 2)

    assert [item["id"] for item in result] == [1, 2]
    assert calls == [
        (
            "风景",
            {
                "search_target": "exact_match_for_tags",
                "sort": "popular_desc",
            },
        )
    ]


def test_fetch_search_results_follows_offset_and_deduplicates_date_results(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []
    pages = [
        {
            "illusts": [artwork(1), artwork(2)],
            "next_url": "https://app-api.pixiv.net/v1/search/illust?word=evil&offset=30&sort=popular_desc",
        },
        {"illusts": [artwork(2), artwork(3)], "next_url": None},
    ]

    class API:
        def search_illust(self, keyword, **options):
            calls.append((keyword, options))
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_search_results("风景", "tag_partial", "date", 3)

    assert [item["id"] for item in result] == [1, 2, 3]
    assert calls == [
        (
            "风景",
            {
                "search_target": "partial_match_for_tags",
                "sort": "date_desc",
            },
        ),
        (
            "风景",
            {
                "search_target": "partial_match_for_tags",
                "sort": "date_desc",
                "offset": "30",
            },
        ),
    ]


def test_fetch_search_results_sorts_limited_candidates_by_bookmarks(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    pages = [
        {
            "illusts": [artwork(1, 10), artwork(2, 40)],
            "next_url": "https://app-api.pixiv.net/v1/search/illust?offset=30",
        },
        {"illusts": [artwork(3, 100), artwork(4, 20)], "next_url": None},
    ]

    class API:
        def search_illust(self, keyword, **options):
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_search_results("风景", "tag_partial", "bookmarks", 3)

    assert [item["id"] for item in result] == [3, 2, 1]


def test_fetch_search_results_bookmark_sort_does_not_scan_beyond_limit(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def search_illust(self, keyword, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [artwork(calls, calls)],
                "next_url": (
                    "https://app-api.pixiv.net/v1/search/illust"
                    f"?offset={calls * 30}"
                    if calls < 5
                    else None
                ),
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_search_results("风景", "tag_partial", "bookmarks", 1)

    assert [item["id"] for item in result] == [1]
    assert calls == 1


def test_fetch_search_results_stops_after_empty_page_with_next_offset(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def search_illust(self, keyword, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [],
                "next_url": (
                    "https://app-api.pixiv.net/v1/search/illust"
                    f"?offset={calls * 30}"
                    if calls < 5
                    else None
                ),
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    assert downloader.fetch_search_results("风景", "tag_partial", "date", 2) == []
    assert calls == 1


def test_fetch_search_results_stops_after_duplicate_only_page(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0
    pages = [
        {
            "illusts": [artwork(1)],
            "next_url": "https://app-api.pixiv.net/v1/search/illust?offset=30",
        },
        {
            "illusts": [artwork(1)],
            "next_url": "https://app-api.pixiv.net/v1/search/illust?offset=60",
        },
        {"illusts": [artwork(2)], "next_url": None},
    ]

    class API:
        def search_illust(self, keyword, **options):
            nonlocal calls
            calls += 1
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_search_results("风景", "tag_partial", "date", 2)

    assert [item["id"] for item in result] == [1]
    assert calls == 2


def test_create_search_directory_uses_safe_basename_and_fixed_task_date(tmp_path):
    from src.pixiv_downloader.file_organizer import create_search_directory

    directory = create_search_directory(tmp_path, "../风景:夜景", date(2026, 7, 23))

    assert directory == tmp_path / "search" / ".._风景_夜景_2026-07-23"
    assert directory.is_dir()
    assert directory.resolve().is_relative_to((tmp_path / "search").resolve())


def test_collection_single_image_rejects_overlong_windows_path(tmp_path):
    from src.pixiv_downloader.file_organizer import get_collection_save_path

    overlong_root = tmp_path / ("深" * 220)

    with pytest.raises(ValueError, match="输出目录过长"):
        get_collection_save_path(overlong_root, artwork(1))


@pytest.mark.asyncio
async def test_download_search_filters_and_writes_collection_metadata(tmp_path, monkeypatch):
    import json

    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.file_organizer import create_search_directory

    candidates = [
        {
            **artwork(1, 50),
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/1.jpg"},
            "tags": [{"name": "风景"}],
            "create_date": "2026-07-22T00:00:00+09:00",
        },
        {
            **artwork(2, 1),
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/2.jpg"},
        },
    ]

    class API:
        def search_illust(self, keyword, **options):
            return {"illusts": candidates, "next_url": None}

    class Database:
        def __init__(self):
            self.records = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **record):
            self.records.append((artwork_id, record))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    collection_dir = create_search_directory(tmp_path, "风景", date(2026, 7, 23))
    database = Database()
    downloader = ArtistDownloader(
        API(),
        database,
        Proxy(),
        output_dir=tmp_path,
        collection_dir=collection_dir,
        source="search",
        session_factory=lambda **options: Session(),
    )

    async def write_file(url, save_path, **options):
        save_path.write_bytes(b"image")

    monkeypatch.setattr(downloader, "download_file", write_file)

    result = await downloader.download_search(
        "风景",
        "tag_partial",
        "date",
        10,
        filters={
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 10,
        },
    )

    assert result == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0}
    image_path = collection_dir / "1_画师_作品 1.jpg"
    assert image_path.read_bytes() == b"image"
    metadata = json.loads(image_path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    assert metadata["download_info"]["source"] == "search"
    assert database.records[0][1]["file_path"] == str(image_path)


def test_execute_search_task_revalidates_query_and_uses_created_date(monkeypatch, tmp_path):
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
        ):
            captured["collection_dir"] = collection_dir
            captured["source"] = source

        async def download_search(self, keyword, search_type, sort, limit, **options):
            captured["query"] = (keyword, search_type, sort, limit)
            captured["options"] = options

    class Auth:
        api = object()

        def get_access_token(self):
            return "ok"

    class Config:
        def load_config(self):
            return {
                "download": {"output_dir": str(tmp_path)},
                "ugoira": {},
            }

    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)
    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "config_manager", Config())

    app_module._execute_download_task(
        {
            "task_id": 13,
            "task_type": "search",
            "query": {
                "keyword": "风景",
                "search_type": "tag_exact",
                "sort": "bookmarks",
                "limit": 100,
            },
            "filters": {
                "work_types": ["illust"],
                "r18_modes": ["general"],
                "min_bookmarks": 10,
            },
            "created_time": "2026-07-23 09:30:00",
            "downloaded_count": 2,
            "skipped_count": 1,
            "failed_count": 0,
        },
        object(),
    )

    assert captured["collection_dir"] == tmp_path / "search" / "风景_2026-07-23"
    assert captured["source"] == "search"
    assert captured["query"] == ("风景", "tag_exact", "bookmarks", 100)
    assert captured["options"]["start_index"] == 3
    assert captured["options"]["filters"]["min_bookmarks"] == 10


def test_execute_search_task_rejects_tampered_persisted_query(monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.task_manager import TaskManagerError

    with pytest.raises(TaskManagerError, match="搜索任务缺少有效查询"):
        app_module._execute_download_task(
            {
                "task_id": 13,
                "task_type": "search",
                "query": {"keyword": "风景", "sort": "bypass"},
                "filters": None,
                "created_time": "2026-07-23 09:30:00",
                "downloaded_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
            },
            object(),
        )


def test_search_task_api_normalizes_query_filters_and_persists_via_task_manager(
    monkeypatch,
):
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
            "task_type": "search",
            "query": {"keyword": "  风景  ", "search_type": "tag_exact"},
            "filters": {
                "work_types": ["ugoira", "illust"],
                "r18_modes": ["r18", "general"],
                "min_bookmarks": 25,
            },
        },
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "success": True,
        "task_id": 42,
        "status": "pending",
    }
    assert captured == [
        {
            "task_type": "search",
            "query": {
                "keyword": "风景",
                "search_type": "tag_exact",
                "sort": "date",
                "limit": 500,
            },
            "filters": {
                "work_types": ["illust", "ugoira"],
                "r18_modes": ["general", "r18"],
                "min_bookmarks": 25,
            },
        }
    ]


def test_search_task_api_rejects_invalid_query_or_filters_without_creating(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Tasks:
        def create_task(self, **task):
            raise AssertionError("无效搜索任务不得持久化")

    monkeypatch.setattr(app_module, "task_manager", Tasks())
    client = app_module.app.test_client()
    invalid_payloads = (
        {"task_type": "search", "query": {"keyword": " "}},
        {"task_type": "search", "query": {"keyword": "风景", "limit": 0}},
        {"task_type": "search", "query": {"keyword": "风景", "sort": "bypass"}},
        {
            "task_type": "search",
            "query": {"keyword": "风景"},
            "filters": {"work_types": [], "r18_modes": ["general"]},
        },
    )

    for payload in invalid_payloads:
        response = client.post("/api/tasks", json=payload)
        assert response.status_code == 400
        assert response.get_json() == {
            "success": False,
            "error": "搜索条件或筛选条件无效。",
        }


def test_search_task_survives_sqlite_close_and_reopen(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    task_id = database.create_task(
        task_type="search",
        query={
            "keyword": "风景",
            "search_type": "tag_partial",
            "sort": "bookmarks",
            "limit": 100,
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
        assert task["task_type"] == "search"
        assert task["query"] == {
            "keyword": "风景",
            "search_type": "tag_partial",
            "sort": "bookmarks",
            "limit": 100,
        }
        assert task["filters"]["min_bookmarks"] == 10
    finally:
        reopened.close()


def test_search_form_and_frontend_task_contract_are_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")

    for marker in (
        'id="search-download-form"',
        'id="search-keyword"',
        'id="search-type"',
        'value="tag_exact"',
        'value="tag_partial"',
        'id="search-sort"',
        'value="popular"',
        'value="date"',
        'value="bookmarks"',
        'id="search-limit"',
        'value="500"',
        'name="search-work-types"',
        'name="search-r18-modes"',
        'id="search-min-bookmarks"',
    ):
        assert marker in page
    for marker in (
        "search-download-form",
        "task_type: 'search'",
        "search_type:",
        "sort:",
        "limit,",
        "search-work-types",
        "search-r18-modes",
        "search-min-bookmarks",
        "`搜索 ${task.query?.keyword}`",
    ):
        assert marker in script
