"""收藏任务 query 与资源预算。"""
from typing import Any


MAX_BOOKMARK_ITEMS = 5000
MAX_BOOKMARK_PAGES = 200
MAX_BOOKMARK_TAGS = 1000
MAX_BOOKMARK_TAG_LENGTH = 100
MAX_CUSTOM_BOOKMARK_LIMIT = 1000
MAX_CONSECUTIVE_NO_PROGRESS_PAGES = 3

_VISIBILITIES = {"public", "private"}
_SORTS = {"bookmark_time", "artwork_date", "bookmarks"}


class BookmarksValidationError(ValueError):
    """收藏任务 query 不符合可持久化契约。"""


def normalize_bookmark_tag(tag: Any) -> str | None:
    """规范化可选收藏标签；非法外部标签一律拒绝。"""
    if tag is None:
        return None
    if not isinstance(tag, str):
        raise BookmarksValidationError("收藏条件无效")
    normalized = tag.strip()
    if not normalized:
        return None
    if (
        len(normalized) > MAX_BOOKMARK_TAG_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise BookmarksValidationError("收藏条件无效")
    return normalized


def normalize_bookmarks_query(query: dict[str, Any] | None) -> dict[str, Any]:
    """校验并规范化收藏任务 query。"""
    if (
        not isinstance(query, dict)
        or "visibility" not in query
        or set(query) - {"visibility", "tag", "sort", "limit"}
    ):
        raise BookmarksValidationError("收藏条件无效")

    visibility = query.get("visibility")
    sort = query.get("sort", "bookmark_time")
    limit = query.get("limit")
    tag = query.get("tag")
    if visibility not in _VISIBILITIES or sort not in _SORTS:
        raise BookmarksValidationError("收藏条件无效")
    if limit is not None and (
        type(limit) is not int or not 1 <= limit <= MAX_CUSTOM_BOOKMARK_LIMIT
    ):
        raise BookmarksValidationError("收藏条件无效")
    tag = normalize_bookmark_tag(tag)

    return {
        "visibility": visibility,
        "tag": tag,
        "sort": sort,
        "limit": limit,
    }


def normalize_persisted_bookmarks_query(
    query: dict[str, Any] | None,
) -> dict[str, Any]:
    """校验内部持久化 query，并允许不可变有序作品快照。"""
    if not isinstance(query, dict) or set(query) - {
        "visibility", "tag", "sort", "limit", "artwork_ids",
    }:
        raise BookmarksValidationError("收藏条件无效")
    normalized = normalize_bookmarks_query({
        key: value for key, value in query.items() if key != "artwork_ids"
    })
    if "artwork_ids" not in query:
        return normalized
    artwork_ids = query["artwork_ids"]
    if (
        not isinstance(artwork_ids, list)
        or len(artwork_ids) > MAX_BOOKMARK_ITEMS
        or any(type(value) is not int or value <= 0 for value in artwork_ids)
        or len(set(artwork_ids)) != len(artwork_ids)
    ):
        raise BookmarksValidationError("收藏条件无效")
    return {**normalized, "artwork_ids": list(artwork_ids)}
