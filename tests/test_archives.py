"""Issue #18 任务完成后 ZIP 打包契约。"""
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import stat
import threading
import time
import zipfile
from zipfile import ZipFile

import pytest


def _archive_lock_worker(root, marker, queue):
    from src.pixiv_downloader.archives import archive_transaction

    with archive_transaction(root):
        collision = Path(marker).exists()
        Path(marker).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(0.1)
        Path(marker).unlink()
        queue.put(collision)


def test_create_task_archive_includes_single_metadata_and_multifile_directory(tmp_path):
    from src.pixiv_downloader.archives import create_task_archive

    downloads = tmp_path / "downloads"
    single = downloads / "artists" / "画师_1" / "101_单图.jpg"
    single.parent.mkdir(parents=True)
    single.write_bytes(b"image")
    single.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")

    multi = downloads / "artists" / "画师_1" / "102_多图"
    multi.mkdir()
    (multi / "p0.jpg").write_bytes(b"page-0")
    (multi / "p1.png").write_bytes(b"page-1")
    (multi / "metadata.json").write_text("{}", encoding="utf-8")

    result = create_task_archive(
        {
            "task_id": 7,
            "task_type": "artist",
            "query": {"artist_id": 123},
            "status": "completed",
            "completed_time": "2026-07-23 10:20:30",
        },
        [{"artwork_id": 101, "file_path": str(single)},
         {"artwork_id": 102, "file_path": str(multi)}],
        downloads,
    )

    assert result["filename"] == "artist_123_2026-07-23.zip"
    assert result["file_count"] == 5
    assert result["path"] == downloads / result["filename"]
    with ZipFile(result["path"]) as archive:
        assert archive.namelist() == [
            "artists/画师_1/101_单图.jpg",
            "artists/画师_1/101_单图.metadata.json",
            "artists/画师_1/102_多图/metadata.json",
            "artists/画师_1/102_多图/p0.jpg",
            "artists/画师_1/102_多图/p1.png",
        ]
        assert archive.read("artists/画师_1/102_多图/p1.png") == b"page-1"


def test_multiple_artwork_directories_validate_file_size_independently(tmp_path):
    from src.pixiv_downloader.archives import create_task_archive

    downloads = tmp_path / "downloads"
    records = []
    for artwork_id, content in ((101, b"one"), (102, b"two-two")):
        directory = downloads / str(artwork_id)
        directory.mkdir(parents=True)
        (directory / "p0.jpg").write_bytes(content)
        (directory / "metadata.json").write_text("{}", encoding="utf-8")
        records.append({
            "artwork_id": artwork_id,
            "file_path": str(directory),
            "file_size": len(content),
        })

    result = create_task_archive(
        {"task_id": 7, "task_type": "artist", "query": {"artist_id": 123},
         "status": "completed", "completed_time": "2026-07-23"},
        records,
        downloads,
    )

    assert result["file_count"] == 4


def test_create_task_archive_reports_each_written_file(tmp_path):
    from src.pixiv_downloader.archives import create_task_archive

    downloads = tmp_path / "downloads"
    source = downloads / "artists" / "画师_1" / "101_单图.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    events = []

    create_task_archive(
        {
            "task_id": 7,
            "task_type": "artist",
            "query": {"artist_id": 123},
            "status": "completed",
            "completed_time": "2026-07-23 10:20:30",
        },
        [{"artwork_id": 101, "file_path": str(source)}],
        downloads,
        progress=lambda current, total, filename: events.append(
            (current, total, filename)
        ),
    )

    assert events == [
        (1, 2, "artists/画师_1/101_单图.jpg"),
        (2, 2, "artists/画师_1/101_单图.metadata.json"),
    ]


def test_create_task_archive_deletes_originals_only_after_archive_succeeds(tmp_path):
    from src.pixiv_downloader.archives import create_task_archive

    downloads = tmp_path / "downloads"
    source = downloads / "artists" / "画师_1" / "101_单图.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")

    result = create_task_archive(
        {
            "task_id": 7,
            "task_type": "artist",
            "query": {"artist_id": 123},
            "status": "completed",
            "completed_time": "2026-07-23 10:20:30",
        },
        [{"artwork_id": 101, "file_path": str(source)}],
        downloads,
        delete_originals=True,
    )

    assert result["path"].is_file()
    assert not source.exists()
    assert not metadata.exists()
    with ZipFile(result["path"]) as archive:
        assert archive.read("artists/画师_1/101_单图.jpg") == b"image"


