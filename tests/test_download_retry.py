"""Issue #9 单文件智能重试状态机。"""
import asyncio
from collections import deque

import aiohttp
import pytest


class Response:
    def __init__(self, status=200, payload=b"ok", headers=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self.content = self
        self.content_length = len(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def iter_chunked(self, size):
        yield self.payload


class SequenceSession:
    def __init__(self, events):
        self.events = deque(events)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        event = self.events.popleft()
        if isinstance(event, BaseException):
            raise event
        return event


class Proxy:
    def aiohttp_options(self):
        return {}, {}


def downloader(tmp_path, *, retry_times=3, retry_delays=(1, 3, 5), sleeps=None):
    from src.pixiv_downloader.download_policy import DownloadPolicy
    from src.pixiv_downloader.downloader import ArtistDownloader

    async def fake_sleep(delay):
        if sleeps is not None:
            sleeps.append(delay)

    return ArtistDownloader(
        object(),
        object(),
        Proxy(),
        output_dir=tmp_path,
        policy=DownloadPolicy(2, retry_times, tuple(float(x) for x in retry_delays)),
        sleep=fake_sleep,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_failure",
    [
        aiohttp.ClientConnectionError("network"),
        asyncio.TimeoutError(),
        Response(status=500),
        Response(status=503),
    ],
)
async def test_retryable_failures_retry_then_replace_atomically(
    tmp_path,
    first_failure,
    caplog,
):
    sleeps = []
    target = tmp_path / "retry.jpg"
    session = SequenceSession([first_failure, Response(payload=b"success")])

    with caplog.at_level("WARNING", logger="src.pixiv_downloader.downloader"):
        await downloader(tmp_path, sleeps=sleeps).download_file(
            "https://example.test/a",
            target,
            session=session,
            task_id=7,
            artwork_id=8,
        )

    assert session.calls == 2
    assert sleeps == [1.0]
    assert target.read_bytes() == b"success"
    assert not target.with_suffix(".jpg.part").exists()
    assert "下载请求失败，准备重试" in caplog.text
    assert "task_id=7" in caplog.text
    assert "artwork_id=8" in caplog.text
    assert "https://example.test/a" not in caplog.text


@pytest.mark.asyncio
async def test_retry_budget_is_three_retries_after_initial_request(tmp_path):
    sleeps = []
    target = tmp_path / "failed.jpg"
    session = SequenceSession([Response(status=503) for _ in range(4)])

    with pytest.raises(Exception):
        await downloader(tmp_path, sleeps=sleeps).download_file("https://example.test/a", target, session=session)

    assert session.calls == 4
    assert sleeps == [1.0, 3.0, 5.0]
    assert not target.exists()
    assert not target.with_suffix(".jpg.part").exists()


@pytest.mark.asyncio
async def test_non_retryable_4xx_fails_without_sleep(tmp_path):
    sleeps = []
    session = SequenceSession([Response(status=404), Response()])

    with pytest.raises(Exception):
        await downloader(tmp_path, sleeps=sleeps).download_file("https://example.test/a", tmp_path / "404.jpg", session=session)

    assert session.calls == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["invalid", "-1", None])
async def test_invalid_retry_after_falls_back_to_configured_delay(tmp_path, retry_after):
    sleeps = []
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    session = SequenceSession([
        Response(status=429, headers=headers),
        Response(payload=b"success"),
    ])

    class Limiter:
        async def acquire(self):
            return None

        async def release(self):
            return None

        async def reduce_limit(self):
            return 1

    await downloader(tmp_path, sleeps=sleeps).download_file(
        "https://example.test/a",
        tmp_path / "fallback.jpg",
        session=session,
        limiter=Limiter(),
    )

    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_429_reduces_limiter_and_honours_retry_after(tmp_path):
    sleeps = []
    session = SequenceSession([
        Response(status=429, headers={"Retry-After": "4"}),
        Response(payload=b"success"),
    ])

    class Limiter:
        def __init__(self):
            self.reductions = 0
            self.active = 0

        async def acquire(self):
            self.active += 1

        async def release(self):
            self.active -= 1

        async def reduce_limit(self):
            self.reductions += 1

    limiter = Limiter()
    await downloader(tmp_path, sleeps=sleeps).download_file(
        "https://example.test/a",
        tmp_path / "429.jpg",
        session=session,
        limiter=limiter,
    )

    assert limiter.reductions == 1
    assert sleeps == [4.0]
