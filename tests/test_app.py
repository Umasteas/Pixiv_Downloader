"""基础 Flask 应用的验收测试。"""
from pathlib import Path

from flask_socketio import SocketIO

from src.pixiv_downloader import app as app_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_skeleton_and_dependencies_exist():
    """项目骨架、pytest 与 Flask 依赖均已落盘。"""
    for relative_path in (
        "src",
        "static",
        "templates",
        "tests",
        "requirements.txt",
    ):
        assert (PROJECT_ROOT / relative_path).exists(), relative_path

    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "Flask==" in requirements
    assert "Flask-SocketIO==" in requirements
    assert "PyYAML==" in requirements
    assert "pytest==" in requirements


def test_welcome_page_requires_token_and_uses_bootstrap_5():
    """欢迎页只有携带安全 token 时可访问，并显示标题及 Bootstrap 5。"""
    client = app_module.app.test_client()

    assert client.get("/").status_code == 403

    response = client.get(f"/?token={app_module.SECURITY_TOKEN}")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "欢迎使用 Pixiv 批量下载工具" in page
    assert "bootstrap@5.3.0" in page


def test_socketio_is_integrated_and_accepts_a_test_client():
    """Flask-SocketIO 已绑定应用并能建立连接。"""
    assert isinstance(app_module.socketio, SocketIO)

    client = app_module.socketio.test_client(app_module.app)
    try:
        assert client.is_connected()
    finally:
        client.disconnect()


def test_start_app_opens_browser_and_runs_server(monkeypatch):
    """启用 auto_open 时打开带 token 的 URL，然后启动 SocketIO 服务器。"""
    opened_urls = []
    run_calls = []

    monkeypatch.setattr(app_module.webbrowser, "open", opened_urls.append)
    monkeypatch.setattr(
        app_module.socketio,
        "run",
        lambda flask_app, **kwargs: run_calls.append((flask_app, kwargs)),
    )

    app_module.start_app(host="127.0.0.1", port=54321, auto_open=True)

    expected_url = f"http://127.0.0.1:54321?token={app_module.SECURITY_TOKEN}"
    assert opened_urls == [expected_url]
    assert run_calls == [
        (
            app_module.app,
            {
                "host": "127.0.0.1",
                "port": 54321,
                "debug": False,
                "allow_unsafe_werkzeug": True,
            },
        )
    ]