def test_create_task_archive_reports_partial_delete_without_losing_zip(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader.archives import create_task_archive

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"image")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")
    original_rename = os.rename

    def fail_metadata_delete(source_path, destination_path, *args, **kwargs):
        if Path(source_path) == metadata:
            raise PermissionError("busy")
        return original_rename(source_path, destination_path, *args, **kwargs)

    monkeypatch.setattr(os, "rename", fail_metadata_delete)
    result = create_task_archive(
        {
            "task_id": 7,
            "task_type": "artist",
            "query": {"artist_id": 123},
            "status": "completed",
            "completed_time": "2026-07-23 10:20:30",
        },
        [{"artwork_id": 101, "file_path": str(source)}],
        downloads,
        delete_originals=True,
    )

    assert result["path"].is_file()
    assert result["deleted_originals"] is False
    assert result["deleted_original_count"] == 1
    assert result["delete_failed_count"] == 1
    assert result["delete_warning"] == "部分原文件删除失败。"
    assert not source.exists()
    assert metadata.exists()


def test_create_task_archive_rejects_insufficient_disk_before_writing(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"image-bytes")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(archives.shutil, "disk_usage", lambda path: usage(100, 100, 0))

    with pytest.raises(archives.ArchiveError, match="磁盘空间不足"):
        archives.create_task_archive(
            {
                "task_id": 7,
                "task_type": "artist",
                "query": {"artist_id": 123},
                "status": "completed",
                "completed_time": "2026-07-23 10:20:30",
            },
            [{"artwork_id": 101, "file_path": str(source)}],
            downloads,
            delete_originals=True,
        )

    assert source.read_bytes() == b"image-bytes"
    assert not (downloads / "artist_123_2026-07-23.zip").exists()
    assert not (downloads / "artist_123_2026-07-23.zip.part").exists()


def test_create_task_archive_reserves_space_for_zip_overhead(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"image")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")
    source_bytes = source.stat().st_size + metadata.stat().st_size
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        archives.shutil,
        "disk_usage",
        lambda path: usage(source_bytes, 0, source_bytes),
    )

    with pytest.raises(archives.InsufficientDiskSpace):
        archives.create_task_archive(
            {
                "task_id": 7,
                "task_type": "artist",
                "query": {"artist_id": 123},
                "status": "completed",
                "completed_time": "2026-07-23 10:20:30",
            },
            [{"artwork_id": 101, "file_path": str(source)}],
            downloads,
        )


def test_create_task_archive_rejects_file_changed_while_streaming(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"original")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    original_open = archives.open_regular_file

    class MutatingReader:
        def __init__(self, stream, path):
            self.stream = stream
            self.path = path
            self.mutated = False

        def read(self, size=-1):
            data = self.stream.read(size)
            if data and not self.mutated:
                self.path.write_bytes(b"changed-content")
                self.mutated = True
            return data

        def fileno(self):
            return self.stream.fileno()

        def close(self):
            self.stream.close()

    def mutating_open(path, root=None):
        stream, identity = original_open(path, root)
        if path == source:
            stream = MutatingReader(stream, path)
        return stream, identity

    monkeypatch.setattr(archives, "open_regular_file", mutating_open)

    with pytest.raises(archives.ArchiveError, match="发生变化"):
        archives.create_task_archive(
            {
                "task_id": 7,
                "task_type": "artist",
                "query": {"artist_id": 123},
                "status": "completed",
                "completed_time": "2026-07-23 10:20:30",
            },
            [{"artwork_id": 101, "file_path": str(source)}],
            downloads,
        )

    assert not (downloads / "artist_123_2026-07-23.zip").exists()
    assert not (downloads / "artist_123_2026-07-23.zip.part").exists()


def test_create_task_archive_rejects_incomplete_artwork_metadata(tmp_path):
    from src.pixiv_downloader.archives import ArchiveError, create_task_archive

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"image")

    with pytest.raises(ArchiveError, match="metadata"):
        create_task_archive(
            {
                "task_id": 7,
                "task_type": "artist",
                "query": {"artist_id": 123},
                "status": "completed",
                "completed_time": "2026-07-23 10:20:30",
            },
            [{"artwork_id": 101, "file_path": str(source)}],
            downloads,
        )

    assert source.is_file()
    assert not (downloads / "artist_123_2026-07-23.zip").exists()


def test_create_task_archive_serializes_concurrent_writes(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    source = downloads / "image.jpg"
    downloads.mkdir()
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    class SlowZip:
        def __init__(self, path, *args, **kwargs):
            self.path = path

        def __enter__(self):
            nonlocal active, max_active
            self.path.write_bytes(b"")
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            return self

        def open(self, name, mode):
            outer = self

            class Target:
                def __enter__(self):
                    return self

                def write(self, data):
                    time.sleep(0.02)
                    with outer.path.open("ab") as stream:
                        stream.write(data)
                    return len(data)

                def __exit__(self, *args):
                    return None

            return Target()

        def __exit__(self, *args):
            nonlocal active
            with state_lock:
                active -= 1

    monkeypatch.setattr(archives, "ZipFile", SlowZip)
    task = {
        "task_id": 7,
        "task_type": "artist",
        "query": {"artist_id": 123},
        "status": "completed",
        "completed_time": "2026-07-23 10:20:30",
    }

    def create(index):
        return archives.create_task_archive(
            task,
            [{"artwork_id": 101, "file_path": str(source)}],
            downloads,
            filename=f"archive_{index}.zip",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))

    assert max_active == 1
    assert all(result["path"].is_file() for result in results)


