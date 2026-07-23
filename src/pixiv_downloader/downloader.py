"""画师来源的 Pixiv 单图作品下载服务。"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp

from .download_policy import DownloadPolicy
from .errors import (
    ErrorCategory,
    FatalDownloadError,
    RecoverableDownloadError,
    SkippableDownloadError,
    classify_error,
)
from .filters import filter_artworks
from .ranking import normalize_ranking_query, ranking_mode, ranking_work_type
from .rate_limiter import AdaptiveLimiter
from .search import normalize_search_query
from .ugoira import (
    check_ffmpeg,
    convert_to_gif,
    convert_to_mp4,
    parse_ugoira_frames,
)

from .file_organizer import (
    get_collection_artwork_directory,
    get_collection_save_path,
    get_artwork_directory,
    get_page_save_path,
    get_save_path,
    metadata_path_for,
)


logger = logging.getLogger(__name__)


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
        ugoira_options: dict[str, Any] | None = None,
        collection_dir: Path | str | None = None,
        source: str = "artist",
        metadata_context: dict[str, Any] | None = None,
        gif_converter=convert_to_gif,
        mp4_converter=convert_to_mp4,
        ffmpeg_checker=check_ffmpeg,
    ) -> None:
        self.api = api
        self.database = database
        self.proxy_manager = proxy_manager
        self.output_dir = Path(output_dir)
        self.collection_dir = Path(collection_dir) if collection_dir is not None else None
        self.source = source
        self.metadata_context = dict(metadata_context or {})
        self.session_factory = session_factory
        self.policy = policy or DownloadPolicy()
        self.sleep = sleep
        self.ugoira_options = {
            "download_original": True,
            "convert_to_gif": False,
            "convert_to_mp4": True,
            "ffmpeg_path": "auto",
            **(ugoira_options or {}),
        }
        self.gif_converter = gif_converter
        self.mp4_converter = mp4_converter
        self.ffmpeg_checker = ffmpeg_checker

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

    def fetch_search_results(
        self,
        keyword: str,
        search_type: str,
        sort: str,
        limit: int,
    ) -> list[Any]:
        """按稳定 query 获取有界候选窗口；收藏数排序在窗口内执行。"""
        query = normalize_search_query({
            "keyword": keyword,
            "search_type": search_type,
            "sort": sort,
            "limit": limit,
        })
        search_target = {
            "tag_exact": "exact_match_for_tags",
            "tag_partial": "partial_match_for_tags",
        }[query["search_type"]]
        api_sort = "popular_desc" if query["sort"] == "popular" else "date_desc"
        artworks: list[Any] = []
        seen_artwork_ids: set[int] = set()
        seen_offsets: set[str] = set()
        offset: str | None = None

        while True:
            options: dict[str, Any] = {
                "search_target": search_target,
                "sort": api_sort,
            }
            if offset is not None:
                options["offset"] = offset
            response = self.api.search_illust(query["keyword"], **options)
            count_before_page = len(artworks)
            for artwork in _field(response, "illusts", []) or []:
                if len(artworks) >= query["limit"]:
                    break
                artwork_id = int(_field(artwork, "id"))
                if artwork_id in seen_artwork_ids:
                    continue
                seen_artwork_ids.add(artwork_id)
                artworks.append(artwork)
                if query["sort"] != "bookmarks" and len(artworks) >= query["limit"]:
                    return artworks[:query["limit"]]

            if len(artworks) >= query["limit"]:
                break
            if len(artworks) == count_before_page:
                break

            next_url = _field(response, "next_url")
            if not next_url:
                break
            values = parse_qs(urlparse(str(next_url)).query).get("offset", [])
            if len(values) != 1 or not values[0].isdigit() or values[0] in seen_offsets:
                break
            offset = values[0]
            seen_offsets.add(offset)

        if query["sort"] == "bookmarks":
            artworks.sort(
                key=lambda artwork: int(
                    _field(artwork, "total_bookmarks", 0) or 0
                ),
                reverse=True,
            )
        return artworks[:query["limit"]]

    def fetch_ranking(
        self,
        ranking_type: str,
        ranking_date: str,
        limit: int,
        *,
        control=None,
    ) -> list[Any]:
        """按固定榜单、日期和有界候选数量读取排行榜。"""
        query = normalize_ranking_query({
            "ranking_type": ranking_type,
            "date": ranking_date,
            "limit": limit,
        })
        artworks: list[Any] = []
        seen_artwork_ids: set[int] = set()
        offset: int | None = None
        last_offset = -1
        request_count = 0

        while request_count < 100:
            if control is not None and not control.checkpoint():
                break
            options: dict[str, Any] = {
                "mode": ranking_mode(query["ranking_type"]),
                "filter": "for_ios",
                "date": query["date"],
            }
            if offset is not None:
                options["offset"] = offset
            response = self.api.illust_ranking(**options)
            request_count += 1
            count_before_page = len(artworks)
            for artwork in _field(response, "illusts", []) or []:
                if len(artworks) >= query["limit"]:
                    break
                raw_artwork_id = _field(artwork, "id")
                if isinstance(raw_artwork_id, bool):
                    continue
                try:
                    artwork_id = int(raw_artwork_id)
                except (TypeError, ValueError, OverflowError):
                    continue
                if artwork_id <= 0:
                    continue
                if artwork_id in seen_artwork_ids:
                    continue
                seen_artwork_ids.add(artwork_id)
                artworks.append(artwork)

            if len(artworks) >= query["limit"]:
                break
            if len(artworks) == count_before_page:
                break

            next_url = _field(response, "next_url")
            if not next_url:
                break
            values = parse_qs(urlparse(str(next_url)).query).get("offset", [])
            if len(values) != 1 or not values[0].isdigit():
                break
            if len(values[0]) > len(str(1_000_000)):
                break
            next_offset = int(values[0])
            if next_offset <= last_offset or next_offset > 1_000_000:
                break
            offset = next_offset
            last_offset = next_offset

        return artworks[:query["limit"]]

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
        task_id: int | None = None,
        artwork_id: int | None = None,
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
                    if status in {401, 403}:
                        raise FatalDownloadError("Pixiv 登录状态失效。")
                    if 400 <= status <= 499:
                        message = (
                            "作品或文件不存在。"
                            if status == 404
                            else "Pixiv 拒绝了作品请求。"
                        )
                        raise SkippableDownloadError(message)
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
                        raise RecoverableDownloadError("下载重试耗尽") from exc
                    delay = self.policy.retry_delays[attempt]
                    if isinstance(exc, _RetryableHttpStatus) and exc.status == 429:
                        if limiter is not None:
                            await limiter.reduce_limit()
                        if exc.retry_after is not None:
                            delay = max(delay, exc.retry_after)
                    reason = (
                        f"HTTP {exc.status}"
                        if isinstance(exc, _RetryableHttpStatus)
                        else exc.__class__.__name__
                    )
                    logger.warning(
                        "下载请求失败，准备重试：task_id=%s artwork_id=%s "
                        "attempt=%s/%s delay=%s reason=%s",
                        task_id,
                        artwork_id,
                        attempt + 1,
                        self.policy.retry_times,
                        delay,
                        reason,
                    )
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

        if _field(artwork, "type") == "ugoira":
            save_path, metadata_path, file_size = await self._download_ugoira(
                artwork,
                session=session,
                request_options=request_options,
                progress=progress,
                limiter=limiter,
                control=control,
                task_id=task_id,
            )
        elif page_count > 1:
            urls = self._original_image_urls(artwork)
            save_path = self._artwork_directory(artwork)
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
                    task_id=task_id,
                    artwork_id=artwork_id,
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
            save_path = self._save_path(artwork)
            image_url = self._original_image_url(artwork)
            await self.download_file(
                image_url,
                save_path,
                session=session,
                request_options=request_options,
                progress=progress,
                limiter=limiter,
                control=control,
                task_id=task_id,
                artwork_id=artwork_id,
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

    async def download_ugoira_zip(
        self,
        artwork_id: int,
        save_path: Path | str,
        *,
        session=None,
        request_options: dict[str, Any] | None = None,
        progress=None,
        limiter=None,
        control=None,
        task_id: int | None = None,
    ) -> list[Any]:
        """获取 Pixiv Ugoira 元数据，下载 ZIP，并验证帧与延迟。"""
        response = self.api.ugoira_metadata(int(artwork_id))
        metadata = _field(response, "ugoira_metadata", {}) or {}
        zip_urls = _field(metadata, "zip_urls", {}) or {}
        url = _field(zip_urls, "medium")
        frames = _field(metadata, "frames", []) or []
        if not url:
            raise DownloadError("Ugoira 元数据缺少 ZIP 地址。")
        await self.download_file(
            str(url),
            save_path,
            session=session,
            request_options=request_options,
            progress=progress,
            limiter=limiter,
            control=control,
            task_id=task_id,
            artwork_id=artwork_id,
        )
        parse_ugoira_frames(save_path, frames)
        return list(frames)

    async def _download_ugoira(
        self,
        artwork: Any,
        *,
        session,
        request_options,
        progress,
        limiter,
        control,
        task_id,
    ) -> tuple[Path, Path, int]:
        options = self.ugoira_options
        selected = any(
            bool(options.get(key))
            for key in ("download_original", "convert_to_gif", "convert_to_mp4")
        )
        if not selected:
            raise DownloadError("Ugoira 至少需要选择一种保存格式。")
        artwork_id = int(_field(artwork, "id"))
        artwork_dir = self._artwork_directory(artwork)
        archive_path = artwork_dir / "ugoira.zip"
        frames = await self.download_ugoira_zip(
            artwork_id,
            archive_path,
            session=session,
            request_options=request_options,
            progress=progress,
            limiter=limiter,
            control=control,
            task_id=task_id,
        )
        conversion_failed = False
        if options.get("convert_to_gif"):
            try:
                self.gif_converter(archive_path, frames, artwork_dir / "ugoira.gif")
            except Exception:
                conversion_failed = True
                logger.warning("Ugoira GIF 转换失败，已保留 ZIP：artwork_id=%s", artwork_id)
        if options.get("convert_to_mp4"):
            status = self.ffmpeg_checker(str(options.get("ffmpeg_path", "auto")))
            if status.available and status.path:
                try:
                    self.mp4_converter(
                        archive_path,
                        frames,
                        artwork_dir / "ugoira.mp4",
                        ffmpeg_path=status.path,
                    )
                except Exception:
                    conversion_failed = True
                    logger.warning(
                        "Ugoira MP4 转换失败，已保留 ZIP：artwork_id=%s",
                        artwork_id,
                    )
            else:
                conversion_failed = True
                logger.warning(
                    "Ugoira MP4 转换跳过，未检测到 ffmpeg，已保留 ZIP：artwork_id=%s",
                    artwork_id,
                )
        if not options.get("download_original") and not conversion_failed:
            archive_path.unlink()
        files = [
            path
            for path in (
                archive_path,
                artwork_dir / "ugoira.gif",
                artwork_dir / "ugoira.mp4",
            )
            if path.is_file()
        ]
        return artwork_dir, artwork_dir / "metadata.json", sum(
            path.stat().st_size for path in files
        )

    async def download_artist(
        self,
        artist_id: int,
        *,
        task_id: int | None = None,
        start_index: int = 0,
        control=None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """下载画师的全部单图，单个作品失败时继续后续作品。"""
        artworks = filter_artworks(
            self.fetch_artist_artworks(int(artist_id)),
            filters,
        )
        return await self._download_items(
            artworks,
            lambda artwork: artwork,
            task_id=task_id,
            start_index=start_index,
            control=control,
        )

    async def download_search(
        self,
        keyword: str,
        search_type: str,
        sort: str,
        limit: int,
        *,
        task_id: int | None = None,
        start_index: int = 0,
        control=None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """下载规范化搜索结果，并复用筛选、并发和恢复语义。"""
        artworks = filter_artworks(
            self.fetch_search_results(keyword, search_type, sort, limit),
            filters,
        )
        return await self._download_items(
            artworks,
            lambda artwork: artwork,
            task_id=task_id,
            start_index=start_index,
            control=control,
        )

    async def download_ranking(
        self,
        ranking_type: str,
        ranking_date: str,
        limit: int,
        *,
        task_id: int | None = None,
        start_index: int = 0,
        control=None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """下载规范化榜单结果，并复用筛选、并发和恢复语义。"""
        fetch_options = {"control": control} if control is not None else {}
        artworks = self.fetch_ranking(
            ranking_type,
            ranking_date,
            limit,
            **fetch_options,
        )
        required_work_type = ranking_work_type(ranking_type)
        if required_work_type is not None:
            artworks = [
                artwork
                for artwork in artworks
                if _field(artwork, "type") == required_work_type
            ]
        artworks = filter_artworks(artworks, filters)
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

            async def process(item: Any):
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
                except Exception as exc:
                    failure = classify_error(exc)
                    if failure.category is ErrorCategory.FATAL:
                        raise FatalDownloadError(failure.message) from exc
                    try:
                        artwork_id = int(_field(item, "id", item))
                    except (TypeError, ValueError) as id_error:
                        raise FatalDownloadError("失败作品缺少有效 ID。") from id_error
                    logger.warning(
                        "作品处理失败并跳过：artwork_id=%s category=%s message=%s",
                        artwork_id,
                        failure.category.value,
                        failure.message,
                    )
                    return "failed", (
                        artwork_id,
                        failure.category.value,
                        failure.message,
                    )
                return status, None

            statuses = [None] * len(scheduled)
            next_index = 0
            claim_lock = asyncio.Lock()
            stop_scheduling = asyncio.Event()

            async def worker() -> None:
                nonlocal next_index
                while True:
                    async with claim_lock:
                        if stop_scheduling.is_set() or next_index >= len(scheduled):
                            return
                        index = next_index
                        next_index += 1
                    try:
                        item_result = await process(scheduled[index])
                    except FatalDownloadError:
                        stop_scheduling.set()
                        raise
                    statuses[index] = item_result
                    if item_result is None:
                        stop_scheduling.set()
                        return

            workers = [
                asyncio.create_task(worker())
                for _ in range(min(self.policy.max_concurrent, len(scheduled)))
            ]
            try:
                await asyncio.gather(*workers)
            except BaseException:
                stop_scheduling.set()
                for task in workers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
            checkpoint_prefix_open = True
            for item_result in statuses:
                if item_result is None:
                    checkpoint_prefix_open = False
                    continue
                status, failure = item_result
                result[status] += 1
                if control is not None and checkpoint_prefix_open:
                    if not await _control_allows_progress(control):
                        checkpoint_prefix_open = False
                        continue
                    if failure is not None and hasattr(control, "record_failure"):
                        control.record_failure(*failure)
                    else:
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

    def _artwork_directory(self, artwork: Any) -> Path:
        if self.collection_dir is not None:
            return get_collection_artwork_directory(self.collection_dir, artwork)
        return get_artwork_directory(self.output_dir, artwork)

    def _save_path(self, artwork: Any) -> Path:
        if self.collection_dir is not None:
            return get_collection_save_path(self.collection_dir, artwork)
        return get_save_path(self.output_dir, artwork)

    def _metadata(self, artwork: Any, save_path: Path) -> dict[str, Any]:
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
                **self.metadata_context,
                "source": self.source,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "file_path": str(save_path),
            },
        }
