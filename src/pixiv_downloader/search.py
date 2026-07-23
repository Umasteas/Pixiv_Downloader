"""搜索任务查询的有限集合校验。"""
from typing import Any


_SEARCH_TYPES = ("tag_exact", "tag_partial")
_SEARCH_SORTS = ("popular", "date", "bookmarks")
_MAX_SEARCH_LIMIT = 10_000
_MAX_KEYWORD_LENGTH = 200


class SearchValidationError(ValueError):
    """搜索任务 query 不符合可持久化契约。"""


def normalize_search_query(query: dict[str, Any] | None) -> dict[str, Any]:
    """返回稳定、可持久化且可在执行边界重新校验的搜索 query。"""
    if not isinstance(query, dict) or set(query) - {
        "keyword",
        "search_type",
        "sort",
        "limit",
    }:
        raise SearchValidationError("搜索条件无效")

    keyword = query.get("keyword")
    search_type = query.get("search_type", "tag_partial")
    sort = query.get("sort", "date")
    limit = query.get("limit", 500)
    if (
        not isinstance(keyword, str)
        or not (keyword := keyword.strip())
        or len(keyword) > _MAX_KEYWORD_LENGTH
        or any(ord(character) < 32 for character in keyword)
        or search_type not in _SEARCH_TYPES
        or sort not in _SEARCH_SORTS
        or type(limit) is not int
        or not 1 <= limit <= _MAX_SEARCH_LIMIT
    ):
        raise SearchValidationError("搜索条件无效")

    return {
        "keyword": keyword,
        "search_type": search_type,
        "sort": sort,
        "limit": limit,
    }