def test_database_lists_only_downloads_owned_by_requested_task(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database = DatabaseManager(tmp_path / "history.db")
    try:
        target = database.create_task(task_type="artist", query={"artist_id": 1})
        other = database.create_task(task_type="artist", query={"artist_id": 2})
        database.mark_as_downloaded(
            101,
            task_id=target,
            file_path="downloads/101.jpg",
            tags=["目标"],
        )
        database.mark_as_downloaded(
            202,
            task_id=other,
            file_path="downloads/202.jpg",
            tags=["其他"],
        )

        records = database.list_task_downloads(target)

        assert [record["artwork_id"] for record in records] == [101]
        assert records[0]["file_path"] == "downloads/101.jpg"
        assert records[0]["tags"] == ["目标"]
    finally:
        database.close()


def test_archive_task_api_requires_token_and_returns_download_link(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module

    task = {
        "task_id": 7,
        "task_type": "artist",
        "query": {"artist_id": 123},
        "status": "completed",
        "completed_time": "2026-07-23 10:20:30",
    }

    class Database:
        archive = None

        def get_task(self, task_id):
            return task if task_id == 7 else None

        def get_task_archive(self, task_id):
            return self.archive

        def reserve_task_archive(self, task_id, filename, unavailable):
            self.archive = {
                "task_id": task_id,
                "filename": filename,
                "status": "reserved",
                "recovery_token": "ab" * 32,
            }
            return filename

        def list_task_downloads(self, task_id):
            assert task_id == 7
            return [{"artwork_id": 101, "file_path": "image.jpg"}]

        def complete_task_archive(self, task_id, **values):
            self.archive = {"task_id": task_id, "status": "complete", **values}
            return self.archive

        def fail_task_archive(self, task_id):
            return None

    calls = []
    events = []

    def fake_create(task_value, records, output_dir, **options):
        calls.append((task_value, records, output_dir, options["delete_originals"]))
        options["progress"](1, 1, "image.jpg")
        return {
            "filename": "artist_123_2026-07-23.zip",
            "path": tmp_path / "downloads" / "artist_123_2026-07-23.zip",
            "file_count": 1,
            "file_size": 42,
            "deleted_originals": False,
        }

    monkeypatch.setattr(app_module, "database_manager", Database())
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(tmp_path / "downloads")}},
    )
    monkeypatch.setattr(app_module, "create_task_archive_unlocked", fake_create)
    monkeypatch.setattr(app_module.socketio, "emit", lambda event, payload: events.append((event, payload)))
    client = app_module.app.test_client()

    denied = client.post("/api/tasks/7/archive", json={"delete_originals": False})
    response = client.post(
        "/api/tasks/7/archive",
        json={"delete_originals": False},
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "archive": {
            "filename": "artist_123_2026-07-23.zip",
            "file_count": 1,
            "file_size": 42,
            "deleted_originals": False,
            "download_url": "/api/archives/artist_123_2026-07-23.zip",
        },
    }
    assert calls == [(task, [{"artwork_id": 101, "file_path": "image.jpg"}], tmp_path / "downloads", False)]
    assert events == [("archive_progress", {
        "task_id": 7,
        "current": 1,
        "total": 1,
    })]


def test_download_archive_api_requires_token_and_returns_attachment(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archive = downloads / "artist_123_2026-07-23.zip"
    archive.write_bytes(b"zip-content")
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(downloads)}},
    )
    class Database:
        def get_task_archive_by_filename(self, filename):
            if filename != archive.name:
                return None
            return {
                "task_id": 7,
                "filename": filename,
                "file_count": 1,
                "file_size": archive.stat().st_size,
            }

    monkeypatch.setattr(app_module, "database_manager", Database())
    client = app_module.app.test_client()

    denied = client.get(f"/api/archives/{archive.name}")
    response = client.get(
        f"/api/archives/{archive.name}",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.data == b"zip-content"
    assert response.headers["Content-Disposition"].startswith(
        'attachment; filename=artist_123_2026-07-23.zip'
    )

    unregistered = downloads / "unregistered.zip"
    unregistered.write_bytes(b"secret")
    rejected = client.get(
        f"/api/archives/{unregistered.name}",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )
    assert rejected.status_code == 404


def test_download_archive_returns_json_when_archive_index_is_unavailable(monkeypatch):
    from src.pixiv_downloader import app as app_module

    class Database:
        def get_task_archive_by_filename(self, filename):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module, "database_manager", Database())
    response = app_module.app.test_client().get(
        "/api/archives/archive.zip",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )

    assert response.status_code == 500
    assert response.is_json
    assert response.get_json() == {
        "success": False,
        "error": "归档文件不可用。",
    }


