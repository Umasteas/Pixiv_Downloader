"""已完成下载任务的安全 ZIP 归档。"""
from __future__ import annotations

from datetime import date
import errno
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import stat
import threading
import tempfile
import time
import zlib
from contextlib import contextmanager
from typing import Any, Callable, Iterable
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .file_organizer import sanitize_filename


class ArchiveError(RuntimeError):
    """任务状态、文件清单或归档写入无效。"""


class ArchiveIOError(ArchiveError):
    """归档文件系统操作失败。"""


class InsufficientDiskSpace(ArchiveError):
    """下载根所在磁盘没有足够空间写入归档。"""


_ARCHIVE_LOCK = threading.Lock()
MAX_ARCHIVE_FILES = 100000
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024 * 1024
MAX_ARCHIVE_PATH_DEPTH = 32
MAX_ARCHIVE_SECONDS = 24 * 60 * 60


@contextmanager
def _archive_process_lock(root: Path):
    """使用 stdlib 文件锁跨线程/进程串行化选名、写入和删除。"""
    lock_path = root / ".pixiv-archive.lock"
    if not lock_path.exists():
        initial = tempfile.NamedTemporaryFile(
            mode="w+b", prefix=".pixiv-archive-lock-", suffix=".init",
            dir=root, delete=False,
        )
        initial_path = Path(initial.name)
        try:
            initial.write(b"\0")
            initial.flush()
            os.fsync(initial.fileno())
            initial.close()
            try:
                os.link(initial_path, lock_path)
            except FileExistsError:
                pass
        finally:
            initial.close()
            initial_path.unlink(missing_ok=True)
    try:
        lock_stat = os.lstat(lock_path)
        if not stat.S_ISREG(lock_stat.st_mode) or stat.S_ISLNK(lock_stat.st_mode):
            raise ArchiveIOError("归档锁文件无效。")
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags)
        opened_stat = os.fstat(fd)
        if _file_identity(opened_stat) != _file_identity(lock_stat):
            os.close(fd)
            raise ArchiveIOError("归档锁文件在打开时发生变化。")
        handle = os.fdopen(fd, "r+b")
    except OSError as exc:
        raise ArchiveIOError("归档锁初始化失败。") from exc
    if os.fstat(handle.fileno()).st_size < 1:
        handle.close()
        raise ArchiveIOError("归档锁初始化失败。")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked and os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def archive_transaction(output_dir):
    """锁住一次归档的选名、写入、提交与可选删除。"""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _ARCHIVE_LOCK, _archive_process_lock(root):
        yield root


def _task_query_name(task: dict[str, Any]) -> str:
    query = task.get("query")
    if not isinstance(query, dict):
        raise ArchiveError("任务查询无效。")
    task_type = task.get("task_type")
    values = {
        "artist": query.get("artist_id"),
        "search": query.get("keyword"),
        "ranking": query.get("ranking_type"),
        "bookmarks": query.get("visibility"),
        "follow_latest": "selected",
        "retry_failed": f"task-{query.get('source_task_id')}",
    }
    value = values.get(task_type)
    if value is None or isinstance(value, bool):
        raise ArchiveError("任务查询无效。")
    return sanitize_filename(value, max_length=80)


def _archive_filename(task: dict[str, Any]) -> str:
    if task.get("status") != "completed":
        raise ArchiveError("只能打包已完成任务。")
    task_type = str(task.get("task_type") or "")
    if not task_type:
        raise ArchiveError("任务类型无效。")
    completed = str(task.get("completed_time") or "")[:10]
    try:
        completed_date = date.fromisoformat(completed).isoformat()
    except ValueError:
        raise ArchiveError("任务完成时间无效。") from None
    return sanitize_filename(
        f"{task_type}_{_task_query_name(task)}_{completed_date}", max_length=180
    ) + ".zip"


