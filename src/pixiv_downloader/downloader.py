"""画师来源的 Pixiv 单图作品下载服务。"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp

from .download_policy import DownloadPolicy
from .rate_limiter import AdaptiveLimiter

from .file_organizer import (
    get_artwork_directory,
    get_page_save_path,
    get_save_path,
    metadata_path_for,
)


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


async def _control_allows_progress(control) -> bool:
    if control is None:
        return True
    return await asyncio.to_thread(control.checkpoint)


@asynccontextmanager
async def _limiter_slot(limiter):
    if limiter is not None:
        await limiter.acquire()
    try:
        yield
    finally:
        if limiter is not None:
            await limiter.release()


class DownloadError(RuntimeError):
    """作品或文件下载失败。"""


class DownloadCancelled(DownloadError):
    """持久化任务在安全点被取消。"""


class _RetryableHttpStatus(RuntimeError):
    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def parse_page_count(artwork: Any) -> int:
    """读取 Pixiv 页数；缺失字段兼容单图，显式非法值一律拒绝。"""
    page_count = _field(artwork, "page_count", 1)
    if type(page_count) is not int or page_count < 1:
        raise DownloadError("page_count 必须是大于等于 1 的整数")
    return page_count


class ArtistDownloader:
    """从画师作品列表下载单图、元数据并写入去重历史。"""

    def __init__(
        self,
        api,
        database,
        proxy_manager,
        *,
        output_dir: Path | str = "downloads",
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        policy: DownloadPolicy | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.api = api
        self.database = database
        self.proxy_manager = proxy_manager
        self.output_dir = Path(output_dir)
        self.session_factory = session_factory
        self.policy = policy or DownloadPolicy()
        self.sleep = sleep

    def fetch_artist_artworks(self, artist_id: int) -> list[Any]:
        """读取画师的全部作品分页。"""
        artworks: list[Any] = []
        options: dict[str, Any] = {}
        while True:
            response = self.api.user_illusts(int(artist_id), **options)
            artworks.extend(_field(response, "illusts", []) or [])
            next_url = _field(response, "next_url")
            if not next_url:
                break
            query = parse_qs(urlparse(str(next_url)).query)
            allowed = {"type", "filter", "offset"}
            options = {
                key: values[-1]
                for key, values in query.items()
                if key in allowed and values
            }
            if not options:
                break
        return artworks

    async def download_file(
        self,
        url: str,
        save_path: Path | str,
        session=None,
        *,
        request_options: dict[str, Any] | None = None,
        progress: Callable[[str, int, int | None, bool], None] | None = None,
        limiter=None,
        control=None,
    ) -> None:
        """通过统一代理配置流式下载文件，并原子替换目标。"""
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = save_path.with_suffix(save_path.suffix + ".part")
        if request_options is None:
            session_options, request_options = self.proxy_manager.aiohttp_options()
        else:
            session_options = {}
        request_options = dict(request_options)
        headers = dict(request_options.get("headers", {}))
        headers.setdefault("Referer", "https://app-api.pixiv.net/")
        request_options["headers"] = headers

        async def request_once(active_session) -> tuple[int, int | None]:
            async with _limiter_slot(limiter):
                if not await _control_allows_progress(control):
                    raise DownloadCancelled("下载任务已取消")
                async with active_session.get(url, **request_options) as response:
                    status = int(getattr(response, "status", 0) or 0)
                    if status == 429:
                        retry_after = self._retry_after_seconds(
                            getattr(response, "headers", {})
                        )
                        raise _RetryableHttpStatus(status, retry_after)
                    if 500 <= status <= 599:
                        raise _RetryableHttpStatus(status)
                    response.raise_for_status()
                    total_bytes = getattr(response, "content_length", None)
                    downloaded_bytes = 0
                    if progress is not None:
                        progress(save_path.name, 0, total_bytes, False)
                    with temporary.open("wb") as output:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            output.write(chunk)
                            downloaded_bytes += len(chunk)
                            if progress is not None:
                                progress(
                                    save_path.name,
                                    downloaded_bytes,
                                    total_bytes,
                                    False,
                                )
            return downloaded_bytes, total_bytes

        async def transfer(active_session) -> None:
            for attempt in range(self.policy.retry_times + 1):
                try:
                    downloaded_bytes, total_bytes = await request_once(active_session)
                    temporary.replace(save_path)
                    if progress is not None:
                        progress(save_path.name, downloaded_bytes, total_bytes, True)
                    return
                except (aiohttp.ClientError, asyncio.TimeoutError, _RetryableHttpStatus) as exc:
                    temporary.unlink(missing_ok=True)
                    if attempt >= self.policy.retry_times:
                        raise DownloadError(f"下载重试耗尽：{url}") from exc
                    delay = self.policy.retry_delays[attempt]
                    if isinstance(exc, _RetryableHttpStatus) and exc.status == 429:
                        if limiter is not None:
                            await limiter.reduce_limit()
                        if exc.retry_after is not None:
                            delay = max(delay, exc.retry_after)
                    await self.sleep(delay)
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise

        if session is not None:
            await transfer(session)
            return
        async with self.session_factory(**session_options) as active_session:
            await transfer(active_session)

    async def download_artwork(
        self,
        artwork: Any,
        *,
        session=None,
        request_options: dict[str, Any] | None = None,
        task_id: int | None = None,
        progress: Callable[[str, int, int | None, bool], None] | None = None,
        limiter=None,
        control=None,
    ) -> str:
        """下载一个未下载作品，并记录元数据和数据库历史。"""
        artwork_id = int(_field(artwork, "id"))
        page_count = parse_page_count(artwork)
        if self.database.is_downloaded(artwork_id):
            return "skipped"

        if page_count > 1:
            urls = self._original_image_urls(artwork)
            save_path = get_artwork_directory(self.output_dir, artwork)
            page_paths = [
                get_page_save_path(save_path, index, url)
                for index, url in enumerate(urls)
            ]
            pending_pages = [
                self.download_file(
                    url,
                    page_path,
                    session=session,
                    request_options=request_options,
                    progress=progress,
                    limiter=limiter,
                    control=control,
                )
                for url, page_path in zip(urls, page_paths)
                if not page_path.exists()
            ]
            if pending_pages:
                page_results = await asyncio.gather(
                    *pending_pages,
                    return_exceptions=True,
                )
                failure = next(
                    (result for result in page_results if isinstance(result, BaseException)),
                    None,
                )
                if failure is not None:
                    raise failure
            metadata_path = save_path / "metadata.json"
            file_size = sum(path.stat().st_size for path in page_paths)
        else:
            save_path = get_save_path(self.output_dir, artwork)
            image_url = self._original_image_url(artwork)
            await self.download_file(
                image_url,
                save_path,
                session=session,
                request_options=request_options,
                progress=progress,
                limiter=limiter,
                control=control,
            )
            metadata_path = metadata_path_for(save_path)
            file_size = save_path.stat().st_size

        metadata = self._metadata(artwork, save_path)
        self._write_metadata(metadata_path, metadata)
        user = _field(artwork, "user", {})
        record = {
            "artist_id": int(_field(user, "id")),
            "artist_name": str(_field(user, "name", "")),
            "title": str(_field(artwork, "title", "")),
            "file_path": str(save_path),
            "file_size": file_size,
            "page_count": page_count,
            "bookmarks": int(_field(artwork, "total_bookmarks", 0) or 0),
            "tags": metadata["tags"],
        }
        if task_id is not None:
            record["task_id"] = task_id
        try:
            self.database.mark_as_downloaded(artwork_id, **record)
        except Exception:
            metadata_path.unlink(missing_ok=True)
            raise
        return "downloaded"

    async def download_artist(
        self,
        artist_id: int,
        *,
        task_id: int | None = None,
        start_index: int = 0,
        control=None,
    ) -> dict[str, int]:
        """下载画师的全部单图，单个作品失败时继续后续作品。"""
        artworks = self.fetch_artist_artworks(int(artist_id))
        return await self._download_items(
            artworks,
            lambda artwork: artwork,
            task_id=task_id,
            start_index=start_index,
            control=control,
        )

    async def download_artwork_ids(
        self,
        artwork_ids: list[int],
        *,
        task_id: int | None = None,
        start_index: int = 0,
        control=None,
    ) -> dict[str, int]:
        """按作品 ID 下载选中作品，可从持久化索引继续。"""
        def fetch(artwork_id: int):
            artwork = _field(self.api.illust_detail(int(artwork_id)), "illust")
            if artwork is None:
                raise DownloadError("作品详情不存在")
            return artwork

        return await self._download_items(
            artwork_ids,
            fetch,
            task_id=task_id,
            start_index=start_index,
            control=control,
        )

    async def _download_items(
        self,
        items: list[Any],
        resolve: Callable[[Any], Any],
        *,
        task_id: int | None,
        start_index: int,
        control,
    ) -> dict[str, int]:
        result = {"total": len(items), "downloaded": 0, "skipped": 0, "failed": 0}
        if control is not None:
            control.set_total(len(items))
        session_options, request_options = self.proxy_manager.aiohttp_options()
        limiter = AdaptiveLimiter(self.policy.max_concurrent)
        async with self.session_factory(**session_options) as session:
            scheduled = list(items[start_index:])

            async def process(item: Any) -> str | None:
                if not await _control_allows_progress(control):
                    return None
                try:
                    status = await self.download_artwork(
                        resolve(item),
                        session=session,
                        request_options=request_options,
                        task_id=task_id,
                        progress=(
                            getattr(control, "file_progress", None)
                            if control is not None
                            else None
                        ),
                        limiter=limiter,
                        control=control,
                    )
                except DownloadCancelled:
                    return None
                except Exception:
                    status = "failed"
                return status

            statuses = await asyncio.gather(*(process(item) for item in scheduled))
            checkpoint_prefix_open = True
            for status in statuses:
                if status is None:
                    checkpoint_prefix_open = False
                    continue
                result[status] += 1
                if control is not None and checkpoint_prefix_open:
                    if not await _control_allows_progress(control):
                        checkpoint_prefix_open = False
                        continue
                    control.record_result(status)
        return result

    @staticmethod
    def _retry_after_seconds(headers: Any) -> float | None:
        try:
            value = headers.get("Retry-After")
            seconds = float(value)
        except (AttributeError, TypeError, ValueError):
            return None
        return seconds if seconds >= 0 else None

    @staticmethod
    def _original_image_url(artwork: Any) -> str:
        single_page = _field(artwork, "meta_single_page", {}) or {}
        url = _field(single_page, "original_image_url")
        if not url:
            raise DownloadError("作品缺少原图地址，拒绝下载非原图")
        return str(url)

    @staticmethod
    def _original_image_urls(artwork: Any) -> list[str]:
        page_count = parse_page_count(artwork)
        if page_count <= 1:
            return [ArtistDownloader._original_image_url(artwork)]
        pages = _field(artwork, "meta_pages", []) or []
        if len(pages) != page_count:
            raise DownloadError("多图原图地址数量与页数不一致")
        urls = [
            _field(_field(page, "image_urls", {}) or {}, "original")
            for page in pages
        ]
        if any(not url for url in urls):
            raise DownloadError("多图原图地址不完整")
        return [str(url) for url in urls]

    @staticmethod
    def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _metadata(artwork: Any, save_path: Path) -> dict[str, Any]:
        user = _field(artwork, "user", {})
        raw_tags = _field(artwork, "tags", []) or []
        tags = [str(_field(tag, "name", tag)) for tag in raw_tags]
        return {
            "artwork_id": int(_field(artwork, "id")),
            "title": str(_field(artwork, "title", "")),
            "artist": {
                "id": int(_field(user, "id")),
                "name": str(_field(user, "name", "")),
            },
            "tags": tags,
            "bookmarks": int(_field(artwork, "total_bookmarks", 0) or 0),
            "page_count": parse_page_count(artwork),
            "create_date": str(_field(artwork, "create_date", "")),
            "download_info": {
                "source": "artist",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "file_path": str(save_path),
            },
        }
