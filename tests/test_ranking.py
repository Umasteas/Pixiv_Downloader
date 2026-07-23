"""Issue #14 排行榜 query 与 Pixiv mode 的领域测试。"""
from datetime import date, datetime, timedelta, timezone

import pytest


class Proxy:
    def aiohttp_options(self):
        return {}, {}


def artwork(artwork_id):
    return {
        "id": artwork_id,
        "type": "illust",
        "x_restrict": 0,
        "total_bookmarks": artwork_id,
        "title": f"作品 {artwork_id}",
        "user": {"id": 1, "name": "画师"},
    }


def jst_today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def test_normalize_ranking_query_applies_defaults_and_maps_modes():
    from src.pixiv_downloader.ranking import normalize_ranking_query, ranking_mode

    today = date(2026, 7, 23)
    assert normalize_ranking_query(
        {"ranking_type": "daily", "date": "2026-07-23"},
        today=today,
    ) == {
        "ranking_type": "daily",
        "date": "2026-07-23",
        "limit": 500,
    }
    assert {
        ranking_type: ranking_mode(ranking_type)
        for ranking_type in (
            "daily",
            "daily_illust",
            "daily_ugoira",
            "weekly",
            "monthly",
            "rookie",
            "daily_manga",
            "daily_r18",
            "weekly_r18",
            "weekly_r18g",
        )
    } == {
        "daily": "day",
        "daily_illust": "day",
        "daily_ugoira": "day",
        "weekly": "week",
        "monthly": "month",
        "rookie": "week_rookie",
        "daily_manga": "day_manga",
        "daily_r18": "day_r18",
        "weekly_r18": "week_r18",
        "weekly_r18g": "week_r18g",
    }


def test_ranking_content_aliases_have_fixed_work_types():
    from src.pixiv_downloader.ranking import ranking_work_type

    assert ranking_work_type("daily") is None
    assert ranking_work_type("daily_illust") == "illust"
    assert ranking_work_type("daily_ugoira") == "ugoira"
    assert ranking_work_type("daily_manga") == "manga"


def test_normalize_ranking_query_accepts_the_30_day_window_boundaries():
    from src.pixiv_downloader.ranking import normalize_ranking_query

    today = date(2026, 7, 23)
    assert normalize_ranking_query(
        {"ranking_type": "weekly", "date": (today - timedelta(days=29)).isoformat(), "limit": 1},
        today=today,
    )["limit"] == 1
    assert normalize_ranking_query(
        {"ranking_type": "monthly", "date": today.isoformat(), "limit": 500},
        today=today,
    )["limit"] == 500


@pytest.mark.parametrize(
    "query",
    [
        None,
        {},
        {"ranking_type": "unknown", "date": "2026-07-23"},
        {"ranking_type": "daily", "date": "2026/07/23"},
        {"ranking_type": "daily", "date": "2026-07-24"},
        {"ranking_type": "daily", "date": "2026-06-23"},
        {"ranking_type": "daily", "date": "2026-07-23", "limit": True},
        {"ranking_type": "daily", "date": "2026-07-23", "limit": "50"},
        {"ranking_type": "daily", "date": "2026-07-23", "limit": 0},
        {"ranking_type": "daily", "date": "2026-07-23", "limit": 501},
        {"ranking_type": "daily", "date": "2026-07-23", "extra": "bypass"},
    ],
)
def test_normalize_ranking_query_rejects_invalid_values(query):
    from src.pixiv_downloader.ranking import RankingValidationError, normalize_ranking_query

    with pytest.raises(RankingValidationError):
        normalize_ranking_query(query, today=date(2026, 7, 23))