def test_archive_projection_hides_reserved_corrupt_and_missing_records(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archive = downloads / "archive.zip"
    archive.write_bytes(b"zip")
    monkeypatch.setattr(
        app_module.config_manager,
        "load_config",
        lambda: {"download": {"output_dir": str(downloads)}},
    )
    task = {"task_id": 7, "status": "completed"}

    assert app_module._safe_task_archive(task, {
        "status": "reserved", "filename": archive.name,
        "file_count": 1, "file_size": archive.stat().st_size,
    }) is None
    assert app_module._safe_task_archive(task, {
        "status": "complete", "filename": archive.name,
        "file_count": "invalid", "file_size": archive.stat().st_size,
    }) is None
    assert app_module._safe_task_archive(task, {
        "status": "complete", "filename": "missing.zip",
        "file_count": 1, "file_size": 3,
    }) is None


def test_archive_rejects_source_symlink_inside_download_root(tmp_path):
    from src.pixiv_downloader.archives import ArchiveError, create_task_archive

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"secret")
    link = downloads / "image.jpg"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 环境不支持创建符号链接")
    with pytest.raises(ArchiveError, match="符号链接"):
        create_task_archive(
            {"task_id": 9, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(link)}], downloads,
        )


def test_task_archive_names_are_persistently_unique(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    try:
        first = database.create_task(task_type="artist", query={"artist_id": 1})
        second = database.create_task(task_type="artist", query={"artist_id": 1})
        base = "artist_1_2026-07-23.zip"
        assert database.reserve_task_archive(first, base) == base
        assert database.reserve_task_archive(second, base) == f"artist_1_2026-07-23_{second}.zip"
        assert database.reserve_task_archive(first, base) == base
        database.complete_task_archive(
            first,
            filename=base,
            file_count=2,
            file_size=42,
            deleted_originals=False,
            deleted_original_count=0,
            delete_failed_count=0,
        )
    finally:
        database.close()

    reopened = DatabaseManager(database_path)
    try:
        assert reopened.get_task_archive(first)["filename"] == base
        assert reopened.get_task_archive_by_filename(base)["task_id"] == first
    finally:
        reopened.close()


def test_download_archive_rejects_symlink(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"secret")
    link = downloads / "archive.zip"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 环境不支持创建符号链接")
    monkeypatch.setattr(app_module.config_manager, "load_config",
                        lambda: {"download": {"output_dir": str(downloads)}})
    class Database:
        def get_task_archive_by_filename(self, filename):
            return {
                "task_id": 7,
                "filename": filename,
                "file_count": 1,
                "file_size": outside.stat().st_size,
            }

    monkeypatch.setattr(app_module, "database_manager", Database())
    response = app_module.app.test_client().get(
        "/api/archives/archive.zip",
        headers={"X-App-Token": app_module.SECURITY_TOKEN},
    )
    assert response.status_code == 404

def test_completed_task_frontend_exposes_archive_progress_delete_and_download():
    script = (
        Path(__file__).resolve().parents[1] / "static" / "app.js"
    ).read_text(encoding="utf-8")
    archive_module = (
        Path(__file__).resolve().parents[1] / "static" / "archive_ui.js"
    ).read_text(encoding="utf-8")
    template = (
        Path(__file__).resolve().parents[1] / "templates" / "index.html"
    ).read_text(encoding="utf-8")

    assert "data-task-archive" in script
    assert "'打包为 ZIP'" in script
    detail_renderer = script.split("const renderTaskDetail", 1)[1].split(
        "const formatEta", 1
    )[0]
    assert "'打包为 ZIP'" in detail_renderer
    assert "dataset.archiveDownload" in detail_renderer
    assert "dataset.deleteAfterArchive" in detail_renderer
    assert "data-delete-after-archive" in script
    assert "打包成功后删除原文件" in script
    assert "socket.on('archive_progress'" in script
    assert "window.confirm('ZIP 创建成功后将删除原文件，确定继续吗？')" in script
    assert "X-App-Token': appToken" in script
    assert "data-archive-download" in script
    assert "下载 ZIP" in script
    assert "archiveDeleteSelections" in script
    assert "scheduler(() => urlApi.revokeObjectURL" in archive_module
    assert "filename='archive_ui.js'" in template


def _completed_task_with_file(database, downloads):
    source = downloads / "artists" / "画师_1" / "101.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    task_id = database.create_task(
        task_type="artist",
        query={"artist_id": 123},
        total_count=1,
    )
    database.transition_task(task_id, "pending", "running")
    database.mark_as_downloaded(
        101,
        task_id=task_id,
        file_path=str(source),
        file_size=source.stat().st_size,
    )
    database.record_task_result(task_id, "downloaded")
    database.transition_task(task_id, "running", "completed")
    return task_id, source