def _archive_marker(task: dict[str, Any], recovery_token: str, manifest) -> bytes:
    task_id = task.get("task_id")
    if type(task_id) is not int or task_id <= 0:
        raise ArchiveError("任务 ID 无效。")
    if len(recovery_token) != 64:
        raise ArchiveError("归档恢复凭据无效。")
    try:
        bytes.fromhex(recovery_token)
    except ValueError:
        raise ArchiveError("归档恢复凭据无效。") from None
    payload = "\n".join(
        f"{name}\0{size}\0{digest}"
        for name, (size, digest) in sorted(manifest.items())
    ).encode("utf-8")
    signature = hmac.new(
        bytes.fromhex(recovery_token),
        str(task_id).encode("ascii") + b"\n" + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"pixiv-downloader-task:{task_id}:{signature}".encode("ascii")


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _parent_chain_identities(root: Path, path: Path):
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ArchiveError("任务文件超出下载目录。") from None
    root_value = os.lstat(root)
    if stat.S_ISLNK(root_value.st_mode) or not stat.S_ISDIR(root_value.st_mode):
        raise ArchiveError("下载目录无效。")
    identities = [(root, (root_value.st_dev, root_value.st_ino))]
    current = root
    for part in relative.parts[:-1]:
        current /= part
        value = os.lstat(current)
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
            raise ArchiveError("任务文件父目录无效。")
        identities.append((current, (value.st_dev, value.st_ino)))
    return identities


def _assert_parent_chain(root: Path, path: Path, identities) -> None:
    if _parent_chain_identities(root, path) != identities:
        raise ArchiveError("任务文件父目录在读取时发生变化。")


def _assert_regular_identity(
    path: Path,
    before: os.stat_result,
    fd: int,
) -> os.stat_result:
    try:
        current = os.lstat(path)
        opened = os.fstat(fd)
    except OSError as exc:
        raise ArchiveError("任务文件在归档过程中发生变化。") from exc
    if any(
        not stat.S_ISREG(value.st_mode)
        for value in (before, current, opened)
    ):
        raise ArchiveError("任务文件必须是普通文件。")
    if len({_file_identity(value) for value in (before, current, opened)}) != 1:
        raise ArchiveError("任务文件在归档过程中发生变化。")
    return opened


def open_regular_file(path: Path, root: Path | None = None):
    parent_identities = _parent_chain_identities(root, path) if root is not None else None
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ArchiveError("任务文件不可访问。") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ArchiveError("任务文件必须是根目录内的普通文件。")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ArchiveError("任务文件不可访问。") from exc
    try:
        after = _assert_regular_identity(path, before, fd)
        if parent_identities is not None:
            _assert_parent_chain(root, path, parent_identities)
        return os.fdopen(fd, "rb"), after
    except Exception:
        os.close(fd)
        raise


_open_regular_file = open_regular_file


def _source_files(records: Iterable[dict[str, Any]], root: Path) -> list[Path]:
    files: set[Path] = set()
    for record in records:
        raw_path = record.get("file_path")
        if not raw_path:
            raise ArchiveError("任务下载记录缺少文件路径。")
        source = Path(str(raw_path))
        candidate = source if source.is_absolute() else root / source
        try:
            candidate_stat = os.lstat(candidate)
        except OSError as exc:
            raise ArchiveError("任务文件不存在。") from exc
        if stat.S_ISLNK(candidate_stat.st_mode):
            raise ArchiveError("任务文件不能是符号链接。")
        source = candidate.resolve(strict=False)
        try:
            source.relative_to(root)
        except ValueError:
            raise ArchiveError("任务文件不在下载目录内。") from None
        if len(source.relative_to(root).parts) > MAX_ARCHIVE_PATH_DEPTH:
            raise ArchiveError("任务文件路径层级超过限制。")
        try:
            source_stat = os.lstat(source)
        except OSError as exc:
            raise ArchiveError("任务文件不存在。") from exc
        if stat.S_ISLNK(source_stat.st_mode):
            raise ArchiveError("任务文件不能是符号链接。")
        if stat.S_ISREG(source_stat.st_mode):
            expected_size = record.get("file_size")
            if expected_size is not None and int(expected_size) != source_stat.st_size:
                raise ArchiveError("任务文件大小与数据库记录不匹配。")
            files.add(source)
            metadata = source.with_suffix(".metadata.json")
            try:
                metadata_stat = os.lstat(metadata)
            except OSError as exc:
                raise ArchiveError("任务文件缺少 metadata。") from exc
            if stat.S_ISLNK(metadata_stat.st_mode) or not stat.S_ISREG(metadata_stat.st_mode):
                raise ArchiveError("任务 metadata 必须是普通文件。")
            files.add(metadata)
        elif stat.S_ISDIR(source_stat.st_mode):
            metadata = source / "metadata.json"
            try:
                metadata_stat = os.lstat(metadata)
            except OSError as exc:
                raise ArchiveError("任务文件缺少 metadata。") from exc
            if stat.S_ISLNK(metadata_stat.st_mode) or not stat.S_ISREG(metadata_stat.st_mode):
                raise ArchiveError("任务 metadata 必须是普通文件。")
            directory_files = []
            for path in source.rglob("*"):
                try:
                    item = os.lstat(path)
                except OSError as exc:
                    raise ArchiveError("任务文件不可访问。") from exc
                if stat.S_ISLNK(item.st_mode):
                    raise ArchiveError("任务目录不能包含符号链接。")
                if stat.S_ISREG(item.st_mode):
                    files.add(path)
                    directory_files.append((path, item.st_size))
                    if len(files) > MAX_ARCHIVE_FILES:
                        raise ArchiveError("任务文件数量超过限制。")
            expected_size = record.get("file_size")
            if expected_size is not None:
                actual_size = sum(
                    size for path, size in directory_files if path != metadata
                )
                if int(expected_size) != actual_size:
                    raise ArchiveError("任务文件大小与数据库记录不匹配。")
        else:
            raise ArchiveError("任务文件不存在。")
    if not files:
        raise ArchiveError("任务没有可打包文件。")
    if len(files) > MAX_ARCHIVE_FILES:
        raise ArchiveError("任务文件数量超过限制。")
    for path in files:
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise ArchiveError("任务文件不在下载目录内。") from None
        if len(relative.parts) > MAX_ARCHIVE_PATH_DEPTH:
            raise ArchiveError("任务文件路径层级超过限制。")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _required_archive_space(files: Iterable[Path], root: Path) -> int:
    """保守估算 DEFLATE 最坏膨胀与 ZIP 目录/临时文件开销。"""
    total = 0
    entry_overhead = 0
    for path in files:
        size = os.lstat(path).st_size
        total += size
        if total > MAX_ARCHIVE_BYTES:
            raise ArchiveError("任务文件总大小超过限制。")
        name_bytes = len(path.relative_to(root).as_posix().encode("utf-8"))
        entry_overhead += 128 + name_bytes * 2
    compression_margin = max(64 * 1024, total // 100)
    return total + compression_margin + entry_overhead + 22


def create_task_archive_unlocked(task, records, output_dir, *, progress=None,
                                 delete_originals=False, filename=None,
                                 recovery_token=None):
    """使用已打开且身份复核的文件描述符流式写入归档。"""
    try:
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        root_stat = os.lstat(root)
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise ArchiveError("下载目录无效。")
        files = _source_files(records, root)
        recovery_token = recovery_token or secrets.token_hex(32)
        source_identities = {path: _file_identity(os.lstat(path)) for path in files}
        source_digests = {}
        required_bytes = _required_archive_space(files, root)
        if shutil.disk_usage(root).free < required_bytes:
            raise InsufficientDiskSpace("磁盘空间不足，无法创建任务归档。")
        filename = filename or _archive_filename(task)
        if Path(filename).name != filename or not filename.lower().endswith(".zip"):
            raise ArchiveError("归档文件名无效。")
        destination = root / filename
        if destination.exists():
            raise ArchiveError("归档目标文件已存在。")
        temporary_handle = tempfile.NamedTemporaryFile(
            mode="w+b", prefix=f".{destination.stem}-", suffix=".part",
            dir=root, delete=False,
        )
        temporary = Path(temporary_handle.name)
        temporary_handle.close()
        started_at = time.monotonic()
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                for index, path in enumerate(files, start=1):
                    if time.monotonic() - started_at > MAX_ARCHIVE_SECONDS:
                        raise ArchiveError("任务归档耗时超过限制。")
                    name = path.relative_to(root).as_posix()
                    source, identity = open_regular_file(path, root)
                    try:
                        if _file_identity(identity) != source_identities[path]:
                            raise ArchiveError("归档源文件在创建期间发生变化。")
                        digest = hashlib.sha256()
                        with archive.open(name, "w") as target:
                            while chunk := source.read(1024 * 1024):
                                if time.monotonic() - started_at > MAX_ARCHIVE_SECONDS:
                                    raise ArchiveError("任务归档耗时超过限制。")
                                target.write(chunk)
                                digest.update(chunk)
                        _assert_regular_identity(path, identity, source.fileno())
                        source_digests[path] = digest.hexdigest()
                    finally:
                        source.close()
                    if progress is not None:
                        progress(index, len(files), name)
                manifest = {
                    path.relative_to(root).as_posix(): (
                        source_identities[path][2],
                        source_digests[path],
                    )
                    for path in files
                }
                archive.comment = _archive_marker(task, recovery_token, manifest)
            archive_stream, archive_identity = open_regular_file(temporary, root)
            try:
                archive_digest = hashlib.file_digest(
                    archive_stream,
                    "sha256",
                ).hexdigest()
                _assert_regular_identity(
                    temporary,
                    archive_identity,
                    archive_stream.fileno(),
                )
            finally:
                archive_stream.close()
            os.link(temporary, destination)
            if _file_identity(os.lstat(destination)) != _file_identity(archive_identity):
                raise ArchiveError("归档工件在提交时发生变化。")
            temporary.unlink()
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            if exc.errno == errno.ENOSPC:
                raise InsufficientDiskSpace("磁盘空间不足，无法创建任务归档。") from None
            raise ArchiveIOError("归档文件写入失败。") from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    except InsufficientDiskSpace:
        raise
    except ArchiveError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise InsufficientDiskSpace("磁盘空间不足，无法创建任务归档。") from None
        raise ArchiveIOError("归档文件操作失败。") from exc

    result = {
        "filename": filename,
        "path": destination,
        "file_count": len(files),
        "file_size": archive_identity.st_size,
        "deleted_originals": False,
        "deleted_original_count": 0,
        "delete_failed_count": 0,
        "delete_warning": None,
        "_source_files": files,
        "_source_identities": source_identities,
        "_source_digests": source_digests,
        "_archive_identity": _file_identity(archive_identity),
        "_archive_digest": archive_digest,
        "_root": root,
    }
    if delete_originals:
        delete_archive_sources(result)
    return result


def recover_task_archive(task, archive_record, records, output_dir):
    """恢复已原子提交、但尚未来得及写入 complete 状态的 ZIP。"""
    if archive_record.get("status") not in {"reserved", "failed"}:
        return None
    filename = str(archive_record.get("filename") or "")
    if Path(filename).name != filename or not filename.lower().endswith(".zip"):
        return None
    root = Path(output_dir).resolve()
    path = root / filename
    stream = None
    try:
        recovery_token = str(archive_record.get("recovery_token") or "")
        expected_files = _source_files(records, root)
        expected_manifest = {}
        expected_identities = {}
        for item in expected_files:
            expected_stream, expected_identity = open_regular_file(item, root)
            try:
                expected_digest = hashlib.file_digest(
                    expected_stream,
                    "sha256",
                ).hexdigest()
                _assert_regular_identity(
                    item,
                    expected_identity,
                    expected_stream.fileno(),
                )
            finally:
                expected_stream.close()
            expected_manifest[item.relative_to(root).as_posix()] = (
                expected_identity.st_size,
                expected_digest,
            )
            expected_identities[item] = _file_identity(expected_identity)
        stream, identity = open_regular_file(path, root)
        if identity.st_size > MAX_ARCHIVE_BYTES:
            return None
        with ZipFile(stream) as archive:
            started_at = time.monotonic()
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                return None
            if any(item.is_dir() for item in infos):
                return None
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                return None
            if set(names) != set(expected_manifest):
                return None
            declared_bytes = 0
            for item in infos:
                name = item.filename
                parts = name.split("/")
                if (
                    "\\" in name
                    or name.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or len(parts) > MAX_ARCHIVE_PATH_DEPTH
                ):
                    return None
                expected_size = expected_manifest[name][0]
                if item.file_size != expected_size:
                    return None
                declared_bytes += item.file_size
                if declared_bytes > MAX_ARCHIVE_BYTES:
                    return None
            actual_manifest = {}
            actual_bytes = 0
            for item in infos:
                digest = hashlib.sha256()
                entry_bytes = 0
                with archive.open(item, "r") as archived_file:
                    while chunk := archived_file.read(1024 * 1024):
                        if time.monotonic() - started_at > MAX_ARCHIVE_SECONDS:
                            return None
                        entry_bytes += len(chunk)
                        actual_bytes += len(chunk)
                        if (
                            entry_bytes > item.file_size
                            or actual_bytes > MAX_ARCHIVE_BYTES
                        ):
                            return None
                        digest.update(chunk)
                if entry_bytes != item.file_size:
                    return None
                actual_manifest[item.filename] = (item.file_size, digest.hexdigest())
            if (
                not secrets.compare_digest(
                    archive.comment,
                    _archive_marker(task, recovery_token, expected_manifest),
                )
                or actual_manifest != expected_manifest
            ):
                return None
            file_count = len(actual_manifest)
        for item in expected_files:
            current_stream, current_identity = open_regular_file(item, root)
            try:
                if _file_identity(current_identity) != expected_identities[item]:
                    return None
                current_digest = hashlib.file_digest(
                    current_stream,
                    "sha256",
                ).hexdigest()
                _assert_regular_identity(
                    item,
                    current_identity,
                    current_stream.fileno(),
                )
                expected_digest = expected_manifest[
                    item.relative_to(root).as_posix()
                ][1]
                if not secrets.compare_digest(current_digest, expected_digest):
                    return None
            finally:
                current_stream.close()
        _assert_regular_identity(path, identity, stream.fileno())
        if file_count <= 0:
            return None
        return {
            "filename": filename,
            "path": path,
            "file_count": file_count,
            "file_size": identity.st_size,
            "deleted_originals": False,
            "deleted_original_count": 0,
            "delete_failed_count": 0,
            "delete_warning": None,
        }
    except (
        ArchiveError,
        BadZipFile,
        EOFError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        zlib.error,
    ):
        return None
    finally:
        if stream is not None:
            stream.close()


def _restore_quarantined(quarantined: Path, original: Path) -> bool:
    try:
        os.link(quarantined, original)
        quarantined.unlink()
        return True
    except OSError:
        return False


def _delete_open_file(path: Path, stream) -> None:
    if os.name != "nt":
        path.unlink()
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    disposition = FileDispositionInfo(True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    success = kernel32.SetFileInformationByHandle(
        wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno())),
        4,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    )
    if not success:
        raise OSError(ctypes.get_last_error(), "无法按文件句柄删除隔离文件")


def _open_deletable_regular_file(path: Path, root: Path):
    if os.name != "nt":
        return open_regular_file(path, root)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    parent_identities = _parent_chain_identities(root, path)
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ArchiveError("隔离文件必须是普通文件。")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        0x80000000 | 0x00010000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "隔离文件无法打开")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    try:
        after = _assert_regular_identity(path, before, fd)
        _assert_parent_chain(root, path, parent_identities)
        return os.fdopen(fd, "rb"), after
    except Exception:
        os.close(fd)
        raise


