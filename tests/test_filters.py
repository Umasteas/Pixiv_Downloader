"""作品基础筛选条件的领域测试。"""
import pytest


def artwork(work_type, r18_mode, bookmarks):
    return {
        "id": bookmarks,
        "type": work_type,
        "x_restrict": r18_mode,
        "total_bookmarks": bookmarks,
    }


def test_filter_artworks_applies_type_rating_and_minimum_bookmarks_together():
    from src.pixiv_downloader.filters import filter_artworks

    candidates = [
        artwork("illust", 0, 500),
        artwork("manga", 0, 700),
        artwork("illust", 1, 900),
        artwork("illust", 0, 499),
    ]

    selected = filter_artworks(
        candidates,
        {
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 500,
        },
    )

    assert [item["total_bookmarks"] for item in selected] == [500]


def test_filter_artworks_excludes_missing_and_unknown_ratings():
    from src.pixiv_downloader.filters import filter_artworks

    missing_rating = {"type": "illust", "total_bookmarks": 500}
    unknown_rating = {
        "type": "illust",
        "x_restrict": 99,
        "total_bookmarks": 500,
    }

    selected = filter_artworks(
        [missing_rating, unknown_rating],
        {
            "work_types": ["illust"],
            "r18_modes": ["general", "r18", "r18g"],
            "min_bookmarks": 0,
        },
    )

    assert selected == []


@pytest.mark.parametrize("empty_field", ["work_types", "r18_modes"])
def test_filter_artworks_rejects_empty_filter_collections(empty_field):
    from src.pixiv_downloader.filters import FilterValidationError, filter_artworks

    filters = {
        "work_types": ["illust"],
        "r18_modes": ["general"],
        "min_bookmarks": 0,
    }
    filters[empty_field] = []

    with pytest.raises(FilterValidationError):
        filter_artworks([artwork("illust", 0, 500)], filters)


def test_create_task_api_persists_normalized_filters(monkeypatch):
    from src.pixiv_downloader import app as app_module

    captured = {}

    class Manager:
        def create_task(self, **task):
            captured.update(task)
            return 42

    monkeypatch.setattr(app_module, "task_manager", Manager())

    response = app_module.app.test_client().post(
        "/api/tasks",
        json={
            "task_type": "artist",
            "query": {"artist_id": 123},
            "filters": {
                "work_types": ["ugoira", "illust", "illust"],
                "r18_modes": ["r18", "general"],
                "min_bookmarks": 500,
            },
        },
    )

    assert response.status_code == 201
    assert captured == {
        "task_type": "artist",
        "query": {"artist_id": 123},
        "filters": {
            "work_types": ["illust", "ugoira"],
            "r18_modes": ["general", "r18"],
            "min_bookmarks": 500,
        },
    }


