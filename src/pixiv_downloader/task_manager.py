"""持久化下载任务的状态机协调器。"""
import logging
from threading import Condition, RLock
from time import monotonic
from typing import Any, Callable


logger = logging.getLogger(__name__)


class TaskManagerError(RuntimeError):
    """任务不存在、转换非法或执行失败。"""


class TaskControl:
    """传给任务执行器的暂停、取消与进度句柄。"""

    def __init__(self, manager: "TaskManager", task_id: int) -> None:
        self._manager = manager
        self.task_id = task_id
        self._started_at = manager.clock()
        self._last_sample_at = self._started_at
        self._last_sample_bytes = 0
        self._last_progress_at: float | None = None

    def _progress_due(self, now: float) -> bool:
        if self._last_progress_at is not None and now - self._last_progress_at < 1:
            return False
        self._last_progress_at = now
        return True

    def checkpoint(self) -> bool:
        """暂停时阻塞；任务被取消时返回 False。"""
        return self._manager._checkpoint(self.task_id)

    def record_result(self, result: str) -> dict[str, Any]:
        """持久化一个作品的处理结果。"""
        task = self._manager.database.record_task_result(self.task_id, result)
        if task is None:
            raise TaskManagerError("任务不能再记录进度")
        processed = sum(
            int(task.get(key) or 0)
            for key in ("downloaded_count", "skipped_count", "failed_count")
        )
        total = task.get("total_count")
        remaining = max(0, int(total) - processed) if total is not None else None
        now = self._manager.clock()
        elapsed = max(0.0, now - self._started_at)
        eta = (
            0.0
            if remaining == 0
            else remaining * elapsed / processed
            if remaining is not None and processed and elapsed > 0
            else None
        )
        if self._progress_due(now):
            self._manager._publish_progress(task, eta_seconds=eta)
        return task

    def set_total(self, total_count: int) -> dict[str, Any]:
        """持久化任务总作品数。"""
        task = self._manager.database.set_task_total(self.task_id, total_count)
        if task is None:
            raise TaskManagerError("任务不能更新总数")
        now = self._manager.clock()
        if self._progress_due(now):
            self._manager._publish_progress(task)
        return task

    def file_progress(
        self,
        filename: str,
        downloaded_bytes: int,
        total_bytes: int | None,
        complete: bool,
    ) -> None:
        """按最多每秒一次发布当前文件的字节进度。"""
        now = self._manager.clock()
        if not self._progress_due(now):
            return
        elapsed = now - self._last_sample_at
        speed = 0.0
        if downloaded_bytes and elapsed > 0:
            speed = max(0, downloaded_bytes - self._last_sample_bytes) / elapsed / (1024 ** 2)
        self._last_sample_at = now
        self._last_sample_bytes = downloaded_bytes
        progress = (
            min(100, max(0, round(downloaded_bytes * 100 / total_bytes)))
            if total_bytes
            else (100 if complete else 0)
        )
        task = self._manager.database.get_task(self.task_id)
        if task is None:
            raise TaskManagerError("任务不存在")
        self._manager._publish_progress(
            task,
            current_speed=speed,
            current_files=[{"filename": filename, "progress": progress}],
        )


