"""任务管理器状态机、队列和 Web 集成测试。"""
from pathlib import Path
from threading import Event, Thread

import pytest


def test_task_manager_create_start_complete_persists(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    executed = []

    def execute(task, control):
        executed.append((task["task_id"], task["query"], control.task_id))

    try:
        manager = TaskManager(database, execute)
        task_id = manager.create_task(
            task_type="artist",
            query={"artist_id": 456},
            total_count=1,
        )

        assert database.get_task(task_id)["status"] == "pending"
        completed = manager.start_task(task_id)

        assert executed == [(task_id, {"artist_id": 456}, task_id)]
        assert completed["status"] == "completed"
        assert completed["started_time"] is not None
        assert completed["completed_time"] is not None
    finally:
        database.close()

    reopened = DatabaseManager(database_path)
    try:
        assert reopened.get_task(task_id)["status"] == "completed"
    finally:
        reopened.close()


def test_task_manager_pause_and_resume_at_checkpoint(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database = DatabaseManager(tmp_path / "history.db")
    reached_boundary = Event()
    enter_checkpoint = Event()
    continued = Event()
    results = []
    errors = []
    worker = None

    def execute(task, control):
        reached_boundary.set()
        assert enter_checkpoint.wait(timeout=2)
        assert control.checkpoint() is True
        continued.set()

    try:
        manager = TaskManager(database, execute)
        task_id = manager.create_task(task_type="artist", query={"artist_id": 456})

        def run_task():
            try:
                results.append(manager.start_task(task_id))
            except Exception as exc:
                errors.append(exc)

        worker = Thread(target=run_task)
        worker.start()
        assert reached_boundary.wait(timeout=2)

        paused = manager.pause_task(task_id)
        enter_checkpoint.set()
        assert paused["status"] == "paused"
        assert not continued.wait(timeout=0.1)

        resumed = manager.resume_task(task_id)
        worker.join(timeout=2)
        assert resumed["status"] == "running"
        assert continued.is_set()
        assert not worker.is_alive()
        assert errors == []
        assert results[0]["status"] == "completed"
    finally:
        enter_checkpoint.set()
        if worker is not None:
            worker.join(timeout=2)
        database.close()


def test_task_manager_cancel_pending_and_running_tasks(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database = DatabaseManager(tmp_path / "history.db")
    reached_boundary = Event()
    enter_checkpoint = Event()
    checkpoint_results = []
    worker_results = []

    def execute(task, control):
        reached_boundary.set()
        assert enter_checkpoint.wait(timeout=2)
        checkpoint_results.append(control.checkpoint())

    try:
        manager = TaskManager(database, execute)
        pending_id = manager.create_task(task_type="artist", query={"artist_id": 1})
        assert manager.cancel_task(pending_id)["status"] == "cancelled"

        running_id = manager.create_task(task_type="artist", query={"artist_id": 2})
        worker = Thread(target=lambda: worker_results.append(manager.start_task(running_id)))
        worker.start()
        assert reached_boundary.wait(timeout=2)
        cancelled = manager.cancel_task(running_id)
        enter_checkpoint.set()
        worker.join(timeout=2)

        assert cancelled["status"] == "cancelled"
        assert checkpoint_results == [False]
        assert worker_results[0]["status"] == "cancelled"
        assert database.get_task(running_id)["completed_time"] is not None
    finally:
        enter_checkpoint.set()
        if "worker" in locals():
            worker.join(timeout=2)
        database.close()


def test_task_manager_allows_only_one_active_task(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager, TaskManagerError

    database = DatabaseManager(tmp_path / "history.db")
    first_started = Event()
    release_first = Event()
    first_results = []

    def execute(task, control):
        if task["query"]["slot"] == "first":
            first_started.set()
            assert release_first.wait(timeout=2)

    try:
        manager = TaskManager(database, execute)
        first_id = manager.create_task(task_type="artist", query={"slot": "first"})
        second_id = manager.create_task(task_type="artist", query={"slot": "second"})
        worker = Thread(target=lambda: first_results.append(manager.start_task(first_id)))
        worker.start()
        assert first_started.wait(timeout=2)

        with pytest.raises(TaskManagerError, match="另一个任务正在运行"):
            manager.start_task(second_id)
        assert database.get_task(second_id)["status"] == "pending"

        release_first.set()
        worker.join(timeout=2)
        assert first_results[0]["status"] == "completed"
        assert manager.start_task(second_id)["status"] == "completed"
    finally:
        release_first.set()
        if "worker" in locals():
            worker.join(timeout=2)
        database.close()


def test_task_manager_resume_persisted_paused_progress(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    task_id = database.create_task(
        task_type="artist",
        query={"artist_id": 456},
        total_count=3,
    )
    database.transition_task(task_id, "pending", "running")
    database.record_task_result(task_id, "downloaded")
    database.transition_task(task_id, "running", "paused")
    database.close()

    resumed_from = []

    def execute(task, control):
        resumed_from.append(task["downloaded_count"])
        control.record_result("skipped")
        control.record_result("failed")

    reopened = DatabaseManager(database_path)
    try:
        manager = TaskManager(reopened, execute)
        completed = manager.resume_task(task_id)

        assert resumed_from == [1]
        assert completed["status"] == "completed"
        assert completed["downloaded_count"] == 1
        assert completed["skipped_count"] == 1
        assert completed["failed_count"] == 1
    finally:
        reopened.close()


def test_task_manager_recovers_interrupted_running_task_as_paused(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager

    database_path = tmp_path / "history.db"
    database = DatabaseManager(database_path)
    task_id = database.create_task(task_type="artist", query={"artist_id": 456})
    database.transition_task(task_id, "pending", "running")
    database.close()

    reopened = DatabaseManager(database_path)
    try:
        TaskManager(reopened, lambda task, control: None)
        assert reopened.get_task(task_id)["status"] == "paused"
    finally:
        reopened.close()


def test_task_failure_terminal_guards_and_stable_listing(tmp_path):
    from src.pixiv_downloader.database import DatabaseManager
    from src.pixiv_downloader.task_manager import TaskManager, TaskManagerError

    database = DatabaseManager(tmp_path / "history.db")

    def fail(task, control):
        raise RuntimeError("network secret detail")

    try:
        manager = TaskManager(database, fail)
        first_id = manager.create_task(task_type="artist", query={"artist_id": 1})
        second_id = manager.create_task(task_type="artist", query={"artist_id": 2})

        with pytest.raises(RuntimeError, match="network secret detail"):
            manager.start_task(first_id)

        failed = database.get_task(first_id)
        assert failed["status"] == "failed"
        assert failed["completed_time"] is not None
        with pytest.raises(TaskManagerError):
            manager.pause_task(first_id)
        with pytest.raises(TaskManagerError):
            manager.cancel_task(first_id)
        assert [task["task_id"] for task in manager.list_tasks()] == [second_id, first_id]
    finally:
        database.close()


def test_task_api_and_frontend_controls(monkeypatch):
    from src.pixiv_downloader import app as app_module

    calls = []
    tasks = [{
        "task_id": 9,
        "task_type": "artist",
        "query": {"artist_id": 456},
        "filters": None,
        "status": "pending",
        "created_time": "2026-07-22 00:00:00",
        "started_time": None,
        "completed_time": None,
        "total_count": None,
        "downloaded_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
    }]

    class Auth:
        def has_saved_token(self):
            return True

    class Manager:
        def list_tasks(self):
            calls.append("list")
            return tasks

        def create_task(self, **task):
            calls.append(("create", task))
            return 9

        def start_task(self, task_id):
            calls.append(("start", task_id))

        def pause_task(self, task_id):
            calls.append(("pause", task_id))
            return {**tasks[0], "status": "paused"}

        def resume_task(self, task_id):
            calls.append(("resume", task_id))

        def cancel_task(self, task_id):
            calls.append(("cancel", task_id))
            return {**tasks[0], "status": "cancelled"}

    monkeypatch.setattr(app_module, "auth_manager", Auth())
    monkeypatch.setattr(app_module, "task_manager", Manager(), raising=False)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda target, *args: target(*args),
    )
    client = app_module.app.test_client()

    listed = client.get("/api/tasks")
    invalid = client.post("/api/tasks", json={"task_type": "artist", "query": {"artist_id": 0}})
    created = client.post("/api/tasks", json={"task_type": "artist", "query": {"artist_id": 456}})
    started = client.post("/api/tasks/9/start")
    paused = client.post("/api/tasks/9/pause")
    resumed = client.post("/api/tasks/9/resume")
    cancelled = client.post("/api/tasks/9/cancel")
    page = client.get(f"/?token={app_module.SECURITY_TOKEN}").get_data(as_text=True)
    script = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")

    assert listed.get_json() == {"tasks": tasks}
    assert invalid.status_code == 400
    assert created.status_code == 201
    assert created.get_json() == {"success": True, "task_id": 9, "status": "pending"}
    assert started.status_code == 202
    assert paused.get_json()["task"]["status"] == "paused"
    assert resumed.status_code == 202
    assert cancelled.get_json()["task"]["status"] == "cancelled"
    assert calls == [
        "list",
        ("create", {"task_type": "artist", "query": {"artist_id": 456}}),
        ("start", 9),
        ("pause", 9),
        ("resume", 9),
        ("cancel", 9),
    ]
    for element_id in ("task-panel", "task-list", "task-list-feedback"):
        assert f'id="{element_id}"' in page
    for endpoint in ("/api/tasks", "start", "pause", "resume", "cancel"):
        assert endpoint in script