def test_archive_api_real_database_zip_download_and_restart_round_trip(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.archives import _archive_filename
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    reopened = None
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        base = _archive_filename(task)
        (downloads / base).parent.mkdir(parents=True, exist_ok=True)
        (downloads / base).write_bytes(b"unregistered")
        (downloads / f"{Path(base).stem}_{task_id}.zip").write_bytes(b"also occupied")
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        monkeypatch.setattr(app_module.socketio, "emit", lambda *args: None)
        client = app_module.app.test_client()
        headers = {"X-App-Token": app_module.SECURITY_TOKEN}

        created = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers=headers,
        )
        assert created.status_code == 200
        archive = created.get_json()["archive"]
        assert archive["filename"] not in {base, f"{Path(base).stem}_{task_id}.zip"}
        assert database.get_task_archive(task_id)["status"] == "complete"

        repeated = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers=headers,
        )
        assert repeated.status_code == 200
        assert repeated.get_json()["archive"]["filename"] == archive["filename"]

        late_delete = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": True},
            headers=headers,
        )
        assert late_delete.status_code == 409
        assert "不能事后删除" in late_delete.get_json()["error"]

        downloaded = client.get(archive["download_url"], headers=headers)
        assert downloaded.status_code == 200
        with ZipFile(downloads / archive["filename"]) as package:
            assert package.read("artists/画师_1/101.jpg") == b"image"
        assert downloaded.data == (downloads / archive["filename"]).read_bytes()

        database.close()
        reopened = DatabaseManager(database_path)
        monkeypatch.setattr(app_module, "database_manager", reopened)
        monkeypatch.setattr(app_module.task_manager, "list_tasks", reopened.list_tasks)
        refreshed = client.get("/api/tasks")
        restored = next(item for item in refreshed.get_json()["tasks"] if item["task_id"] == task_id)
        assert restored["archive"]["filename"] == archive["filename"]
    finally:
        if reopened is not None:
            reopened.close()
        else:
            database.close()


def test_archive_api_commit_failure_cleans_zip_keeps_sources_and_can_retry(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, source = _completed_task_with_file(database, downloads)
        original_complete = database.complete_task_archive
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        monkeypatch.setattr(app_module.socketio, "emit", lambda *args: None)
        monkeypatch.setattr(database, "complete_task_archive", lambda *args, **kwargs: None)
        client = app_module.app.test_client()
        headers = {"X-App-Token": app_module.SECURITY_TOKEN}

        failed = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": True},
            headers=headers,
        )
        assert failed.status_code == 500
        assert failed.is_json
        assert source.is_file()
        assert not list(downloads.glob("*.zip"))
        assert database.get_task_archive(task_id)["status"] == "failed"

        monkeypatch.setattr(database, "complete_task_archive", original_complete)
        retried = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers=headers,
        )
        assert retried.status_code == 200
        assert database.get_task_archive(task_id)["status"] == "complete"
    finally:
        database.close()


def test_archive_api_recovers_zip_committed_before_database_complete(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.archives import _archive_filename, create_task_archive
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, _archive_filename(task))
        recovery_token = database.get_task_archive(task_id)["recovery_token"]
        result = create_task_archive(
            task,
            database.list_task_downloads(task_id),
            downloads,
            filename=filename,
            recovery_token=recovery_token,
        )
        with ZipFile(result["path"]) as package:
            assert recovery_token.encode("ascii") not in package.comment
        before = result["path"].stat().st_mtime_ns
        assert database.get_task_archive(task_id)["status"] == "reserved"
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 200
        assert response.get_json()["archive"]["filename"] == filename
        assert result["path"].stat().st_mtime_ns == before
        assert database.get_task_archive(task_id)["status"] == "complete"
        assert source.is_file()
    finally:
        database.close()


