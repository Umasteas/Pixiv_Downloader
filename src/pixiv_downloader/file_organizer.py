"""Pixiv 下载文件的便携目录组织与安全命名。"""
import re
from pathlib import Path
from typing import Any


_INVALID_FILENAME = re.compile(r'[\x00-\x1f\\/:*?"<>|]')


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _windows_units(value: str) -> int:
    """返回 Windows 文件名使用的 UTF-16 code unit 数。"""
    return len(value.encode("utf-16-le")) // 2


def _truncate_windows_component(value: str, max_units: int) -> str:
    result: list[str] = []
    used = 0
    for character in value:
        character_units = _windows_units(character)
        if used + character_units > max_units:
            break
        result.append(character)
        used += character_units
    return "".join(result)


def sanitize_filename(value: object, *, max_length: int = 200) -> str:
    """替换 Windows 非法字符，并按 UTF-16 单元限制组件长度。"""
    safe = _INVALID_FILENAME.sub("_", str(value or "")).strip().rstrip(". ")
    safe = safe or "未命名"
    safe = _truncate_windows_component(safe, max_length).rstrip(". ")
    return safe or "未命名"


def create_directory_structure(
    output_dir: Path | str,
    artist_name: str,
    artist_id: int,
) -> Path:
    suffix = f"_{int(artist_id)}"
    artist_component = (
        sanitize_filename(
            artist_name,
            max_length=200 - _windows_units(suffix),
        )
        + suffix
    )
    artist_dir = Path(output_dir) / "artists" / artist_component
    artist_dir.mkdir(parents=True, exist_ok=True)
    return artist_dir


def get_save_path(output_dir: Path | str, artwork: Any) -> Path:
    user = _field(artwork, "user", {})
    artist_id = int(_field(user, "id"))
    artist_name = str(_field(user, "name", "未命名画师"))
    artist_dir = create_directory_structure(output_dir, artist_name, artist_id)
    stem = sanitize_filename(
        (
            f"{int(_field(artwork, 'id'))}_{artist_name}_"
            f"{_field(artwork, 'title', '未命名作品')}"
        ),
        max_length=200 - _windows_units(".metadata.json"),
    )
    return artist_dir / f"{stem}.jpg"


def metadata_path_for(save_path: Path | str) -> Path:
    path = Path(save_path)
    return path.with_suffix(".metadata.json")