def rollback_task_archive(result: dict[str, Any]) -> bool:
    """仅回滚身份与摘要仍匹配本次创建结果的未提交 ZIP。"""
    root = result["_root"]
    path = Path(result["path"])
    expected_identity = result["_archive_identity"]
    expected_digest = result["_archive_digest"]
    quarantine_root = Path(tempfile.mkdtemp(prefix=".pixiv-rollback-", dir=root))
    quarantined = quarantine_root / "archive.zip"
    stream = None
    try:
        parent_identities = _parent_chain_identities(root, path)
        try:
            os.rename(path, quarantined)
        except FileNotFoundError:
            return True
        _assert_parent_chain(root, path, parent_identities)
        stream, moved_identity = _open_deletable_regular_file(quarantined, root)
        moved_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            _file_identity(moved_identity) != expected_identity
            or not secrets.compare_digest(moved_digest, expected_digest)
        ):
            stream.close()
            stream = None
            _restore_quarantined(quarantined, path)
            return False
        _assert_regular_identity(quarantined, moved_identity, stream.fileno())
        _delete_open_file(quarantined, stream)
        return True
    except (ArchiveError, OSError, KeyError, TypeError):
        if stream is not None:
            stream.close()
            stream = None
        if quarantined.exists():
            _restore_quarantined(quarantined, path)
        return False
    finally:
        if stream is not None:
            stream.close()
        try:
            quarantine_root.rmdir()
        except OSError:
            pass


