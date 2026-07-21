"""SQLite 下载历史、任务记录和作品去重基础设施。"""
import json
import sqlite3
import threading
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
                    file_path, page_count, bookmarks, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artwork_id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    artist_name = excluded.artist_name,
                    title = excluded.title,
                    download_time = CURRENT_TIMESTAMP,
                    task_id = excluded.task_id,
                    file_path = excluded.file_path,
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
                    page_count,
                    bookmarks,
                    encoded_tags,
                ),
            )

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

    def close(self) -> None:
        """关闭底层 SQLite 连接。"""
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
