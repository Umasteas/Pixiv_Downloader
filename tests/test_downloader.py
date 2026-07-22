"""画师单图下载的领域与集成测试。"""
import json
from pathlib import Path

import pytest


def artwork(**overrides):
    value = {
        "id": 123,
        "title": '标题:/\\*?"<>|',
        "page_count": 1,
        "user": {"id": 456, "name": "画师:名称"},
        "tags": [{"name": "风景"}, {"name": "原创"}],
        "total_bookmarks": 789,
        "create_date": "2026-07-21T12:00:00+09:00",
        "meta_single_page": {
            "original_image_url": "https://i.pximg.net/img-original/example.jpg"
        },
    }
    value.update(overrides)
    return value


class FakeResponse:
    status = 200

    def __init__(self, payload=b"jpeg-bytes"):
        self.payload = payload
        self.content = self
        self.content_length = len(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    async def iter_chunked(self, size):
        yield self.payload[:4]
        yield self.payload[4:]


class FakeSession:
    instances = []

    def __init__(self, **kwargs):
        self.session_kwargs = kwargs
        self.get_calls = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse()


@pytest.mark.asyncio
async def test_download_file_reports_streaming_byte_progress(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    FakeSession.instances.clear()
    updates = []
    downloader = ArtistDownloader(
        api=object(),
        database=object(),
        proxy_manager=Proxy(),
        output_dir=tmp_path,
        session_factory=FakeSession,
    )
    save_path = tmp_path / "作品.jpg"

    await downloader.download_file(
        "https://i.pximg.net/example.jpg",
        save_path,
        progress=lambda filename, downloaded, total, complete: updates.append(
            (filename, downloaded, total, complete)
        ),
    )

    assert updates == [
        ("作品.jpg", 0, 10, False),
        ("作品.jpg", 4, 10, False),
        ("作品.jpg", 10, 10, False),
        ("作品.jpg", 10, 10, True),
    ]
    assert save_path.read_bytes() == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_artist_download_integration_uses_proxy_metadata_and_dedup(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    single = artwork()
    duplicate = artwork(id=124, title="已下载")
    multiple = artwork(id=125, page_count=2)

    class FakeApi:
        def __init__(self):
            self.calls = []

        def user_illusts(self, artist_id, **kwargs):
            self.calls.append((artist_id, kwargs))
            return {"illusts": [single, duplicate, multiple], "next_url": None}

    class FakeDatabase:
        def __init__(self):
            self.mark_calls = []

        def is_downloaded(self, artwork_id):
            return artwork_id == 124

        def mark_as_downloaded(self, artwork_id, **kwargs):
            self.mark_calls.append((artwork_id, kwargs))

    class FakeProxyManager:
        def __init__(self):
            self.calls = 0

        def aiohttp_options(self):
            self.calls += 1
            return {"trust_env": False}, {"proxy": "http://proxy.test:8080"}

    FakeSession.instances.clear()
    api = FakeApi()
    database = FakeDatabase()
    proxy = FakeProxyManager()
    downloader = ArtistDownloader(
        api,
        database,
        proxy,
        output_dir=tmp_path,
        session_factory=FakeSession,
    )

    result = await downloader.download_artist(456)

    assert result == {"total": 3, "downloaded": 1, "skipped": 2, "failed": 0}
    assert proxy.calls == 1
    assert api.calls == [(456, {})]
    assert len(FakeSession.instances) == 1
    session = FakeSession.instances[0]
    assert session.session_kwargs == {"trust_env": False}
    assert session.get_calls == [
        (
            "https://i.pximg.net/img-original/example.jpg",
            {
                "proxy": "http://proxy.test:8080",
                "headers": {"Referer": "https://app-api.pixiv.net/"},
            },
        )
    ]

    image_path = tmp_path / "artists" / "画师_名称_456" / "123_画师_名称_标题_________.jpg"
    metadata_path = image_path.with_suffix(".metadata.json")
    assert image_path.read_bytes() == b"jpeg-bytes"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["artwork_id"] == 123
    assert metadata["title"] == '标题:/\\*?"<>|'
    assert metadata["artist"] == {"id": 456, "name": "画师:名称"}
    assert metadata["tags"] == ["风景", "原创"]
    assert metadata["bookmarks"] == 789
    assert metadata["create_date"] == "2026-07-21T12:00:00+09:00"
    assert metadata["download_info"]["source"] == "artist"
    assert metadata["download_info"]["file_path"] == str(image_path)
    assert database.mark_calls[0][0] == 123
    assert database.mark_calls[0][1]["file_path"] == str(image_path)
    assert database.mark_calls[0][1]["file_size"] == len(b"jpeg-bytes")


def test_fetch_artist_artworks_follows_pixiv_pagination_without_duplicate_user_id(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    calls = []

    class PaginatedApi:
        def user_illusts(self, user_id, **kwargs):
            calls.append((user_id, kwargs))
            if len(calls) == 1:
                return {
                    "illusts": [artwork(id=1)],
                    "next_url": (
                        "https://app-api.pixiv.net/v1/user/illusts"
                        "?user_id=456&type=illust&filter=for_ios&offset=30"
                    ),
                }
            return {"illusts": [artwork(id=2)], "next_url": None}

    downloader = ArtistDownloader(
        PaginatedApi(),
        database=object(),
        proxy_manager=object(),
        output_dir=tmp_path,
    )

    result = downloader.fetch_artist_artworks(456)

    assert [item["id"] for item in result] == [1, 2]
    assert calls == [
        (456, {}),
        (456, {"type": "illust", "filter": "for_ios", "offset": "30"}),
    ]


def test_artist_download_form_and_api(monkeypatch):
    from src.pixiv_downloader import app as app_module

    calls = []

    class StubAuth:
        api = object()

        def get_access_token(self):
            raise AssertionError("创建 pending 任务时不应认证或下载")

        def has_saved_token(self):
            return True

    class Manager:
        def create_task(self, **task):
            calls.append(task)
            return 77

    monkeypatch.setattr(app_module, "auth_manager", StubAuth())
    monkeypatch.setattr(app_module, "task_manager", Manager())
    client = app_module.app.test_client()

    invalid = client.post("/api/download/artist", json={"artist_id": 0})
    float_id = client.post("/api/download/artist", json={"artist_id": 1.5})
    response = client.post("/api/download/artist", json={"artist_id": 456})
    page = client.get(f"/?token={app_module.SECURITY_TOKEN}").get_data(as_text=True)
    script = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert invalid.status_code == 400
    assert float_id.status_code == 400
    assert response.status_code == 201
    assert response.get_json() == {
        "success": True,
        "task_id": 77,
        "status": "pending",
    }
    assert calls == [{"task_type": "artist", "query": {"artist_id": 456}}]
    assert 'id="artist-download-form"' in page
    assert 'id="artist-id"' in page
    assert "/api/tasks" in script


@pytest.mark.asyncio
async def test_artist_download_resumes_from_checkpoint_and_records_task_progress(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    class Api:
        def user_illusts(self, artist_id, **kwargs):
            return {
                "illusts": [artwork(id=1), artwork(id=2), artwork(id=3)],
                "next_url": None,
            }

    class Database:
        def __init__(self):
            self.mark_calls = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **kwargs):
            self.mark_calls.append((artwork_id, kwargs))

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Control:
        def __init__(self):
            self.checkpoints = 0
            self.results = []
            self.totals = []
            self.file_updates = []

        def set_total(self, total):
            self.totals.append(total)

        def checkpoint(self):
            self.checkpoints += 1
            return self.checkpoints == 1

        def record_result(self, result):
            self.results.append(result)

        def file_progress(self, filename, downloaded, total, complete):
            self.file_updates.append((filename, downloaded, total, complete))

    database = Database()
    control = Control()
    downloader = ArtistDownloader(
        Api(),
        database,
        Proxy(),
        output_dir=tmp_path,
        session_factory=FakeSession,
    )

    result = await downloader.download_artist(
        456,
        task_id=77,
        start_index=1,
        control=control,
    )

    assert result == {"total": 3, "downloaded": 1, "skipped": 0, "failed": 0}
    assert control.totals == [3]
    assert control.checkpoints == 2
    assert control.results == ["downloaded"]
    assert [call[0] for call in database.mark_calls] == [2]
    assert database.mark_calls[0][1]["task_id"] == 77
    assert [update[1:] for update in control.file_updates] == [
        (0, 10, False),
        (4, 10, False),
        (10, 10, False),
        (10, 10, True),
    ]
    assert all(update[0].endswith(".jpg") for update in control.file_updates)


@pytest.mark.asyncio
async def test_selected_artwork_task_resumes_and_records_progress(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader

    class Api:
        def __init__(self):
            self.calls = []

        def illust_detail(self, artwork_id):
            self.calls.append(artwork_id)
            return {"illust": artwork(id=artwork_id)}

    class Database:
        def __init__(self):
            self.mark_calls = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **kwargs):
            self.mark_calls.append((artwork_id, kwargs))

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Control:
        def __init__(self):
            self.total = None
            self.results = []

        def set_total(self, total):
            self.total = total

        def checkpoint(self):
            return True

        def record_result(self, result):
            self.results.append(result)

    api = Api()
    database = Database()
    control = Control()
    downloader = ArtistDownloader(
        api,
        database,
        Proxy(),
        output_dir=tmp_path,
        session_factory=FakeSession,
    )

    result = await downloader.download_artwork_ids(
        [10, 20, 30],
        task_id=88,
        start_index=1,
        control=control,
    )

    assert result == {"total": 3, "downloaded": 2, "skipped": 0, "failed": 0}
    assert api.calls == [20, 30]
    assert control.total == 3
    assert control.results == ["downloaded", "downloaded"]
    assert [call[0] for call in database.mark_calls] == [20, 30]
    assert all(call[1]["task_id"] == 88 for call in database.mark_calls)


@pytest.mark.asyncio
async def test_download_artwork_requires_original_url_and_never_marks_fallback(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader, DownloadError

    candidate = artwork(
        meta_single_page={},
        image_urls={"large": "https://i.pximg.net/img-master/not-original.jpg"},
    )

    class Database:
        def __init__(self):
            self.mark_calls = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, *args, **kwargs):
            self.mark_calls.append((args, kwargs))

    database = Database()
    downloader = ArtistDownloader(
        api=object(),
        database=database,
        proxy_manager=object(),
        output_dir=tmp_path,
    )

    with pytest.raises(DownloadError, match="原图"):
        await downloader.download_artwork(candidate)

    assert database.mark_calls == []
    assert list(tmp_path.rglob("*.jpg")) == []
    assert list(tmp_path.rglob("*.metadata.json")) == []


def test_task_start_redacts_background_authentication_errors(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Manager:
        def start_task(self, task_id):
            raise app_module.AuthError(
                "Token 验证失败: https://alice:secret-password@proxy.local"
            )

    monkeypatch.setattr(app_module, "task_manager", Manager())
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda target, *args: target(*args),
    )
    response = app_module.app.test_client().post("/api/tasks/1/start")
    payload = response.get_json()

    assert response.status_code == 202
    assert payload == {"success": True, "status": "accepted"}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "alice" not in serialized
    assert "secret-password" not in serialized
    assert "proxy.local" not in serialized


def test_file_organization_and_safe_naming(tmp_path):
    from src.pixiv_downloader.file_organizer import (
        create_directory_structure,
        get_save_path,
        metadata_path_for,
    )

    artist_dir = create_directory_structure(tmp_path, "画师:名称", 456)
    save_path = get_save_path(tmp_path, artwork())

    assert artist_dir == tmp_path / "artists" / "画师_名称_456"
    assert artist_dir.is_dir()
    assert save_path == artist_dir / "123_画师_名称_标题_________.jpg"
    assert metadata_path_for(save_path) == artist_dir / "123_画师_名称_标题_________.metadata.json"
    assert not any(character in save_path.name for character in '/\\:*?"<>|')

    control_path = get_save_path(tmp_path, artwork(title="line1\nline2\t\x00"))
    assert not any(ord(character) < 32 for character in control_path.name)

    long_path = get_save_path(tmp_path, artwork(title="长" * 300))
    assert len(long_path.name) <= 200
    assert len(metadata_path_for(long_path).name) <= 200

    emoji_path = get_save_path(
        tmp_path,
        artwork(
            title="😀" * 200,
            user={"id": 456, "name": "😀" * 200},
        ),
    )
    windows_units = lambda value: len(value.encode("utf-16-le")) // 2
    assert emoji_path.parent.is_dir()
    assert windows_units(emoji_path.parent.name) <= 200
    assert windows_units(emoji_path.name) <= 200
    assert windows_units(metadata_path_for(emoji_path).name) <= 200
