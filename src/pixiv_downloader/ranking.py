"""排行榜任务查询、日期窗口与 Pixiv mode 映射。"""
from datetime import date, datetime, timedelta, timezone
from typing import Any


_RANKING_MODES = {
    "daily": "day",
    "daily_illust": "day",
    "daily_ugoira": "day",
    "weekly": "week",
    "monthly": "month",
    "rookie": "week_rookie",
    "daily_manga": "day_manga",
    "daily_r18": "day_r18",
    "weekly_r18": "week_r18",
    "weekly_r18g": "week_r18g",
}
_RANKING_WORK_TYPES = {
    "daily_illust": "illust",
    "daily_ugoira": "ugoira",
    "daily_manga": "manga",
}
_MAX_RANKING_LIMIT = 500
_JST = timezone(timedelta(hours=9))


class RankingValidationError(ValueError):
    """排行榜任务 query 不符合可持久化契约。"""


def ranking_mode(ranking_type: str) -> str:
    """把稳定产品值映射为 pixivpy 的 ranking mode。"""
    try:
        return _RANKING_MODES[ranking_type]
    except (KeyError, TypeError) as exc:
        raise RankingValidationError("排行榜条件无效") from exc


def ranking_work_type(ranking_type: str) -> str | None:
    """返回产品榜单别名固定的作品类型。"""
    ranking_mode(ranking_type)
    return _RANKING_WORK_TYPES.get(ranking_type)


def normalize_ranking_query(
    query: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """校验并规范化可持久化的排行榜 query。"""
    if not isinstance(query, dict) or set(query) - {"ranking_type", "date", "limit"}:
        raise RankingValidationError("排行榜条件无效")

    ranking_type = query.get("ranking_type")
    date_value = query.get("date")
    limit = query.get("limit", 500)
    if ranking_type not in _RANKING_MODES or type(limit) is not int:
        raise RankingValidationError("排行榜条件无效")
    if not isinstance(date_value, str) or len(date_value) != 10:
        raise RankingValidationError("排行榜条件无效")
    try:
        ranking_date = date.fromisoformat(date_value)
    except ValueError as exc:
        raise RankingValidationError("排行榜条件无效") from exc
    if ranking_date.isoformat() != date_value:
        raise RankingValidationError("排行榜条件无效")

    current_date = today if today is not None else datetime.now(_JST).date()
    if not current_date - timedelta(days=29) <= ranking_date <= current_date:
        raise RankingValidationError("排行榜条件无效")
    if not 1 <= limit <= _MAX_RANKING_LIMIT:
        raise RankingValidationError("排行榜条件无效")

    return {
        "ranking_type": ranking_type,
        "date": ranking_date.isoformat(),
        "limit": limit,
    }
