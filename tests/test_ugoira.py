"""Ugoira 下载与转换测试。"""
from io import BytesIO
from pathlib import Path
import subprocess
from zipfile import ZipFile

from PIL import Image
import pytest


def image_bytes(color):
    stream = BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, format="PNG")
    return stream.getvalue()


def archive_bytes():
    stream = BytesIO()
    with ZipFile(stream, "w") as bundle:
        bundle.writestr("0.png", image_bytes("red"))
        bundle.writestr("1.png", image_bytes("blue"))
    return stream.getvalue()


def test_parse_ugoira_frames_preserves_pixiv_order_and_delays(tmp_path):
    from src.pixiv_downloader.ugoira import parse_ugoira_frames

    archive = tmp_path / "ugoira.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("000001.jpg", b"first")
        bundle.writestr("000000.jpg", b"second")

    frames = parse_ugoira_frames(
        archive,
        [
            {"file": "000000.jpg", "delay": 80},
            {"file": "000001.jpg", "delay": 120},
        ],
    )

    assert [(frame.name, frame.delay_ms) for frame in frames] == [
        ("000000.jpg", 80),
        ("000001.jpg", 120),
    ]


def test_convert_to_gif_preserves_frame_order_and_delays(tmp_path):
    from src.pixiv_downloader.ugoira import convert_to_gif

    archive = tmp_path / "ugoira.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("0.png", image_bytes("red"))
        bundle.writestr("1.png", image_bytes("blue"))
    output = tmp_path / "ugoira.gif"

    convert_to_gif(
        archive,
        [{"file": "0.png", "delay": 80}, {"file": "1.png", "delay": 120}],
        output,
    )

    with Image.open(output) as animation:
        assert animation.n_frames == 2
        durations = []
        colors = []
        for index in range(animation.n_frames):
            animation.seek(index)
            durations.append(animation.info["duration"])
            colors.append(animation.convert("RGB").getpixel((0, 0)))
    assert durations == [80, 120]
    assert colors == [(255, 0, 0), (0, 0, 255)]
    assert not output.with_suffix(".gif.part").exists()


def test_convert_to_mp4_invokes_ffmpeg_with_frame_durations(tmp_path):
    from src.pixiv_downloader.ugoira import convert_to_mp4

    archive = tmp_path / "ugoira.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("0.png", image_bytes("red"))
        bundle.writestr("1.png", image_bytes("blue"))
    output = tmp_path / "ugoira.mp4"
    calls = []

    def fake_run(command, **options):
        manifest = command[command.index("-i") + 1]
        calls.append((command, options, open(manifest, encoding="utf-8").read()))
        open(command[-1], "wb").write(b"mock-mp4")
        return subprocess.CompletedProcess(command, 0)

    convert_to_mp4(
        archive,
        [{"file": "0.png", "delay": 80}, {"file": "1.png", "delay": 120}],
        output,
        ffmpeg_path="C:/tools/ffmpeg.exe",
        runner=fake_run,
    )

    command, options, manifest = calls[0]
    assert command[0] == "C:/tools/ffmpeg.exe"
    assert command[-1].endswith(".mp4.part")
    assert command[command.index("-vsync") + 1] == "vfr"
    assert command[command.index("-vf") + 1] == (
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p"
    )
    assert options == {"check": True, "capture_output": True}
    assert manifest.count("file '") == 3
    assert "duration 0.080000" in manifest
    assert "duration 0.120000" in manifest
    assert output.read_bytes() == b"mock-mp4"
    assert not output.with_suffix(".mp4.part").exists()


def test_check_ffmpeg_reports_available_and_missing_without_raising():
    from src.pixiv_downloader.ugoira import FFMPEG_DOWNLOAD_URL, check_ffmpeg

    calls = []

    def fake_run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0)

    available = check_ffmpeg(
        "auto",
        which=lambda name: "C:/tools/ffmpeg.exe",
        runner=fake_run,
    )
    missing = check_ffmpeg("auto", which=lambda name: None, runner=fake_run)

    assert available.available is True
    assert available.path == "C:/tools/ffmpeg.exe"
    assert calls == [
        (["C:/tools/ffmpeg.exe", "-version"], {
            "check": True,
            "capture_output": True,
            "timeout": 5,
        })
    ]
    assert missing.available is False
    assert missing.path is None
    assert "未检测到 ffmpeg" in missing.message
    assert missing.download_url == FFMPEG_DOWNLOAD_URL


