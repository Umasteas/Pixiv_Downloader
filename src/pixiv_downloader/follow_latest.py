"""手动扫描 Pixiv 关注画师的新作品。"""
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class FollowLatestError(RuntimeError):
    """关注新作扫描无法安全继续。"""


class FollowLatestScanner:
    """获取全部关注画师，比较数据库基线并返回分组新作。"""

    def __init__(self, *, api, database, clock: Callable[[], str] | None = None):
        self.api = api
        self.database = database
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).isoformat()
        )

    def fetch_followed_artists(self) -> list[dict[str, Any]]:
        """合并公开和私密关注列表，并跟随 offset 分页。"""
        user_id = int(getattr(self.api, "user_id", 0) or 0)
        if user_id <= 0:
            raise FollowLatestError("Pixiv 当前用户 ID 不可用")

        artists: dict[int, dict[str, Any]] = {}
        for restrict in ("public", "private"):
            offset = None
            seen_offsets = set()
            while True:
                response = self.api.user_following(
                    user_id,
                    restrict=restrict,
                    offset=offset,
                )
                for preview in _field(response, "user_previews", []) or []:
                    user = _field(preview, "user", {})
                    artist_id = int(_field(user, "id"))
                    artists[artist_id] = {
                        "artist_id": artist_id,
                        "artist_name": str(_field(user, "name", "")),
                    }

                next_url = _field(response, "next_url")
                next_offset = self._query_value(next_url, "offset")
                if not next_offset or next_offset in seen_offsets:
                    break
                seen_offsets.add(next_offset)
                offset = next_offset
        return [artists[key] for key in sorted(artists)]

    def fetch_follow_latest(self) -> dict[str, Any]:
        """扫描新作、更新数据库基线，并返回前端可直接分组展示的数据。"""
        checked_at = self.clock()
        groups = []
        pending_updates = []
        total_new = 0

        for artist in self.fetch_followed_artists():
            artist_id = artist["artist_id"]
            artist_name = artist["artist_name"]
            previous = self.database.get_followed_artist(artist_id)
            baseline = previous.get("last_artwork_id") if previous else None
            artworks, latest_id = self._fetch_artist_latest(artist, baseline)
            total_new += len(artworks)
            groups.append({
                "artist_id": artist_id,
                "artist_name": artist_name,
                "last_check_time": (
                    previous.get("last_check_time") if previous else None
                ),
                "new_count": len(artworks),
                "artworks": artworks,
            })
            pending_updates.append((
                artist_id,
                artist_name,
                latest_id if latest_id is not None else baseline,
            ))

        for artist_id, artist_name, last_artwork_id in pending_updates:
            self.database.update_followed_artist(
                artist_id,
                artist_name=artist_name,
                last_artwork_id=last_artwork_id,
                checked_at=checked_at,
            )

        return {
            "checked_at": checked_at,
            "new_count": total_new,
            "artists": groups,
        }

    def _fetch_artist_latest(
        self,
        artist: dict[str, Any],
        baseline: int | None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        artist_id = artist["artist_id"]
        artist_name = artist["artist_name"]
        options: dict[str, str] = {}
        seen_urls = set()
        new_artworks = []
        latest_id = None

        while True:
            response = self.api.user_illusts(artist_id, **options)
            page = _field(response, "illusts", []) or []
            reached_baseline = False
            for artwork in page:
                artwork_id = int(_field(artwork, "id"))
                latest_id = max(latest_id or artwork_id, artwork_id)
                if baseline is not None and artwork_id <= baseline:
                    reached_baseline = True
                    continue
                new_artworks.append(
                    self._public_artwork(artwork, artist_id, artist_name)
                )

            next_url = _field(response, "next_url")
            if baseline is None or reached_baseline or not next_url:
                break
            if str(next_url) in seen_urls:
                break
            seen_urls.add(str(next_url))
            options = self._pagination_options(next_url)
            if not options:
                break

        new_artworks.sort(key=lambda item: item["artwork_id"], reverse=True)
        return new_artworks, latest_id

    @staticmethod
    def _public_artwork(
        artwork: Any,
        artist_id: int,
        artist_name: str,
    ) -> dict[str, Any]:
        image_urls = _field(artwork, "image_urls", {}) or {}
        thumbnail = (
            _field(image_urls, "square_medium")
            or _field(image_urls, "medium")
            or ""
        )
        return {
            "artwork_id": int(_field(artwork, "id")),
            "artist_id": artist_id,
            "artist_name": artist_name,
            "title": str(_field(artwork, "title", "")),
            "thumbnail_url": str(thumbnail),
            "bookmarks": int(_field(artwork, "total_bookmarks", 0) or 0),
        }

    @staticmethod
    def _query_value(url: Any, key: str) -> str | None:
        if not url:
            return None
        values = parse_qs(urlparse(str(url)).query).get(key)
        return values[-1] if values else None

    @classmethod
    def _pagination_options(cls, url: Any) -> dict[str, str]:
        query = parse_qs(urlparse(str(url)).query)
        return {
            key: values[-1]
            for key, values in query.items()
            if key in {"type", "filter", "offset"} and values
        }