def test_archive_api_deletes_originals_only_after_complete_commit(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, source = _completed_task_with_file(database, downloads)
        metadata = source.with_suffix(".metadata.json")
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        monkeypatch.setattr(app_module.socketio, "emit", lambda *args: None)

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": True},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 200
        record = database.get_task_archive(task_id)
        assert record["status"] == "complete"
        assert record["deleted_originals"] == 1
        assert record["deleted_original_count"] == 2
        assert not source.exists()
        assert not metadata.exists()
        assert (downloads / record["filename"]).is_file()
    finally:
        database.close()


def test_archive_lock_serializes_independent_processes(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    marker = tmp_path / "active.marker"
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_archive_lock_worker, args=(root, marker, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=2) for _ in processes) == [False, False]


def test_safe_open_rejects_symlink_mode_without_symlink_privilege(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    source = tmp_path / "image.jpg"
    source.write_bytes(b"image")
    original_lstat = archives.os.lstat

    def fake_lstat(path):
        value = original_lstat(path)
        if Path(path) == source:
            values = list(value)
            values[0] = stat.S_IFLNK | 0o777
            return os.stat_result(values)
        return value

    monkeypatch.setattr(archives.os, "lstat", fake_lstat)
    with pytest.raises(archives.ArchiveError, match="普通文件"):
        archives.open_regular_file(source)


def test_archive_rejects_plain_file_outside_download_root(tmp_path):
    from src.pixiv_downloader.archives import ArchiveError, create_task_archive

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"secret")
    with pytest.raises(ArchiveError, match="下载目录"):
        create_task_archive(
            {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(outside)}],
            downloads,
        )


def test_archive_write_failure_removes_unique_temporary_file(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        zipfile._ZipWriteFile,
        "write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    with pytest.raises(archives.ArchiveIOError):
        archives.create_task_archive(
            {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(source)}],
            downloads,
        )
    assert not list(downloads.glob("*.part"))
    assert not list(downloads.glob("*.zip"))


def test_archive_atomic_commit_never_overwrites_racing_target(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    destination = downloads / "archive.zip"
    original_link = archives.os.link

    def racing_link(temporary, target):
        Path(target).write_bytes(b"external-writer")
        return original_link(temporary, target)

    monkeypatch.setattr(archives.os, "link", racing_link)
    with pytest.raises(archives.ArchiveIOError):
        archives.create_task_archive(
            {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(source)}],
            downloads,
            filename=destination.name,
        )
    assert destination.read_bytes() == b"external-writer"
    assert not list(downloads.glob("*.part"))


def test_archive_enforces_file_count_limit(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 1)
    with pytest.raises(archives.ArchiveError, match="文件数量"):
        archives.create_task_archive(
            {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(source)}],
            downloads,
        )


def test_archive_enforces_total_size_depth_and_runtime_limits(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    source = downloads / "nested" / "image.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    task = {
        "task_id": 1,
        "task_type": "artist",
        "query": {"artist_id": 1},
        "status": "completed",
        "completed_time": "2026-07-23",
    }
    records = [{"file_path": str(source)}]

    monkeypatch.setattr(archives, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(archives.ArchiveError, match="总大小"):
        archives.create_task_archive(task, records, downloads)

    monkeypatch.setattr(archives, "MAX_ARCHIVE_BYTES", 1024)
    monkeypatch.setattr(archives, "MAX_ARCHIVE_PATH_DEPTH", 1)
    with pytest.raises(archives.ArchiveError, match="路径层级"):
        archives.create_task_archive(task, records, downloads)

    monkeypatch.setattr(archives, "MAX_ARCHIVE_PATH_DEPTH", 32)
    monkeypatch.setattr(archives, "MAX_ARCHIVE_SECONDS", 1)
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(archives.time, "monotonic", lambda: next(ticks))
    with pytest.raises(archives.ArchiveError, match="耗时"):
        archives.create_task_archive(task, records, downloads)
    assert not list(downloads.glob("*.part"))


def test_delete_originals_preserves_file_replaced_after_archive(tmp_path):
    from src.pixiv_downloader.archives import (
        create_task_archive,
        delete_archive_sources,
    )

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"old")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")
    result = create_task_archive(
        {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
         "status": "completed", "completed_time": "2026-07-23"},
        [{"file_path": str(source)}],
        downloads,
    )
    replacement = downloads / "replacement.jpg"
    replacement.write_bytes(b"new")
    os.replace(replacement, source)

    delete_archive_sources(result)

    assert source.read_bytes() == b"new"
    assert result["delete_failed_count"] == 1
    assert not metadata.exists()


def test_delete_originals_preserves_replacement_racing_with_quarantine(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"old")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")
    result = archives.create_task_archive(
        {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
         "status": "completed", "completed_time": "2026-07-23"},
        [{"file_path": str(source)}],
        downloads,
    )
    original_rename = os.rename

    def replace_then_quarantine(source_path, destination_path):
        if Path(source_path) == source:
            source.write_bytes(b"replacement")
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(os, "rename", replace_then_quarantine)
    archives.delete_archive_sources(result)

    assert source.read_bytes() == b"replacement"
    assert not metadata.exists()
    assert result["deleted_original_count"] == 1
    assert result["delete_failed_count"] == 1