@pytest.mark.asyncio
async def test_download_artwork_ugoira_saves_all_selected_formats(tmp_path):
    from src.pixiv_downloader.downloader import ArtistDownloader
    from src.pixiv_downloader.ugoira import FFmpegStatus

    candidate = {
        "id": 900,
        "type": "ugoira",
        "title": "动图作品",
        "page_count": 1,
        "user": {"id": 456, "name": "画师"},
        "tags": [],
        "total_bookmarks": 12,
    }

    class Api:
        def __init__(self):
            self.calls = []

        def ugoira_metadata(self, artwork_id):
            self.calls.append(artwork_id)
            return {"ugoira_metadata": {
                "zip_urls": {"medium": "https://i.pximg.net/ugoira.zip"},
                "frames": [
                    {"file": "0.png", "delay": 80},
                    {"file": "1.png", "delay": 120},
                ],
            }}

    class Database:
        def __init__(self):
            self.mark_calls = []

        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **record):
            self.mark_calls.append((artwork_id, record))

    class Response:
        status = 200

        def __init__(self):
            self.content = self
            self.content_length = len(archive_bytes())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def iter_chunked(self, size):
            yield archive_bytes()

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **options):
            self.calls.append((url, options))
            return Response()

    database = Database()
    session = Session()
    converter_calls = []

    def fake_gif(archive, frames, output):
        converter_calls.append(("gif", Path(archive).name, len(frames)))
        Path(output).write_bytes(b"gif")

    def fake_mp4(archive, frames, output, *, ffmpeg_path):
        converter_calls.append(("mp4", ffmpeg_path, len(frames)))
        Path(output).write_bytes(b"mp4")

    downloader = ArtistDownloader(
        Api(),
        database,
        proxy_manager=object(),
        output_dir=tmp_path,
        ugoira_options={
            "download_original": True,
            "convert_to_gif": True,
            "convert_to_mp4": True,
            "ffmpeg_path": "auto",
        },
        gif_converter=fake_gif,
        mp4_converter=fake_mp4,
        ffmpeg_checker=lambda path: FFmpegStatus(True, "C:/ffmpeg.exe", "ok"),
    )

    status = await downloader.download_artwork(
        candidate,
        session=session,
        request_options={},
        task_id=17,
    )

    artwork_dir = tmp_path / "artists" / "画师_456" / "900_动图作品"
    assert status == "downloaded"
    assert session.calls[0][0] == "https://i.pximg.net/ugoira.zip"
    assert (artwork_dir / "ugoira.zip").is_file()
    assert (artwork_dir / "ugoira.gif").read_bytes() == b"gif"
    assert (artwork_dir / "ugoira.mp4").read_bytes() == b"mp4"
    assert converter_calls == [
        ("gif", "ugoira.zip", 2),
        ("mp4", "C:/ffmpeg.exe", 2),
    ]
    assert database.mark_calls[0][0] == 900
    assert database.mark_calls[0][1]["file_path"] == str(artwork_dir)
    assert database.mark_calls[0][1]["task_id"] == 17


@pytest.mark.asyncio
async def test_ugoira_conversion_failure_keeps_zip_and_logs_safe_warning(tmp_path, caplog):
    from src.pixiv_downloader.downloader import ArtistDownloader

    candidate = {
        "id": 901,
        "type": "ugoira",
        "title": "转换失败",
        "page_count": 1,
        "user": {"id": 456, "name": "画师"},
        "tags": [],
        "total_bookmarks": 0,
    }

    class Database:
        def is_downloaded(self, artwork_id):
            return False

        def mark_as_downloaded(self, artwork_id, **record):
            self.record = record

    def fail_conversion(*args, **kwargs):
        raise RuntimeError("secret frame path C:/private/frame.png")

    downloader = ArtistDownloader(
        api=object(),
        database=Database(),
        proxy_manager=object(),
        output_dir=tmp_path,
        ugoira_options={
            "download_original": False,
            "convert_to_gif": True,
            "convert_to_mp4": False,
        },
        gif_converter=fail_conversion,
    )

    async def fake_download(artwork_id, save_path, **options):
        Path(save_path).write_bytes(archive_bytes())
        return [
            {"file": "0.png", "delay": 80},
            {"file": "1.png", "delay": 120},
        ]

    downloader.download_ugoira_zip = fake_download

    assert await downloader.download_artwork(candidate) == "downloaded"

    artwork_dir = tmp_path / "artists" / "画师_456" / "901_转换失败"
    assert (artwork_dir / "ugoira.zip").is_file()
    assert not (artwork_dir / "ugoira.gif").exists()
    assert "Ugoira GIF 转换失败，已保留 ZIP：artwork_id=901" in caplog.text
    assert "secret frame path" not in caplog.text
