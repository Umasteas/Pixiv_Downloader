"""持久化下载任务的状态机协调器。"""
from threading import Condition, RLock
from typing import Any, Callable


class TaskManagerError(RuntimeError):
    """任务不存在、转换非法或执行失败。"""


class TaskControl:
    """传给任务执行器的暂停、取消与进度句柄。"""

    def __init__(self, manager: "TaskManager", task_id: int) -> None:
        self._manager = manager
        self.task_id = task_id

    def checkpoint(self) -> bool:
        """暂停时阻塞；任务被取消时返回 False。"""
        return self._manager._checkpoint(self.task_id)

    def record_result(self, result: str) -> dict[str, Any]:
        """持久化一个作品的处理结果。"""
        task = self._manager.database.record_task_result(self.task_id, result)
        if task is None:
            raise TaskManagerError("任务不能再记录进度")
        return task

    def set_total(self, total_count: int) -> dict[str, Any]:
        """持久化任务总作品数。"""
        task = self._manager.database.set_task_total(self.task_id, total_count)
        if task is None:
            raise TaskManagerError("任务不能更新总数")
        return task


class TaskManager:
    """串行执行持久化任务并维护其生命周期。"""

    def __init__(self, database, execute: Callable[[dict[str, Any], TaskControl], None]):
        self.database = database
        self.execute = execute
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active_task_id: int | None = None
        self.database.recover_interrupted_tasks()

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
                self.database.transition_task(task_id, current["status"], "failed")
            raise
        else:
            with self._condition:
                if not self._checkpoint(task_id):
                    return self.database.get_task(task_id)
                completed = self.database.transition_task(task_id, "running", "completed")
                if completed is None:
                    raise TaskManagerError("任务执行期间状态已变化")
                return completed
        finally:
            with self._condition:
                if self._active_task_id == task_id:
                    self._active_task_id = None

    def _checkpoint(self, task_id: int) -> bool:
        with self._condition:
            task = self.database.get_task(task_id)
            while task is not None and task["status"] == "paused":
                self._condition.wait()
                task = self.database.get_task(task_id)
            if task is None:
                raise TaskManagerError("任务不存在")
            return task["status"] != "cancelled"