def delete_archive_sources(result: dict[str, Any]) -> dict[str, Any]:
    """先隔离再核对身份，永不直接 unlink 仍可被路径替换的源文件。"""
    files = result["_source_files"]
    identities = result["_source_identities"]
    digests = result["_source_digests"]
    root = result["_root"]
    deleted_count = 0
    failed_count = 0
    quarantine_root = Path(tempfile.mkdtemp(prefix=".pixiv-delete-", dir=root))
    for index, path in enumerate(files):
        quarantined = quarantine_root / f"source-{index}"
        try:
            parent_identities = _parent_chain_identities(root, path)
            os.rename(path, quarantined)
            _assert_parent_chain(root, path, parent_identities)
        except OSError:
            if quarantined.exists():
                _restore_quarantined(quarantined, path)
            failed_count += 1
            continue
        except ArchiveError:
            if quarantined.exists():
                _restore_quarantined(quarantined, path)
            failed_count += 1
            continue
        stream = None
        try:
            stream, moved_identity = _open_deletable_regular_file(quarantined, root)
            moved_digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if (
                _file_identity(moved_identity) != identities[path]
                or not secrets.compare_digest(moved_digest, digests[path])
            ):
                stream.close()
                stream = None
                _restore_quarantined(quarantined, path)
                failed_count += 1
                continue
            _assert_regular_identity(quarantined, moved_identity, stream.fileno())
            _delete_open_file(quarantined, stream)
            deleted_count += 1
        except (ArchiveError, OSError):
            if stream is not None:
                stream.close()
                stream = None
            if quarantined.exists():
                _restore_quarantined(quarantined, path)
            failed_count += 1
        finally:
            if stream is not None:
                stream.close()
    try:
        quarantine_root.rmdir()
    except OSError:
        pass
    parents = sorted(
        {
            parent
            for path in files
            for parent in path.parents
            if root in parent.parents
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass
    result.update({
        "deleted_originals": failed_count == 0,
        "deleted_original_count": deleted_count,
        "delete_failed_count": failed_count,
        "delete_warning": "部分原文件删除失败。" if failed_count else None,
    })
    return result


def create_task_archive(task, records, output_dir, *, progress=None,
                        delete_originals=False, filename=None,
                        recovery_token=None):
    """跨线程/进程串行创建归档，锁覆盖选名、写入和原文件删除。"""
    with archive_transaction(output_dir) as root:
        return create_task_archive_unlocked(
            task,
            records,
            root,
            progress=progress,
            delete_originals=delete_originals,
            filename=filename,
            recovery_token=recovery_token,
        )
