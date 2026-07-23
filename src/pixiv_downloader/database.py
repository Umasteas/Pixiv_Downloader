"""SQLite 下载历史、任务记录和作品去重基础设施。"""
import json
import secrets
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    query TEXT,
    filters TEXT,
    status TEXT NOT NULL,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_time TIMESTAMP,
    completed_time TIMESTAMP,
    total_count INTEGER,
    downloaded_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS downloads (
    artwork_id INTEGER PRIMARY KEY,
    artist_id INTEGER,
    artist_name TEXT,
    title TEXT,
    download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_id INTEGER,
    file_path TEXT,
    file_size INTEGER,
    page_count INTEGER,
    bookmarks INTEGER,
    tags TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS followed_artists (
    artist_id INTEGER PRIMARY KEY,
    artist_name TEXT,
    last_check_time TIMESTAMP,
    last_artwork_id INTEGER
);

CREATE TABLE IF NOT EXISTS task_failures (
    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    artwork_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, artwork_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS task_archives (
    task_id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'reserved',
    recovery_token TEXT,
    file_count INTEGER NOT NULL,
    file_size INTEGER NOT NULL,
    deleted_originals INTEGER NOT NULL DEFAULT 0,
    deleted_original_count INTEGER NOT NULL DEFAULT 0,
    delete_failed_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""


class DatabaseManager:
    """初始化 SQLite Schema，并提供下载历史和去重公共接口。"""

    def __init__(self, database_path: Union[str, Path] = "history.db") -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        """首次启动时创建 PRD 定义的三张表。"""
        with self._lock, self.connection:
            self.connection.executescript(SCHEMA)
            archive_columns = {
                row[1]
                for row in self.connection.execute("PRAGMA table_info(task_archives)")
            }
            if "status" not in archive_columns:
                self.connection.execute(
                    "ALTER TABLE task_archives ADD COLUMN status TEXT NOT NULL DEFAULT 'reserved'"
                )
                self.connection.execute(
                    "UPDATE task_archives SET status = CASE WHEN file_count > 0 THEN 'complete' ELSE 'reserved' END"
                )
            if "recovery_token" not in archive_columns:
                self.connection.execute(
                    "ALTER TABLE task_archives ADD COLUMN recovery_token TEXT"
                )
            columns = {
                row[1]
                for row in self.connection.execute("PRAGMA table_info(downloads)")
            }
            if "file_size" not in columns:
                self.connection.execute(
                    "ALTER TABLE downloads ADD COLUMN file_size INTEGER"
                )
            legacy_rows = self.connection.execute(
                "SELECT artwork_id, file_path FROM downloads WHERE file_size IS NULL"
            ).fetchall()
            for row in legacy_rows:
                if not row["file_path"]:
                    continue
                try:
                    file_size = Path(row["file_path"]).stat().st_size
                except OSError:
                    continue
                self.connection.execute(
                    "UPDATE downloads SET file_size = ? WHERE artwork_id = ?",
                    (file_size, row["artwork_id"]),
                )

    def is_downloaded(self, artwork_id: int) -> bool:
        """判断作品是否已有下载记录。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT 1 FROM downloads WHERE artwork_id = ? LIMIT 1",
                (artwork_id,),
            ).fetchone()
        return row is not None

    def mark_as_downloaded(
        self,
        artwork_id: int,
        *,
        artist_id: Optional[int] = None,
        artist_name: Optional[str] = None,
        title: Optional[str] = None,
        task_id: Optional[int] = None,
        file_path: Optional[str] = None,
        file_size: Optional[int] = None,
        page_count: Optional[int] = None,
        bookmarks: Optional[int] = None,
        tags: Optional[Iterable[str]] = None,
    ) -> None:
        """新增或更新作品下载记录，artwork_id 始终保持唯一。"""
        encoded_tags = None
        if tags is not None:
            encoded_tags = json.dumps(list(tags), ensure_ascii=False)

        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO downloads (
                    artwork_id, artist_id, artist_name, title, task_id,
                    file_path, file_size, page_count, bookmarks, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artwork_id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    artist_name = excluded.artist_name,
                    title = excluded.title,
                    download_time = CURRENT_TIMESTAMP,
                    task_id = excluded.task_id,
                    file_path = excluded.file_path,
                    file_size = excluded.file_size,
                    page_count = excluded.page_count,
                    bookmarks = excluded.bookmarks,
                    tags = excluded.tags
                """,
                (
                    artwork_id,
                    artist_id,
                    artist_name,
                    title,
                    task_id,
                    file_path,
                    file_size,
                    page_count,
                    bookmarks,
                    encoded_tags,
                ),
            )

    def create_task(
        self,
        *,
        task_type: str,
        query: Any = None,
        filters: Any = None,
        total_count: Optional[int] = None,
    ) -> int:
        """创建一个可供任务管理器后续执行的 pending 任务。"""
        encoded_query = (
            json.dumps(query, ensure_ascii=False)
            if query is not None and not isinstance(query, str)
            else query
        )
        encoded_filters = (
            json.dumps(filters, ensure_ascii=False)
            if filters is not None and not isinstance(filters, str)
            else filters
        )
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO tasks (
                    task_type, query, filters, status, total_count
                ) VALUES (?, ?, ?, 'pending', ?)
                """,
                (task_type, encoded_query, encoded_filters, total_count),
            )
        return int(cursor.lastrowid)

    def create_retry_task(
        self,
        *,
        source_task_id: int,
        artwork_ids: list[int],
    ) -> tuple[int, bool]:
        """原子创建失败项重试；同一来源最多存在一个活跃重试任务。"""
        query = {"source_task_id": source_task_id, "artwork_ids": artwork_ids}
        encoded_query = json.dumps(query, ensure_ascii=False)
        with self._lock, self.connection:
            rows = self.connection.execute(
                """
                SELECT task_id, query FROM tasks
                WHERE task_type = 'retry_failed'
                  AND status IN ('pending', 'running', 'paused')
                """
            ).fetchall()
            for row in rows:
                try:
                    existing_query = json.loads(row["query"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if existing_query.get("source_task_id") == source_task_id:
                    return int(row["task_id"]), False
            cursor = self.connection.execute(
                """
                INSERT INTO tasks (task_type, query, status)
                VALUES ('retry_failed', ?, 'pending')
                """,
                (encoded_query,),
            )
        return int(cursor.lastrowid), True

    def get_task(self, task_id: int) -> Optional[dict[str, Any]]:
        """回读单个任务，并恢复结构化 query/filters。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_task(row)

    def list_tasks(self) -> list[dict[str, Any]]:
        """按最新创建顺序返回全部持久化任务。"""
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM tasks ORDER BY created_time DESC, task_id DESC"
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    @staticmethod
    def _decode_task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("query", "filters"):
            value = item.get(key)
            if value:
                try:
                    item[key] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    pass
        return item

    def transition_task(
        self,
        task_id: int,
        expected_status: str,
        new_status: str,
    ) -> Optional[dict[str, Any]]:
        """仅在当前状态匹配时原子转换任务状态。"""
        assignments = ["status = ?"]
        parameters: list[Any] = [new_status]
        if new_status == "running":
            assignments.append("started_time = COALESCE(started_time, CURRENT_TIMESTAMP)")
        if new_status in {"completed", "cancelled", "failed"}:
            assignments.append("completed_time = CURRENT_TIMESTAMP")
        with self._lock, self.connection:
            cursor = self.connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} "
                "WHERE task_id = ? AND status = ?",
                (*parameters, task_id, expected_status),
            )
        return self.get_task(task_id) if cursor.rowcount == 1 else None

    def record_task_result(
        self,
        task_id: int,
        result: str,
    ) -> Optional[dict[str, Any]]:
        """原子累加任务的单项处理结果。"""
        columns = {
            "downloaded": "downloaded_count",
            "skipped": "skipped_count",
            "failed": "failed_count",
        }
        try:
            column = columns[result]
        except KeyError as exc:
            raise ValueError("result 必须是 downloaded、skipped 或 failed") from exc
        with self._lock, self.connection:
            cursor = self.connection.execute(
                f"UPDATE tasks SET {column} = {column} + 1 "
                "WHERE task_id = ? AND status = 'running'",
                (task_id,),
            )
        return self.get_task(task_id) if cursor.rowcount == 1 else None

    def record_task_failure(
        self,
        task_id: int,
        *,
        artwork_id: int,
        category: str,
        message: str,
    ) -> Optional[dict[str, Any]]:
        """幂等记录失败作品，并仅在首次写入时累加 failed_count。"""
        if type(artwork_id) is not int or artwork_id <= 0:
            raise ValueError("artwork_id 必须是正整数")
        if category not in {"recoverable", "skipped"}:
            raise ValueError("失败作品类别必须是 recoverable 或 skipped")
        with self._lock, self.connection:
            task = self.connection.execute(
                "SELECT status FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None or task["status"] != "running":
                return None
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO task_failures (
                    task_id, artwork_id, category, message
                ) VALUES (?, ?, ?, ?)
                """,
                (task_id, artwork_id, category, str(message)[:300]),
            )
            if cursor.rowcount == 1:
                self.connection.execute(
                    "UPDATE tasks SET failed_count = failed_count + 1 "
                    "WHERE task_id = ? AND status = 'running'",
                    (task_id,),
                )
        return self.get_task(task_id)

    def list_task_failures(self, task_id: int) -> list[dict[str, Any]]:
        """按写入顺序返回任务的失败作品明细。"""
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM task_failures WHERE task_id = ? ORDER BY failure_id",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def failed_artwork_ids(self, task_id: int) -> list[int]:
        """返回可用于仅失败项重试的稳定作品 ID 列表。"""
        return [
            int(item["artwork_id"])
            for item in self.list_task_failures(task_id)
        ]

    def set_task_total(self, task_id: int, total_count: int) -> Optional[dict[str, Any]]:
        """持久化运行任务的总作品数。"""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE tasks SET total_count = ? "
                "WHERE task_id = ? AND status IN ('running', 'paused')",
                (total_count, task_id),
            )
        return self.get_task(task_id) if cursor.rowcount == 1 else None

    def set_bookmark_snapshot(
        self,
        task_id: int,
        artwork_ids: list[int],
    ) -> dict[str, Any]:
        """在下载前原子冻结收藏任务的有序作品 ID；快照不可覆盖。"""
        from .bookmarks import normalize_persisted_bookmarks_query

        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT task_type, query, status FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None or row["task_type"] != "bookmarks":
                raise ValueError("收藏任务快照目标无效")
            if row["status"] not in {"running", "paused"}:
                raise ValueError("收藏任务快照只能写入运行中任务")
            try:
                query = json.loads(row["query"])
            except (TypeError, json.JSONDecodeError):
                raise ValueError("收藏任务快照 query 无效") from None
            existing = query.get("artwork_ids")
            if existing is not None:
                if existing != artwork_ids:
                    raise ValueError("收藏任务快照不可覆盖")
                task = self.get_task(task_id)
                if task is None:
                    raise ValueError("收藏任务快照目标无效")
                return task
            normalized = normalize_persisted_bookmarks_query({
                **query,
                "artwork_ids": artwork_ids,
            })
            cursor = self.connection.execute(
                "UPDATE tasks SET query = ? WHERE task_id = ? "
                "AND task_type = 'bookmarks' AND status IN ('running', 'paused')",
                (json.dumps(normalized, ensure_ascii=False), task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("收藏任务快照写入失败")
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("收藏任务快照目标无效")
        return task

    def recover_interrupted_tasks(self) -> int:
        """把上次进程遗留的 running 任务恢复为 paused。"""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE tasks SET status = 'paused' WHERE status = 'running'"
            )
        return cursor.rowcount

    def get_followed_artist(self, artist_id: int) -> Optional[dict[str, Any]]:
        """返回指定关注画师的最近检查状态。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM followed_artists WHERE artist_id = ?",
                (artist_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_followed_artists(self) -> list[dict[str, Any]]:
        """按画师 ID 返回全部已同步的关注画师状态。"""
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM followed_artists ORDER BY artist_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_followed_artist(
        self,
        artist_id: int,
        *,
        artist_name: str,
        last_artwork_id: Optional[int],
        checked_at: str,
    ) -> None:
        """原子写入关注画师名称、检查时间和最新作品 ID。"""
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO followed_artists (
                    artist_id, artist_name, last_check_time, last_artwork_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(artist_id) DO UPDATE SET
                    artist_name = excluded.artist_name,
                    last_check_time = excluded.last_check_time,
                    last_artwork_id = excluded.last_artwork_id
                """,
                (artist_id, artist_name, checked_at, last_artwork_id),
            )

    def get_download_stats(
        self,
        time_range: str = "all",
        *,
        as_of: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """返回指定日历时间窗口内的下载统计。"""
        if time_range not in {"7", "30", "all"}:
            raise ValueError("time_range 必须是 7、30 或 all")

        reference = as_of or datetime.now(timezone.utc)
        where = ""
        parameters: tuple[Any, ...] = ()
        if time_range != "all":
            days = int(time_range)
            start = (reference.date() - timedelta(days=days - 1)).isoformat()
            end = reference.date().isoformat()
            where = "WHERE date(d.download_time) BETWEEN date(?) AND date(?)"
            parameters = (start, end)

        with self._lock:
            rows = self.connection.execute(
                f"""
                SELECT d.*, COALESCE(t.task_type, 'artist') AS effective_task_type
                FROM downloads AS d
                LEFT JOIN tasks AS t ON t.task_id = d.task_id
                {where}
                ORDER BY d.download_time, d.artwork_id
                """,
                parameters,
            ).fetchall()

        artist_counts: Counter[int] = Counter()
        artist_names: dict[int, str] = {}
        tag_counts: Counter[str] = Counter()
        trend_counts: Counter[str] = Counter()
        task_counts: Counter[str] = Counter()
        total_size = 0

        for row in rows:
            item = dict(row)
            artist_id = item.get("artist_id")
            if artist_id is not None:
                artist_id = int(artist_id)
                artist_counts[artist_id] += 1
                artist_names[artist_id] = item.get("artist_name") or ""
            raw_tags = item.get("tags")
            if raw_tags:
                try:
                    tags = json.loads(raw_tags)
                except (TypeError, json.JSONDecodeError):
                    tags = [raw_tags]
                if not isinstance(tags, list):
                    tags = []
                tag_counts.update(str(tag) for tag in tags if str(tag))
            trend_counts[str(item["download_time"])[:10]] += 1
            task_counts[str(item["effective_task_type"])] += 1
            total_size += int(item.get("file_size") or 0)

        top_artists = [
            {
                "artist_id": artist_id,
                "artist_name": artist_names[artist_id],
                "count": count,
            }
            for artist_id, count in sorted(
                artist_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:10]
        ]
        top_tags = [
            {"tag": tag, "count": count}
            for tag, count in sorted(
                tag_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )[:10]
        ]
        trend = [
            {"date": date, "count": trend_counts[date]}
            for date in sorted(trend_counts)
        ]
        task_types = [
            {"task_type": task_type, "count": count}
            for task_type, count in sorted(
                task_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]
        return {
            "range": time_range,
            "totals": {
                "downloads": len(rows),
                "artists": len(artist_counts),
                "file_size": total_size,
            },
            "top_artists": top_artists,
            "top_tags": top_tags,
            "trend": trend,
            "task_types": task_types,
        }

    def get_download_history(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """按最近写入顺序返回下载历史。"""
        if limit is not None and limit < 0:
            raise ValueError("limit 不能为负数")

        query = "SELECT rowid, * FROM downloads ORDER BY download_time DESC, rowid DESC"
        parameters: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)

        with self._lock:
            rows = self.connection.execute(query, parameters).fetchall()

        history = []
        for row in rows:
            item = dict(row)
            item.pop("rowid", None)
            raw_tags = item.get("tags")
            if raw_tags:
                try:
                    item["tags"] = json.loads(raw_tags)
                except (TypeError, json.JSONDecodeError):
                    item["tags"] = [raw_tags]
            else:
                item["tags"] = [] if raw_tags == "[]" else None
            history.append(item)
        return history

    def list_task_downloads(self, task_id: int) -> list[dict[str, Any]]:
        """按作品 ID 返回仍归属指定任务的下载记录。"""
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM downloads WHERE task_id = ? ORDER BY artwork_id",
                (task_id,),
            ).fetchall()
        downloads = []
        for row in rows:
            item = dict(row)
            raw_tags = item.get("tags")
            if raw_tags:
                try:
                    item["tags"] = json.loads(raw_tags)
                except (TypeError, json.JSONDecodeError):
                    item["tags"] = [raw_tags]
            else:
                item["tags"] = [] if raw_tags == "[]" else None
            downloads.append(item)
        return downloads

    def reserve_task_archive(
        self,
        task_id: int,
        base_filename: str,
        unavailable_filenames: Iterable[str] = (),
    ) -> str:
        """结合数据库与磁盘占用名，为任务原子保留唯一文件名。"""
        unavailable = {str(filename) for filename in unavailable_filenames}
        recovery_token = secrets.token_hex(32)
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT filename, status FROM task_archives WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is not None and existing["status"] == "complete":
                return str(existing["filename"])
            candidate = base_filename
            stem = Path(base_filename).stem
            suffix = 0
            while candidate in unavailable or self.connection.execute(
                """
                SELECT 1 FROM task_archives
                WHERE filename = ? AND task_id != ?
                """,
                (candidate, task_id),
            ).fetchone() is not None:
                suffix += 1
                candidate = f"{stem}_{task_id if suffix == 1 else f'{task_id}_{suffix}'}.zip"
            if existing is not None:
                self.connection.execute(
                    """
                    UPDATE task_archives
                    SET filename = ?, status = 'reserved', file_count = 0,
                        file_size = 0, deleted_originals = 0,
                        deleted_original_count = 0, delete_failed_count = 0,
                        recovery_token = ?
                    WHERE task_id = ?
                    """,
                    (candidate, recovery_token, task_id),
                )
                return candidate
            cursor = self.connection.execute(
                """
                INSERT INTO task_archives (
                    task_id, filename, status, recovery_token, file_count, file_size
                ) VALUES (?, ?, 'reserved', ?, 0, 0)
                """,
                (task_id, candidate, recovery_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("任务归档文件名保留失败")
            return candidate

    def get_task_archive(self, task_id: int) -> Optional[dict[str, Any]]:
        """返回任务归档持久化记录。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM task_archives WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def complete_task_archive(self, task_id: int, *, filename: str, file_count: int,
                             file_size: int, deleted_originals: bool,
                             deleted_original_count: int,
                             delete_failed_count: int) -> Optional[dict[str, Any]]:
        """提交归档完成状态；允许 reserved 或可恢复 failed 原子提交。"""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE task_archives SET filename = ?, status = 'complete',
                    file_count = ?, file_size = ?, deleted_originals = ?,
                    deleted_original_count = ?, delete_failed_count = ?
                WHERE task_id = ? AND status IN ('reserved', 'failed')
                """,
                (filename, file_count, file_size, int(deleted_originals),
                 deleted_original_count, delete_failed_count, task_id),
            )
        return self.get_task_archive(task_id) if cursor.rowcount == 1 else None

    def update_task_archive_deletion(
        self,
        task_id: int,
        *,
        deleted_originals: bool,
        deleted_original_count: int,
        delete_failed_count: int,
    ) -> Optional[dict[str, Any]]:
        """在归档已 complete 后持久化可选删除结果。"""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE task_archives
                SET deleted_originals = ?, deleted_original_count = ?,
                    delete_failed_count = ?
                WHERE task_id = ? AND status = 'complete'
                """,
                (
                    int(deleted_originals),
                    deleted_original_count,
                    delete_failed_count,
                    task_id,
                ),
            )
        return self.get_task_archive(task_id) if cursor.rowcount == 1 else None

    def fail_task_archive(self, task_id: int) -> Optional[dict[str, Any]]:
        """标记提交失败，允许下次 reserve 安全重试。"""
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE task_archives SET status = 'failed'
                WHERE task_id = ? AND status IN ('reserved', 'complete')
                """,
                (task_id,),
            )
        return self.get_task_archive(task_id) if cursor.rowcount == 1 else None

    def list_task_archives(self, task_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        """批量投影已完成归档，避免任务列表 N+1 查询。"""
        ids = [int(task_id) for task_id in task_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM task_archives WHERE status = 'complete' AND task_id IN ({placeholders})",
                ids,
            ).fetchall()
        return {int(row["task_id"]): dict(row) for row in rows}

    def get_task_archive_by_filename(
        self,
        filename: str,
    ) -> Optional[dict[str, Any]]:
        """按唯一文件名返回已完成归档记录。"""
        with self._lock:
            row = self.connection.execute(
                """
                SELECT * FROM task_archives
                WHERE filename = ? AND status = 'complete'
                """,
                (filename,),
            ).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        """关闭底层 SQLite 连接。"""
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
