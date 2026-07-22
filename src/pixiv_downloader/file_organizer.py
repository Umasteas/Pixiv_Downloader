"""Pixiv 下载文件的便携目录组织与安全命名。"""
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_INVALID_FILENAME = re.compile(r'[\x00-\x1f\\/:*?"<>|]')
_ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png"}
_WINDOWS_PATH_BUDGET = 240


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


def get_artwork_directory(output_dir: Path | str, artwork: Any) -> Path:
    """创建多图作品目录，并为目录内 metadata.json 预留路径预算。"""
    user = _field(artwork, "user", {})
    artist_dir = create_directory_structure(
        output_dir,
        str(_field(user, "name", "未命名画师")),
        int(_field(user, "id")),
    )
    prefix = f"{int(_field(artwork, 'id'))}_"
    reserved = _windows_units("\\metadata.json")
    available = (
        _WINDOWS_PATH_BUDGET
        - _windows_units(str(artist_dir.resolve()))
        - _windows_units("\\")
        - reserved
    )
    if available < _windows_units(prefix) + 1:
        raise ValueError("输出目录过长，无法创建多图作品目录")
    component = prefix + sanitize_filename(
        _field(artwork, "title", "未命名作品"),
        max_length=min(200 - _windows_units(prefix), available - _windows_units(prefix)),
    )
    artwork_dir = artist_dir / component
    artwork_dir.mkdir(parents=True, exist_ok=True)
    return artwork_dir


def get_page_save_path(artwork_dir: Path | str, page_index: int, url: str) -> Path:
    """根据受信扩展名生成 pN 文件路径。"""
    if page_index < 0:
        raise ValueError("页码不能为负数")
    extension = Path(urlparse(str(url)).path).suffix.lower()
    if extension not in _ALLOWED_IMAGE_EXTENSIONS:
        extension = ".jpg"
    return Path(artwork_dir) / f"p{page_index}{extension}"


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