def test_fetch_ranking_maps_mode_date_filter_and_stops_at_limit(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []

    class API:
        def illust_ranking(self, **options):
            calls.append(options)
            return {
                "illusts": [artwork(1), artwork(2), artwork(3)],
                "next_url": "https://app-api.pixiv.net/v1/illust/ranking?offset=30",
            }

    today = jst_today().isoformat()
    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_ranking("daily", today, 2)

    assert [item["id"] for item in result] == [1, 2]
    assert calls == [{"mode": "day", "filter": "for_ios", "date": today}]


def test_fetch_ranking_follows_only_offset_and_deduplicates(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []
    pages = [
        {
            "illusts": [artwork(1), artwork(2)],
            "next_url": (
                "https://app-api.pixiv.net/v1/illust/ranking"
                "?mode=day_r18&date=1999-01-01&filter=evil&offset=30&token=secret"
            ),
        },
        {"illusts": [artwork(2), artwork(3)], "next_url": None},
    ]

    class API:
        def illust_ranking(self, **options):
            calls.append(options)
            return pages.pop(0)

    today = jst_today().isoformat()
    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_ranking("weekly", today, 3)

    assert [item["id"] for item in result] == [1, 2, 3]
    assert calls == [
        {"mode": "week", "filter": "for_ios", "date": today},
        {"mode": "week", "filter": "for_ios", "date": today, "offset": 30},
    ]


@pytest.mark.parametrize(
    "pages, expected_calls",
    [
        ([{"illusts": [], "next_url": "https://x.invalid/?offset=30"}], 1),
        (
            [
                {"illusts": [artwork(1)], "next_url": "https://x.invalid/?offset=30"},
                {"illusts": [artwork(1)], "next_url": "https://x.invalid/?offset=60"},
            ],
            2,
        ),
        (
            [
                {"illusts": [artwork(1)], "next_url": "https://x.invalid/?offset=30"},
                {"illusts": [artwork(2)], "next_url": "https://x.invalid/?offset=30"},
            ],
            2,
        ),
        ([{"illusts": [artwork(1)], "next_url": "https://x.invalid/?offset=1000001"}], 1),
        ([{"illusts": [artwork(1)], "next_url": f"https://x.invalid/?offset={'9' * 4301}"}], 1),
    ],
)
def test_fetch_ranking_stops_on_no_progress_or_unsafe_offset(
    tmp_path,
    pages,
    expected_calls,
):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def illust_ranking(self, **options):
            nonlocal calls
            calls += 1
            return pages.pop(0)

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    downloader.fetch_ranking("daily", jst_today().isoformat(), 3)

    assert calls == expected_calls


def test_fetch_ranking_skips_invalid_external_artwork_ids(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    class API:
        def illust_ranking(self, **options):
            return {
                "illusts": [
                    {"id": None},
                    {"id": "not-an-int"},
                    {"id": -1},
                    {"id": True},
                    artwork(2),
                ],
                "next_url": None,
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_ranking("daily", jst_today().isoformat(), 10)

    assert [item["id"] for item in result] == [2]


def test_fetch_ranking_checks_cancellation_before_each_page(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def illust_ranking(self, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [artwork(calls)],
                "next_url": f"https://x.invalid/?offset={calls * 30}",
            }

    class Control:
        checkpoints = 0

        def checkpoint(self):
            self.checkpoints += 1
            return self.checkpoints == 1

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_ranking(
        "daily",
        jst_today().isoformat(),
        10,
        control=Control(),
    )

    assert [item["id"] for item in result] == [1]
    assert calls == 1


def test_fetch_ranking_request_count_is_bounded_by_limit(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = 0

    class API:
        def illust_ranking(self, **options):
            nonlocal calls
            calls += 1
            return {
                "illusts": [artwork(calls)],
                "next_url": f"https://x.invalid/?offset={calls * 30}",
            }

    downloader = ArtistDownloader(API(), None, Proxy(), output_dir=tmp_path)

    result = downloader.fetch_ranking("daily", jst_today().isoformat(), 500)

    assert len(result) == 100
    assert calls == 100


def test_create_ranking_directory_uses_fixed_type_and_date(tmp_path):
    from src.pixiv_downloader.file_organizer import create_ranking_directory

    directory = create_ranking_directory(tmp_path, "daily_r18", date(2026, 7, 23))

    assert directory == tmp_path / "ranking" / "daily_r18_2026-07-23"
    assert directory.is_dir()
    assert directory.resolve().is_relative_to((tmp_path / "ranking").resolve())


@pytest.mark.asyncio
async def test_download_ranking_filters_and_writes_collection_metadata(tmp_path, monkeypatch):
    import json

    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.file_organizer import create_ranking_directory

    candidates = [
        {
            **artwork(1),
            "total_bookmarks": 50,
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/1.jpg"},
            "tags": [{"name": "风景"}],
            "create_date": "2026-07-22T00:00:00+09:00",
        },
        {
            **artwork(2),
            "total_bookmarks": 1,
            "page_count": 1,
            "meta_single_page": {"original_image_url": "https://i.pximg.net/2.jpg"},
        },
    ]

    class API:
        def illust_ranking(self, **options):
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

    today = jst_today().isoformat()
    collection_dir = create_ranking_directory(tmp_path, "daily", date.fromisoformat(today))
    database = Database()
    downloader = ArtistDownloader(
        API(),
        database,
        Proxy(),
        output_dir=tmp_path,
        collection_dir=collection_dir,
        source="ranking",
        metadata_context={"ranking_type": "daily", "ranking_date": today},
        session_factory=lambda **options: Session(),
    )

    async def write_file(url, save_path, **options):
        save_path.write_bytes(b"image")

    monkeypatch.setattr(downloader, "download_file", write_file)

    result = await downloader.download_ranking(
        "daily",
        today,
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
    assert metadata["download_info"]["source"] == "ranking"
    assert metadata["download_info"]["ranking_type"] == "daily"
    assert metadata["download_info"]["ranking_date"] == today
    assert database.records[0][1]["file_path"] == str(image_path)


@pytest.mark.asyncio
async def test_download_ranking_applies_content_specific_alias(monkeypatch, tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    candidates = [
        {**artwork(1), "type": "illust"},
        {**artwork(2), "type": "manga"},
        {**artwork(3), "type": "ugoira"},
    ]
    captured = {}
    downloader = ArtistDownloader(object(), None, Proxy(), output_dir=tmp_path)
    monkeypatch.setattr(downloader, "fetch_ranking", lambda *args: candidates)

    async def download_items(items, resolve, **options):
        captured["ids"] = [item["id"] for item in items]
        return {"total": len(items), "downloaded": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(downloader, "_download_items", download_items)

    await downloader.download_ranking(
        "daily_ugoira",
        jst_today().isoformat(),
        10,
        filters={
            "work_types": ["illust", "manga", "ugoira"],
            "r18_modes": ["general"],
            "min_bookmarks": 0,
        },
    )

    assert captured["ids"] == [3]


def test_execute_ranking_task_revalidates_query_and_uses_ranking_date(monkeypatch, tmp_path):
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
        ):
            captured["collection_dir"] = collection_dir
            captured["source"] = source
            captured["metadata_context"] = metadata_context

        async def download_ranking(self, ranking_type, ranking_date, limit, **options):
            captured["query"] = (ranking_type, ranking_date, limit)
            captured["options"] = options

    class Auth:
        api = object()

        def get_access_token(self):
            return "ok"

    class Config:
        def load_config(self):
            return {"download": {"output_dir": str(tmp_path)}, "ugoira": {}}

    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)
    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "config_manager", Config())
    today = jst_today().isoformat()

    app_module._execute_download_task(
        {
            "task_id": 14,
            "task_type": "ranking",
            "query": {"ranking_type": "daily_r18", "date": today, "limit": 100},
            "filters": {
                "work_types": ["illust"],
                "r18_modes": ["r18"],
                "min_bookmarks": 10,
            },
            "downloaded_count": 2,
            "skipped_count": 1,
            "failed_count": 1,
        },
        object(),
    )

    assert captured["collection_dir"] == tmp_path / "ranking" / f"daily_r18_{today}"
    assert captured["source"] == "ranking"
    assert captured["metadata_context"] == {
        "ranking_type": "daily_r18",
        "ranking_date": today,
    }
    assert captured["query"] == ("daily_r18", today, 100)
    assert captured["options"]["start_index"] == 4
    assert captured["options"]["filters"]["min_bookmarks"] == 10


def test_execute_ranking_task_rejects_tampered_persisted_query():
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.task_manager import TaskManagerError

    with pytest.raises(TaskManagerError, match="排行榜任务缺少有效查询"):
        app_module._execute_download_task(
            {
                "task_id": 14,
                "task_type": "ranking",
                "query": {"ranking_type": "daily", "date": "1999-01-01"},
                "filters": None,
                "downloaded_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
            },
            object(),
        )


def test_ranking_task_api_normalizes_query_filters_and_persists(monkeypatch):
    from src.pixiv_downloader import app as app_module

    captured = []

    class Tasks:
        def create_task(self, **task):
            captured.append(task)
            return 42

    monkeypatch.setattr(app_module, "task_manager", Tasks())
    today = jst_today().isoformat()
    response = app_module.app.test_client().post(
        "/api/tasks",
        json={
            "task_type": "ranking",
            "query": {"ranking_type": "weekly", "date": today, "limit": 100},
            "filters": {
                "work_types": ["ugoira", "illust"],
                "r18_modes": ["r18", "general"],
                "min_bookmarks": 25,
            },
        },
    )

    assert response.status_code == 201
    assert response.get_json()["task_id"] == 42
    assert captured == [
        {
            "task_type": "ranking",
            "query": {"ranking_type": "weekly", "date": today, "limit": 100},
            "filters": {
                "work_types": ["illust", "ugoira"],
                "r18_modes": ["general", "r18"],
                "min_bookmarks": 25,
            },
        }
    ]


def test_ranking_task_api_rejects_invalid_payload_without_creating(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Tasks:
        def create_task(self, **task):
            raise AssertionError("无效排行榜任务不得持久化")

    monkeypatch.setattr(app_module, "task_manager", Tasks())
    response = app_module.app.test_client().post(
        "/api/tasks",
        json={
            "task_type": "ranking",
            "query": {"ranking_type": "daily", "date": "1999-01-01", "limit": 501},
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "error": "排行榜条件或筛选条件无效。",
    }


def test_ranking_task_survives_sqlite_close_and_reopen(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    today = jst_today().isoformat()
    task_id = database.create_task(
        task_type="ranking",
        query={"ranking_type": "monthly", "date": today, "limit": 200},
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
        assert task["task_type"] == "ranking"
        assert task["query"] == {"ranking_type": "monthly", "date": today, "limit": 200}
        assert task["filters"]["min_bookmarks"] == 10
    finally:
        reopened.close()


def test_ranking_form_and_frontend_task_contract_are_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")

    for marker in (
        'id="ranking-download-form"',
        'id="ranking-type"',
        'value="daily"',
        'value="daily_illust"',
        'value="daily_ugoira"',
        'value="weekly"',
        'value="monthly"',
        'value="rookie"',
        'value="daily_manga"',
        'value="daily_r18"',
        'value="weekly_r18"',
        'value="weekly_r18g"',
        'id="ranking-date"',
        'id="ranking-limit"',
        'max="500"',
        'value="500"',
        'name="ranking-work-types"',
        'name="ranking-r18-modes"',
        'id="ranking-min-bookmarks"',
    ):
        assert marker in page
    for marker in (
        "ranking-download-form",
        "task_type: 'ranking'",
        "ranking_type:",
        "ranking-date",
        "ranking-limit",
        "ranking-work-types",
        "ranking-r18-modes",
        "ranking-min-bookmarks",
        "JST_OFFSET_MS",
        "RANKING_WINDOW_DAYS",
        "`排行榜 ${rankingLabels[task.query?.ranking_type]",
    ):
        assert marker in script