def test_delete_originals_restores_file_when_parent_chain_changes(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"old")
    metadata = source.with_suffix(".metadata.json")
    metadata.write_text("{}", encoding="utf-8")
    result = archives.create_task_archive(
        {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
         "status": "completed", "completed_time": "2026-07-23"},
        [{"file_path": str(source)}],
        downloads,
    )
    original_assert_parent_chain = archives._assert_parent_chain

    def fail_source_parent_chain(root, path, identities):
        if path == source:
            raise archives.ArchiveError("changed")
        return original_assert_parent_chain(root, path, identities)

    monkeypatch.setattr(archives, "_assert_parent_chain", fail_source_parent_chain)

    archives.delete_archive_sources(result)

    assert source.read_bytes() == b"old"
    assert not metadata.exists()
    assert result["deleted_original_count"] == 1
    assert result["delete_failed_count"] == 1


def test_archive_api_does_not_adopt_zip_with_wrong_recovery_nonce(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.archives import _archive_filename
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, _archive_filename(task))
        external = downloads / filename
        with ZipFile(external, "w") as package:
            package.write(source, source.relative_to(downloads).as_posix())
            metadata = source.with_suffix(".metadata.json")
            package.write(metadata, metadata.relative_to(downloads).as_posix())
            package.comment = f"pixiv-downloader-task:{task_id}:{'00' * 32}".encode()
        original_bytes = external.read_bytes()
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        monkeypatch.setattr(app_module.socketio, "emit", lambda *args: None)

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 409
        assert "不能自动重建" in response.get_json()["error"]
        assert external.read_bytes() == original_bytes
        assert database.get_task_archive(task_id)["status"] == "reserved"
        assert len(list(downloads.glob("*.zip"))) == 1
    finally:
        database.close()


def test_archive_api_blocks_rebuild_when_complete_zip_still_exists(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        monkeypatch.setattr(app_module.socketio, "emit", lambda *args: None)
        client = app_module.app.test_client()
        headers = {"X-App-Token": app_module.SECURITY_TOKEN}
        created = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers=headers,
        )
        filename = created.get_json()["archive"]["filename"]
        archive_path = downloads / filename
        archive_path.write_bytes(archive_path.read_bytes() + b"corrupt-record-size")

        retried = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers=headers,
        )

        assert retried.status_code == 409
        assert "不能自动重建" in retried.get_json()["error"]
        assert database.get_task_archive(task_id)["status"] == "complete"
        assert len(list(downloads.glob("*.zip"))) == 1
    finally:
        database.close()


def test_archive_recovery_rejects_tampered_content_with_copied_hmac(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.archives import _archive_filename, create_task_archive
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, _archive_filename(task))
        token = database.get_task_archive(task_id)["recovery_token"]
        result = create_task_archive(
            task,
            database.list_task_downloads(task_id),
            downloads,
            filename=filename,
            recovery_token=token,
        )
        with ZipFile(result["path"]) as original:
            comment = original.comment
            entries = {item.filename: original.read(item) for item in original.infolist()}
        image_name = next(name for name in entries if name.endswith(".jpg"))
        entries[image_name] = b"x" * len(entries[image_name])
        with ZipFile(result["path"], "w") as tampered:
            for name, content in entries.items():
                tampered.writestr(name, content)
            tampered.comment = comment
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 409
        assert "不能自动重建" in response.get_json()["error"]
        assert database.get_task_archive(task_id)["status"] == "reserved"
    finally:
        database.close()


def test_archive_rollback_preserves_replacement_file(tmp_path):
    from src.pixiv_downloader.archives import create_task_archive, rollback_task_archive

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"image")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    result = create_task_archive(
        {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
         "status": "completed", "completed_time": "2026-07-23"},
        [{"file_path": str(source)}],
        downloads,
    )
    replacement = downloads / "replacement.zip"
    replacement.write_bytes(b"external replacement")
    os.replace(replacement, result["path"])

    removed = rollback_task_archive(result)

    assert removed is False
    assert result["path"].read_bytes() == b"external replacement"


