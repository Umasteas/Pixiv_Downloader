"""画师来源的 Pixiv 单图作品下载服务。"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp

from .file_organizer import get_save_path, metadata_path_for


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class DownloadError(RuntimeError):
    """作品数据或文件下载失败。"""


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
    ) -> None:
        self.api = api
        self.database = database
        self.proxy_manager = proxy_manager
        self.output_dir = Path(output_dir)
        self.session_factory = session_factory

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

        async def transfer(active_session) -> None:
            try:
                async with active_session.get(url, **request_options) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            output.write(chunk)
                temporary.replace(save_path)
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
    ) -> str:
        """下载一个未下载的单图作品并记录元数据和数据库历史。"""
        artwork_id = int(_field(artwork, "id"))
        if int(_field(artwork, "page_count", 1)) != 1:
            return "skipped"
        if self.database.is_downloaded(artwork_id):
            return "skipped"

        save_path = get_save_path(self.output_dir, artwork)
        image_url = self._original_image_url(artwork)
        await self.download_file(
            image_url,
            save_path,
            session=session,
            request_options=request_options,
        )

        metadata = self._metadata(artwork, save_path)
        metadata_path_for(save_path).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        user = _field(artwork, "user", {})
        tags = metadata["tags"]
        self.database.mark_as_downloaded(
            artwork_id,
            artist_id=int(_field(user, "id")),
            artist_name=str(_field(user, "name", "")),
            title=str(_field(artwork, "title", "")),
            file_path=str(save_path),
            file_size=save_path.stat().st_size,
            page_count=1,
            bookmarks=int(_field(artwork, "total_bookmarks", 0) or 0),
            tags=tags,
        )
        return "downloaded"

    async def download_artist(self, artist_id: int) -> dict[str, int]:
        """下载画师的全部单图，单个作品失败时继续后续作品。"""
        artworks = self.fetch_artist_artworks(int(artist_id))
        result = {"total": len(artworks), "downloaded": 0, "skipped": 0, "failed": 0}
        session_options, request_options = self.proxy_manager.aiohttp_options()
        async with self.session_factory(**session_options) as session:
            for artwork in artworks:
                try:
                    status = await self.download_artwork(
                        artwork,
                        session=session,
                        request_options=request_options,
                    )
                except Exception:
                    result["failed"] += 1
                else:
                    result[status] += 1
        return result

    @staticmethod
    def _original_image_url(artwork: Any) -> str:
        single_page = _field(artwork, "meta_single_page", {}) or {}
        url = _field(single_page, "original_image_url")
        if not url:
            raise DownloadError("作品缺少原图地址，拒绝下载非原图")
        return str(url)

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
            "create_date": str(_field(artwork, "create_date", "")),
            "download_info": {
                "source": "artist",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "file_path": str(save_path),
            },
        }