def test_execute_task_passes_persisted_filters_to_artist_downloader(
    monkeypatch,
    tmp_path,
):
    from src.pixiv_downloader import app as app_module

    captured = {}
    filters = {
        "work_types": ["manga"],
        "r18_modes": ["general"],
        "min_bookmarks": 100,
    }

    class Downloader:
        def __init__(self, *args, **kwargs):
            pass

        async def download_artist(self, artist_id, **options):
            captured.update(options)

    monkeypatch.setattr(app_module, "ArtistDownloader", Downloader)
    monkeypatch.setattr(app_module.auth_manager, "get_access_token", lambda: "token")
    monkeypatch.setattr(app_module.auth_manager, "api", object())
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(tmp_path)}},
    )

    app_module._execute_download_task(
        {
            "task_id": 42,
            "task_type": "artist",
            "query": {"artist_id": 123},
            "filters": filters,
            "downloaded_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        },
        object(),
    )

    assert captured["filters"] == filters


def test_execute_task_rejects_invalid_persisted_filters_and_marks_task_failed(tmp_path):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.filters import FilterValidationError
    from src.pixiv_downloader.task_manager import TaskManager

    database = DatabaseManager(tmp_path / "history.db")
    manager = TaskManager(database, app_module._execute_download_task)
    try:
        task_id = manager.create_task(
            task_type="artist",
            query={"artist_id": 123},
            filters={
                "work_types": [],
                "r18_modes": ["general"],
                "min_bookmarks": 0,
            },
        )

        with pytest.raises(FilterValidationError):
            manager.start_task(task_id)

        assert database.get_task(task_id)["status"] == "failed"
    finally:
        database.close()


def test_restarted_persisted_artist_task_filters_before_resumed_scheduling(
    monkeypatch,
    tmp_path,
):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.task_manager import TaskManager

    candidates = [
        artwork("illust", 0, 500),
        artwork("manga", 0, 900),
        artwork("illust", 1, 900),
        artwork("illust", 0, 499),
    ]

    class Api:
        def user_illusts(self, artist_id, **options):
            return {"illusts": candidates, "next_url": None}

    captured = {}

    async def capture_scheduled(self, items, resolve, **options):
        captured["items"] = items
        captured["start_index"] = options["start_index"]
        options["control"].set_total(len(items))
        return {"total": len(items), "downloaded": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(ArtistDownloader, "_download_items", capture_scheduled)
    monkeypatch.setattr(app_module.auth_manager, "get_access_token", lambda: "token")
    monkeypatch.setattr(app_module.auth_manager, "api", Api())
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(tmp_path / "downloads")}},
    )

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    task_id = database.create_task(
        task_type="artist",
        query={"artist_id": 123},
        filters={
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 500,
        },
    )
    database.transition_task(task_id, "pending", "running")
    database.close()

    reopened = DatabaseManager(database_path)
    monkeypatch.setattr(app_module, "database_manager", reopened)
    try:
        manager = TaskManager(reopened, app_module._execute_download_task)

        assert reopened.get_task(task_id)["status"] == "paused"
        completed = manager.resume_task(task_id)

        assert reopened.get_task(task_id)["filters"] == {
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 500,
        }
        assert captured == {"items": [candidates[0]], "start_index": 0}
        assert completed["status"] == "completed"
        assert completed["total_count"] == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_download_artist_filters_before_scheduling(monkeypatch):
    from src.pixiv_downloader.downloader import ArtistDownloader

    candidates = [
        artwork("illust", 0, 500),
        artwork("manga", 0, 900),
    ]

    class Api:
        def user_illusts(self, artist_id, **options):
            return {"illusts": candidates, "next_url": None}

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    downloader = ArtistDownloader(Api(), object(), Proxy())
    captured = {}

    async def capture(items, resolve, **options):
        captured["items"] = items
        return {"total": len(items), "downloaded": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(downloader, "_download_items", capture)

    result = await downloader.download_artist(
        123,
        filters={
            "work_types": ["illust"],
            "r18_modes": ["general"],
            "min_bookmarks": 500,
        },
    )

    assert captured["items"] == [candidates[0]]
    assert result["total"] == 1


def test_artist_form_submits_filters_and_task_card_renders_summary():
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    page = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    for value in ("illust", "manga", "ugoira"):
        assert f'name="work-types" value="{value}"' in page
    for value in ("general", "r18", "r18g"):
        assert f'name="r18-modes" value="{value}"' in page
    assert 'id="min-bookmarks"' in page
    for marker in (
        "work_types:",
        "r18_modes:",
        "min_bookmarks:",
        "formatFilterSummary",
        "if (!workTypes.length || !r18Modes.length)",
        "请至少选择一种作品类型和一个分级。",
    ):
        assert marker in script


def test_create_task_api_rejects_unknown_filter_values(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Manager:
        def create_task(self, **task):
            raise AssertionError("非法筛选条件不得创建任务")

    monkeypatch.setattr(app_module, "task_manager", Manager())

    response = app_module.app.test_client().post(
        "/api/tasks",
        json={
            "task_type": "artist",
            "query": {"artist_id": 123},
            "filters": {
                "work_types": ["novel"],
                "r18_modes": ["general"],
                "min_bookmarks": 0,
            },
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "error": "筛选条件无效。",
    }