class TaskManager:
    """串行执行持久化任务并维护其生命周期。"""

    def __init__(
        self,
        database,
        execute: Callable[[dict[str, Any], TaskControl], None],
        *,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = monotonic,
    ):
        self.database = database
        self.execute = execute
        self._publisher = publish or (lambda event, payload: None)
        self.clock = clock
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active_task_id: int | None = None
        self.database.recover_interrupted_tasks()

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        """发布非关键事件；通道故障不得反向破坏任务状态机。"""
        try:
            self._publisher(event, payload)
        except Exception:
            logger.warning("任务事件发布失败：%s", event)

    def create_task(self, **task: Any) -> int:
        return self.database.create_task(**task)

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.database.list_tasks()

    def start_task(self, task_id: int) -> dict[str, Any]:
        with self._condition:
            task = self.database.get_task(task_id)
            if task is None:
                raise TaskManagerError("任务不存在")
            self._claim_slot(task_id)
            running = self.database.transition_task(task_id, "pending", "running")
            if running is None:
                self._active_task_id = None
                raise TaskManagerError("只有等待中的任务可以开始")
        return self._execute_task(running)

    def pause_task(self, task_id: int) -> dict[str, Any]:
        with self._condition:
            paused = self.database.transition_task(task_id, "running", "paused")
            if paused is None:
                raise TaskManagerError("只有运行中的任务可以暂停")
            return paused

    def resume_task(self, task_id: int) -> dict[str, Any]:
        with self._condition:
            task = self.database.get_task(task_id)
            if task is None:
                raise TaskManagerError("任务不存在")
            if self._active_task_id == task_id:
                running = self.database.transition_task(task_id, "paused", "running")
                if running is None:
                    raise TaskManagerError("只有暂停的任务可以恢复")
                self._condition.notify_all()
                return running
            self._claim_slot(task_id)
            running = self.database.transition_task(task_id, "paused", "running")
            if running is None:
                self._active_task_id = None
                raise TaskManagerError("只有暂停的任务可以恢复")
        return self._execute_task(running)

    def cancel_task(self, task_id: int) -> dict[str, Any]:
        with self._condition:
            task = self.database.get_task(task_id)
            if task is None:
                raise TaskManagerError("任务不存在")
            if task["status"] not in {"pending", "running", "paused"}:
                raise TaskManagerError("当前任务状态不能取消")
            cancelled = self.database.transition_task(
                task_id,
                task["status"],
                "cancelled",
            )
            if cancelled is None:
                raise TaskManagerError("任务状态已变化")
            self._condition.notify_all()
            return cancelled

    def _claim_slot(self, task_id: int) -> None:
        if self._active_task_id is not None:
            raise TaskManagerError("另一个任务正在运行")
        self._active_task_id = task_id

    def _execute_task(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["task_id"]
        try:
            self.execute(task, TaskControl(self, task_id))
        except Exception:
            current = self.database.get_task(task_id)
            if current is not None and current["status"] in {"running", "paused"}:
                failed = self.database.transition_task(task_id, current["status"], "failed")
                if failed is not None:
                    self.publish("error", {
                        "task_id": task_id,
                        "status": "failed",
                        "message": "任务执行失败，请检查网络、代理或登录状态。",
                    })
            raise
        else:
            with self._condition:
                if not self._checkpoint(task_id):
                    cancelled = self.database.get_task(task_id)
                    if cancelled is None:
                        raise TaskManagerError("任务不存在")
                    self._publish_terminal("task_complete", cancelled)
                    return cancelled
                completed = self.database.transition_task(task_id, "running", "completed")
                if completed is None:
                    raise TaskManagerError("任务执行期间状态已变化")
                self._publish_terminal("task_complete", completed)
                return completed
        finally:
            with self._condition:
                if self._active_task_id == task_id:
                    self._active_task_id = None

    def _publish_progress(
        self,
        task: dict[str, Any],
        *,
        current_speed: float = 0.0,
        eta_seconds: float | None = None,
        current_files: list[dict[str, Any]] | None = None,
    ) -> None:
        self.publish("progress_update", {
            "task_id": task["task_id"],
            "total_count": task["total_count"],
            "downloaded_count": task["downloaded_count"],
            "skipped_count": task["skipped_count"],
            "failed_count": task["failed_count"],
            "current_speed": current_speed,
            "eta_seconds": eta_seconds,
            "current_files": (current_files or [])[:5],
        })

    def _publish_terminal(self, event: str, task: dict[str, Any]) -> None:
        self.publish(event, {
            "task_id": task["task_id"],
            "status": task["status"],
            "total_count": task["total_count"],
            "downloaded_count": task["downloaded_count"],
            "skipped_count": task["skipped_count"],
            "failed_count": task["failed_count"],
        })

    def _checkpoint(self, task_id: int) -> bool:
        with self._condition:
            task = self.database.get_task(task_id)
            while task is not None and task["status"] == "paused":
                self._condition.wait()
                task = self.database.get_task(task_id)
            if task is None:
                raise TaskManagerError("任务不存在")
            return task["status"] != "cancelled"
