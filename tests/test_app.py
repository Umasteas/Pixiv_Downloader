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


class StubAuthManager:
    def __init__(self, *, configured=False, error=None):
        self.configured = configured
        self.error = error
        self.login_calls = []
        self.get_access_token_calls = 0

    def has_saved_token(self):
        return self.configured

    def get_access_token(self):
        self.get_access_token_calls += 1
        if self.error:
            raise self.error
        return "restored-access-token"

    def login(self, refresh_token):
        self.login_calls.append(refresh_token)
        if self.error:
            raise self.error
        self.configured = True
        return "access-token"


def test_start_app_restores_saved_token_before_serving(monkeypatch):
    stub = StubAuthManager(configured=True)
    monkeypatch.setattr(app_module, "auth_manager", stub)
    monkeypatch.setattr(app_module.socketio, "run", lambda *args, **kwargs: None)

    app_module.start_app(port=54322, auto_open=False)

    assert stub.get_access_token_calls == 1


def test_first_start_page_shows_welcome_and_token_setup_steps(monkeypatch):
    stub = StubAuthManager(configured=False)
    monkeypatch.setattr(app_module, "auth_manager", stub)

    response = app_module.app.test_client().get(
        f"/?token={app_module.SECURITY_TOKEN}"
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "步骤 1：欢迎" in page
    assert "步骤 2：Token 配置" in page
    assert 'id="refresh-token"' in page


def test_auth_status_and_login_routes_report_success(monkeypatch):
    stub = StubAuthManager(configured=False)
    monkeypatch.setattr(app_module, "auth_manager", stub)
    client = app_module.app.test_client()

    assert client.get("/api/auth/status").get_json() == {"configured": False}

    response = client.post(
        "/api/auth/login", json={"refresh_token": "new-refresh-token"}
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "message": "Token 验证成功，已安全保存。",
    }
    assert stub.login_calls == ["new-refresh-token"]


def test_config_api_masks_token_without_mutating_stored_value(tmp_path, monkeypatch):
    from src.pixiv_downloader.config_manager import ConfigManager

    config = ConfigManager(str(tmp_path / "config.yaml"))
    config.update_setting("pixiv", "refresh_token", "encrypted-token")
    monkeypatch.setattr(app_module, "config_manager", config)

    response = app_module.app.test_client().get("/api/config")

    assert response.status_code == 200
    assert response.get_json()["pixiv"]["refresh_token"] == "***"
    assert config.get_setting("pixiv", "refresh_token") == "encrypted-token"


def test_config_api_rejects_plaintext_refresh_token_updates(tmp_path, monkeypatch):
    from src.pixiv_downloader.config_manager import ConfigManager

    config = ConfigManager(str(tmp_path / "config.yaml"))
    config.update_setting("pixiv", "refresh_token", "encrypted-token")
    monkeypatch.setattr(app_module, "config_manager", config)

    response = app_module.app.test_client().post(
        "/api/config", json={"pixiv": {"refresh_token": "plaintext-token"}}
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Refresh Token 必须通过认证接口验证并加密保存"
    }
    assert config.get_setting("pixiv", "refresh_token") == "encrypted-token"


def test_auth_login_route_reports_validation_failure(monkeypatch):
    from src.pixiv_downloader.auth import AuthError

    stub = StubAuthManager(error=AuthError("Token 验证失败：invalid"))
    monkeypatch.setattr(app_module, "auth_manager", stub)

    response = app_module.app.test_client().post(
        "/api/auth/login", json={"refresh_token": "bad-token"}
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "error": "Token 验证失败：invalid",
    }
