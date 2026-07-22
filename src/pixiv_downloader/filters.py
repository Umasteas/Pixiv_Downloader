"""任务作品列表的基础筛选规则。"""
from typing import Any, Iterable


_RATING_BY_RESTRICTION = {0: "general", 1: "r18", 2: "r18g"}
_WORK_TYPES = ("illust", "manga", "ugoira")
_R18_MODES = ("general", "r18", "r18g")


class FilterValidationError(ValueError):
    """任务筛选条件不是受支持的有限集合。"""


def _field(artwork: Any, name: str, default=None):
    if isinstance(artwork, dict):
        return artwork.get(name, default)
    return getattr(artwork, name, default)


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    """返回稳定、可持久化的基础筛选条件。"""
    if filters is None:
        filters = {}
    if not isinstance(filters, dict) or set(filters) - {
        "work_types",
        "r18_modes",
        "min_bookmarks",
    }:
        raise FilterValidationError

    requested_types = filters.get("work_types", list(_WORK_TYPES))
    requested_ratings = filters.get("r18_modes", list(_R18_MODES))
    minimum = filters.get("min_bookmarks", 0)
    if (
        not isinstance(requested_types, list)
        or not requested_types
        or any(value not in _WORK_TYPES for value in requested_types)
        or not isinstance(requested_ratings, list)
        or not requested_ratings
        or any(value not in _R18_MODES for value in requested_ratings)
        or type(minimum) is not int
        or minimum < 0
    ):
        raise FilterValidationError

    requested_types = set(requested_types)
    requested_ratings = set(requested_ratings)
    return {
        "work_types": [value for value in _WORK_TYPES if value in requested_types],
        "r18_modes": [value for value in _R18_MODES if value in requested_ratings],
        "min_bookmarks": minimum,
    }


def filter_artworks(
    artworks: Iterable[Any],
    filters: dict[str, Any] | None,
) -> list[Any]:
    """按作品类型、分级和最低收藏数保留匹配作品。"""
    filters = normalize_filters(filters)
    work_types = set(filters["work_types"])
    r18_modes = set(filters["r18_modes"])
    min_bookmarks = filters["min_bookmarks"]

    return [
        artwork
        for artwork in artworks
        if _field(artwork, "type") in work_types
        and (
            _RATING_BY_RESTRICTION.get(_field(artwork, "x_restrict"))
            in r18_modes
        )
        and int(_field(artwork, "total_bookmarks", 0) or 0) >= min_bookmarks
    ]
