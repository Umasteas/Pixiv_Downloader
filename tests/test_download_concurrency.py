"""Issue #9 文件级并发集成测试。"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_cancellation_after_download_does_not_record_into_cancelled_task(
    tmp_path, monkeypatch
):
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Control:
        def __init__(self):
            self.cancelled = False
            self.results = []

        def set_total(self, total):
            return None

        def checkpoint(self):
            return not self.cancelled

        def record_result(self, status):
            self.results.append(status)
            raise AssertionError("不得向已取消任务写入作品结果")

    downloader = ArtistDownloader(
        object(), object(), Proxy(), output_dir=tmp_path,
        session_factory=Session, policy=DownloadPolicy(1, 0, ()),
    )
    control = Control()

    async def finish_then_cancel(item, **kwargs):
        control.cancelled = True
        return "downloaded"

    monkeypatch.setattr(downloader, "download_artwork", finish_then_cancel)

    result = await downloader._download_items(
        [1], lambda value: value,
        task_id=7, start_index=0, control=control,
    )

    assert result == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0}
    assert control.results == []


@pytest.mark.asyncio
async def test_concurrent_results_commit_to_task_control_in_input_order(tmp_path, monkeypatch):
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Control:
        def __init__(self):
            self.results = []

        def set_total(self, total):
            return None

        def checkpoint(self):
            return True

        def record_result(self, status):
            self.results.append(status)

    downloader = ArtistDownloader(
        object(), object(), Proxy(), output_dir=tmp_path,
        session_factory=Session, policy=DownloadPolicy(3, 0, ()),
    )
    statuses = {1: "downloaded", 2: "failed", 3: "skipped"}
    delays = {1: 0.03, 2: 0.02, 3: 0.01}

    async def fake_download(item, **kwargs):
        await asyncio.sleep(delays[item])
        return statuses[item]

    monkeypatch.setattr(downloader, "download_artwork", fake_download)
    control = Control()

    result = await downloader._download_items(
        [1, 2, 3], lambda value: value,
        task_id=7, start_index=0, control=control,
    )

    assert result == {"total": 3, "downloaded": 1, "skipped": 1, "failed": 1}
    assert control.results == ["downloaded", "failed", "skipped"]


@pytest.mark.asyncio
async def test_paused_control_checkpoint_does_not_block_event_loop(tmp_path):
    import threading
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader

    release = threading.Event()

    class Control:
        def set_total(self, total):
            return None

        def checkpoint(self):
            release.wait(timeout=2)
            return False

    class Api:
        def user_illusts(self, artist_id, **kwargs):
            return {"illusts": [{"id": 1}], "next_url": None}

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    downloader = ArtistDownloader(
        Api(), object(), Proxy(), output_dir=tmp_path,
        session_factory=Session, policy=DownloadPolicy(1, 0, ()),
    )
    operation = asyncio.create_task(downloader.download_artist(9, control=Control()))

    await asyncio.sleep(0.02)
    assert not operation.done()
    release.set()
    result = await asyncio.wait_for(operation, timeout=1)

    assert result == {"total": 1, "downloaded": 0, "skipped": 0, "failed": 0}


@pytest.mark.asyncio
async def test_artist_download_runs_files_concurrently_up_to_policy_limit(tmp_path):
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader

    artworks = [
        {
            "id": number,
            "title": f"work-{number}",
            "page_count": 1,
            "user": {"id": 9, "name": "artist"},
            "meta_single_page": {"original_image_url": f"https://example.test/{number}.jpg"},
        }
        for number in range(1, 5)
    ]

    class Api:
        def user_illusts(self, artist_id, **kwargs):
            return {"illusts": artworks, "next_url": None}

    class Database:
        def __init__(self):
            self.marked = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **kwargs):
            self.marked.append(artwork_id)

    class Proxy:
        def aiohttp_options(self):
            return {}, {}

    class Session:
        instances = []

        def __init__(self, **kwargs):
            self.active = 0
            self.max_active = 0
            self.__class__.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def get(self, url, **kwargs):
            session = self

            class Response:
                status = 200
                headers = {}
                content_length = 2
                content = None

                async def __aenter__(self):
                    self.content = self
                    session.active += 1
                    session.max_active = max(session.max_active, session.active)
                    await asyncio.sleep(0.02)
                    return self

                async def __aexit__(self, exc_type, exc, traceback):
                    session.active -= 1
                    return False

                def raise_for_status(self):
                    return None

                async def iter_chunked(self, size):
                    yield b"ok"

            return Response()

    database = Database()
    downloader = ArtistDownloader(
        Api(),
        database,
        Proxy(),
        output_dir=tmp_path,
        session_factory=Session,
        policy=DownloadPolicy(2, 0, ()),
    )

    result = await downloader.download_artist(9)
    session = Session.instances[0]

    assert result == {"total": 4, "downloaded": 4, "skipped": 0, "failed": 0}
    assert session.max_active == 2
    assert sorted(database.marked) == [1, 2, 3, 4]
