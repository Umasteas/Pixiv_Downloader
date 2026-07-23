"""Pixiv Ugoira ZIP、帧时序与格式转换。"""
from io import BytesIO
import subprocess
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zipfile import ZipFile

from PIL import Image


class UgoiraError(RuntimeError):
    """Ugoira 元数据、归档或转换无效。"""


FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"


@dataclass(frozen=True)
class FFmpegStatus:
    """ffmpeg 启动能力及面向用户的安装提示。"""

    available: bool
    path: str | None
    message: str
    download_url: str = FFMPEG_DOWNLOAD_URL


@dataclass(frozen=True)
class UgoiraFrame:
    """ZIP 中一帧及其显示时长。"""

    name: str
    delay_ms: int


def check_ffmpeg(
    configured_path: str = "auto",
    *,
    which=shutil.which,
    runner=subprocess.run,
) -> FFmpegStatus:
    """检查 PATH 或显式配置的 ffmpeg，不让探针失败阻止应用启动。"""
    executable = which("ffmpeg") if configured_path == "auto" else configured_path
    if not executable:
        return FFmpegStatus(False, None, "未检测到 ffmpeg，MP4 转换不可用。")
    try:
        runner(
            [str(executable), "-version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return FFmpegStatus(False, None, "未检测到可用的 ffmpeg，MP4 转换不可用。")
    return FFmpegStatus(True, str(executable), "ffmpeg 可用，可转换 MP4。")


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def parse_ugoira_frames(
    archive_path: Path | str,
    metadata: Iterable[Any],
) -> list[UgoiraFrame]:
    """按 Pixiv 元数据顺序验证 ZIP 帧及毫秒延迟。"""
    frames: list[UgoiraFrame] = []
    with ZipFile(archive_path) as archive:
        members = set(archive.namelist())
        for item in metadata:
            name = str(_field(item, "file", ""))
            path = PurePosixPath(name)
            delay = _field(item, "delay")
            if not name or path.name != name or name not in members:
                raise UgoiraError("Ugoira ZIP 缺少有效帧文件。")
            if type(delay) is not int or delay <= 0:
                raise UgoiraError("Ugoira 帧延迟必须是正整数。")
            frames.append(UgoiraFrame(name, delay))
    if not frames:
        raise UgoiraError("Ugoira 帧列表不能为空。")
    return frames


def convert_to_gif(
    archive_path: Path | str,
    metadata: Iterable[Any],
    output_path: Path | str,
) -> Path:
    """把 Ugoira ZIP 原子转换为保留帧延迟的循环 GIF。"""
    frames = parse_ugoira_frames(archive_path, metadata)
    images: list[Image.Image] = []
    with ZipFile(archive_path) as archive:
        for frame in frames:
            with Image.open(BytesIO(archive.read(frame.name))) as image:
                images.append(image.convert("RGBA"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    try:
        images[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=[frame.delay_ms for frame in frames],
            loop=0,
            disposal=2,
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        for image in images:
            image.close()
    return output


def _concat_file_line(path: Path) -> str:
    value = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{value}'"


def convert_to_mp4(
    archive_path: Path | str,
    metadata: Iterable[Any],
    output_path: Path | str,
    *,
    ffmpeg_path: str = "ffmpeg",
    runner=subprocess.run,
) -> Path:
    """用 ffmpeg concat demuxer 原子生成保留帧时序的 MP4。"""
    frames = parse_ugoira_frames(archive_path, metadata)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    try:
        with tempfile.TemporaryDirectory(
            prefix="ugoira-frames-",
            dir=output.parent,
        ) as workspace_value:
            workspace = Path(workspace_value)
            extracted: list[Path] = []
            with ZipFile(archive_path) as archive:
                for index, frame in enumerate(frames):
                    frame_path = workspace / f"{index:06d}{Path(frame.name).suffix}"
                    frame_path.write_bytes(archive.read(frame.name))
                    extracted.append(frame_path)
            manifest_lines: list[str] = []
            for frame_path, frame in zip(extracted, frames):
                manifest_lines.extend([
                    _concat_file_line(frame_path),
                    f"duration {frame.delay_ms / 1000:.6f}",
                ])
            manifest_lines.append(_concat_file_line(extracted[-1]))
            manifest = workspace / "frames.txt"
            manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
            runner(
                [
                    ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(manifest),
                    "-vsync",
                    "vfr",
                    "-vf",
                    "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temporary),
                ],
                check=True,
                capture_output=True,
            )
        if not temporary.is_file():
            raise UgoiraError("ffmpeg 未生成 MP4 文件。")
        temporary.replace(output)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UgoiraError("Ugoira MP4 转换失败。") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return output