def test_archive_recovery_rejects_duplicate_entry_names(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.archives import _archive_filename, create_task_archive
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, _archive_filename(task))
        token = database.get_task_archive(task_id)["recovery_token"]
        result = create_task_archive(
            task,
            database.list_task_downloads(task_id),
            downloads,
            filename=filename,
            recovery_token=token,
        )
        with ZipFile(result["path"]) as original:
            comment = original.comment
            entries = {item.filename: original.read(item) for item in original.infolist()}
        image_name = next(name for name in entries if name.endswith(".jpg"))
        with pytest.warns(UserWarning, match="Duplicate name"):
            with ZipFile(result["path"], "w") as duplicated:
                duplicated.writestr(image_name, b"x" * len(entries[image_name]))
                for name, content in entries.items():
                    duplicated.writestr(name, content)
                duplicated.comment = comment
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 409
        assert database.get_task_archive(task_id)["status"] == "reserved"
    finally:
        database.close()


def test_archive_recovery_enforces_file_count_limit(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader import archives
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, archives._archive_filename(task))
        token = database.get_task_archive(task_id)["recovery_token"]
        result = archives.create_task_archive(
            task,
            database.list_task_downloads(task_id),
            downloads,
            filename=filename,
            recovery_token=token,
        )
        with ZipFile(result["path"], "a") as package:
            package.writestr("extra.txt", b"extra")
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 2)
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )

        response = app_module.app.test_client().post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )

        assert response.status_code == 409
        assert database.get_task_archive(task_id)["status"] == "reserved"
    finally:
        database.close()


def test_archive_creation_rejects_source_replaced_after_initial_snapshot(
    tmp_path,
    monkeypatch,
):
    from src.pixiv_downloader import archives

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    source = downloads / "image.jpg"
    source.write_bytes(b"old")
    source.with_suffix(".metadata.json").write_text("{}", encoding="utf-8")
    original_open = archives.open_regular_file
    replaced = False

    def replace_before_archive_read(path, root):
        nonlocal replaced
        if Path(path) == source and not replaced:
            replaced = True
            replacement = downloads / "replacement.jpg"
            replacement.write_bytes(b"new")
            os.replace(replacement, source)
        return original_open(path, root)

    monkeypatch.setattr(archives, "open_regular_file", replace_before_archive_read)

    with pytest.raises(archives.ArchiveError, match="创建期间发生变化"):
        archives.create_task_archive(
            {"task_id": 1, "task_type": "artist", "query": {"artist_id": 1},
             "status": "completed", "completed_time": "2026-07-23"},
            [{"file_path": str(source)}],
            downloads,
        )

    assert not list(downloads.glob("*.zip"))
    assert not list(downloads.glob(".*-*.part"))


def test_archive_recovery_rechecks_sources_after_zip_validation(tmp_path, monkeypatch):
    from src.pixiv_downloader import archives
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, source = _completed_task_with_file(database, downloads)
        task = database.get_task(task_id)
        filename = database.reserve_task_archive(task_id, archives._archive_filename(task))
        token = database.get_task_archive(task_id)["recovery_token"]
        archives.create_task_archive(
            task,
            database.list_task_downloads(task_id),
            downloads,
            filename=filename,
            recovery_token=token,
        )
        original_open = archives.open_regular_file
        source_opens = 0

        def replace_before_final_source_check(path, root):
            nonlocal source_opens
            if Path(path) == source:
                source_opens += 1
                if source_opens == 2:
                    replacement = downloads / "replacement.jpg"
                    replacement.write_bytes(b"new-image")
                    os.replace(replacement, source)
            return original_open(path, root)

        monkeypatch.setattr(
            archives,
            "open_regular_file",
            replace_before_final_source_check,
        )

        recovered = archives.recover_task_archive(
            task,
            database.get_task_archive(task_id),
            database.list_task_downloads(task_id),
            downloads,
        )

        assert recovered is None
        assert source.read_bytes() == b"new-image"
        assert database.get_task_archive(task_id)["status"] == "reserved"
    finally:
        database.close()


def test_task_detail_exposes_completed_archive(tmp_path, monkeypatch):
    from src.pixiv_downloader import app as app_module
    from src.pixiv_downloader.database import DatabaseManager

    downloads = tmp_path / "downloads"
    database = DatabaseManager(tmp_path / "history.db")
    try:
        task_id, _source = _completed_task_with_file(database, downloads)
        monkeypatch.setattr(app_module, "database_manager", database)
        monkeypatch.setattr(
            app_module.config_manager,
            "load_config",
            lambda: {"download": {"output_dir": str(downloads)}},
        )
        client = app_module.app.test_client()
        created = client.post(
            f"/api/tasks/{task_id}/archive",
            json={"delete_originals": False},
            headers={"X-App-Token": app_module.SECURITY_TOKEN},
        )
        assert created.status_code == 200

        detail = client.get(f"/api/tasks/{task_id}")

        assert detail.status_code == 200
        archive = detail.get_json()["task"]["archive"]
        assert archive["filename"] == created.get_json()["archive"]["filename"]
        assert archive["download_url"].endswith(archive["filename"])
    finally:
        database.close()
