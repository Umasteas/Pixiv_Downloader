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


def test_task_event_publisher_reaches_socketio_test_client():
    client = app_module.socketio.test_client(app_module.app)
    payload = {
        "task_id": 7,
        "total_count": 2,
        "downloaded_count": 1,
        "skipped_count": 0,
        "failed_count": 0,
        "current_speed": 1.25,
        "eta_seconds": 3.0,
        "current_files": [{"filename": "作品.jpg", "progress": 50}],
    }
    try:
        app_module.task_manager.publish("progress_update", payload)

        assert client.get_received() == [{
            "name": "progress_update",
            "args": [payload],
            "namespace": "/",
        }]
    finally:
        client.disconnect()


def test_frontend_declares_live_task_progress_contract():
    response = app_module.app.test_client().get("/static/app.js")

    assert response.status_code == 200
    script = response.get_data(as_text=True)
    for event in ("progress_update", "task_complete", "error"):
        assert f"socket.on('{event}'" in script
    for marker in (
        "liveTaskProgress",
        "progress-bar",
        "aria-valuenow",
        "current_speed",
        "eta_seconds",
        "current_files",
        "下载",
        "跳过",
        "失败",
    ):
        assert marker in script


def _stub_successful_connectivity(monkeypatch):
    class StubProxyManager:
        async def test_connection(self, values=None):
            return True, "Pixiv 连接成功（HTTP 200）"

    monkeypatch.setattr(app_module, "proxy_manager", StubProxyManager())

def test_start_app_opens_browser_and_runs_server(monkeypatch):
    """启用 auto_open 时打开带 token 的 URL，然后启动 SocketIO 服务器。"""
    opened_urls = []
    run_calls = []

    _stub_successful_connectivity(monkeypatch)
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
    _stub_successful_connectivity(monkeypatch)
    monkeypatch.setattr(app_module, "auth_manager", stub)
    monkeypatch.setattr(app_module.socketio, "run", lambda *args, **kwargs: None)

    app_module.start_app(port=54322, auto_open=False)

    assert stub.get_access_token_calls == 1


def test_start_app_reports_proxy_hint_when_pixiv_is_unreachable(monkeypatch, capsys):
    class StubProxyManager:
        async def test_connection(self):
            return False, "Pixiv 连接失败：timeout"

    monkeypatch.setattr(app_module, "proxy_manager", StubProxyManager())
    monkeypatch.setattr(app_module.socketio, "run", lambda *args, **kwargs: None)

    app_module.start_app(port=54323, auto_open=False)

    output = capsys.readouterr().out
    assert "Pixiv 连接失败：timeout" in output
    assert "请在设置页面配置代理" in output


def test_startup_connectivity_failure_is_visible_in_web_ui(monkeypatch):
    class StubProxyManager:
        async def test_connection(self, values=None):
            return False, "Pixiv 连接失败，请检查网络或代理设置。"

    monkeypatch.setattr(app_module, "proxy_manager", StubProxyManager())
    monkeypatch.setattr(app_module.socketio, "run", lambda *args, **kwargs: None)
    app_module.start_app(port=54324, auto_open=False)

    response = app_module.app.test_client().get(
        f"/?token={app_module.SECURITY_TOKEN}"
    )
    page = response.get_data(as_text=True)

    assert 'id="startup-proxy-alert"' in page
    assert "Pixiv 连接失败，请检查网络或代理设置。" in page


def test_app_initializes_history_database_and_required_tables():
    from src.pixiv_downloader.database import DatabaseManager

    assert isinstance(app_module.database_manager, DatabaseManager)
    tables = {
        row[0]
        for row in app_module.database_manager.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"tasks", "downloads", "followed_artists"}.issubset(tables)


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


def test_proxy_settings_ui_exposes_four_modes_credentials_and_actions(monkeypatch):
    stub = StubAuthManager(configured=True)
    monkeypatch.setattr(app_module, "auth_manager", stub)

    response = app_module.app.test_client().get(
        f"/?token={app_module.SECURITY_TOKEN}"
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    for value in ("none", "system", "http", "socks5"):
        assert f'value="{value}"' in page
    for element_id in (
        "proxy-host",
        "proxy-port",
        "proxy-username",
        "proxy-password",
        "proxy-clear-password",
        "proxy-save",
        "proxy-test",
        "proxy-feedback",
    ):
        assert f'id="{element_id}"' in page


def test_proxy_frontend_loads_saves_and_tests_through_api():
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "fetch('/api/proxy')" in script
    assert "fetch('/api/proxy'," in script
    assert "fetch('/api/proxy/test'" in script
    assert "proxyForm.addEventListener('submit'" in script
    assert "proxyTest.addEventListener('click'" in script


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


def test_config_api_rejects_proxy_updates_that_bypass_validation(tmp_path, monkeypatch):
    from src.pixiv_downloader.config_manager import ConfigManager

    config = ConfigManager(str(tmp_path / "config.yaml"))
    monkeypatch.setattr(app_module, "config_manager", config)

    response = app_module.app.test_client().post(
        "/api/config",
        json={"proxy": {"type": "ftp", "password": "plaintext"}},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "代理配置必须通过专用代理接口校验并保存"
    }
    assert config.config["proxy"]["type"] == "none"


def test_proxy_api_saves_validated_yaml_and_masks_password(tmp_path, monkeypatch):
    from src.pixiv_downloader.config_manager import ConfigManager
    from src.pixiv_downloader.proxy import ProxyManager

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config)
    monkeypatch.setattr(app_module, "proxy_manager", manager)
    monkeypatch.setattr(app_module, "config_manager", config)
    client = app_module.app.test_client()

    response = client.post(
        "/api/proxy",
        json={
            "type": "http",
            "host": "proxy.local",
            "port": 8080,
            "username": "alice",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["proxy"]["password"] == "***"
    raw_config = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "secret" not in raw_config
    assert "password_encrypted:" in raw_config
    assert manager.settings["password"] == "secret"
    assert client.get("/api/proxy").get_json()["proxy"]["password"] == "***"
    generic_proxy = client.get("/api/config").get_json()["proxy"]
    assert "password_encrypted" not in generic_proxy
    assert generic_proxy["password"] == "***"


def test_proxy_api_reapplies_saved_proxy_to_active_pixiv_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.pixiv_downloader.config_manager import ConfigManager
    from src.pixiv_downloader.proxy import ProxyManager

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config)
    active_api = SimpleNamespace(
        requests=SimpleNamespace(trust_env=True),
        requests_kwargs={},
    )
    monkeypatch.setattr(app_module, "proxy_manager", manager)
    monkeypatch.setattr(app_module.auth_manager, "api", active_api)

    response = app_module.app.test_client().post(
        "/api/proxy",
        json={"type": "http", "host": "proxy.local", "port": 8080},
    )

    assert response.status_code == 200
    assert active_api.requests_kwargs["proxies"] == {
        "http": "http://proxy.local:8080",
        "https": "http://proxy.local:8080",
    }


def test_proxy_api_rejects_non_object_json_without_server_error(monkeypatch):
    client = app_module.app.test_client()

    response = client.post("/api/proxy", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "error": "代理配置必须是 JSON 对象",
    }


def test_proxy_connection_endpoint_tests_candidate_without_saving(tmp_path, monkeypatch):
    from src.pixiv_downloader.config_manager import ConfigManager
    from src.pixiv_downloader.proxy import ProxyManager

    calls = []

    class FakeResponse:
        status = 502

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config, session_factory=lambda **kwargs: FakeSession())
    monkeypatch.setattr(app_module, "proxy_manager", manager)

    response = app_module.app.test_client().post(
        "/api/proxy/test",
        json={"type": "http", "host": "candidate.local", "port": 3128},
    )

    assert response.status_code == 502
    assert calls == [
        (
            "https://www.pixiv.net",
            {"proxy": "http://candidate.local:3128", "timeout": 10},
        )
    ]
    persisted = ConfigManager(str(tmp_path / "config.yaml")).config["proxy"]
    assert persisted["type"] == "none"


def test_proxy_connection_endpoint_reports_mocked_network_result(monkeypatch):
    class StubProxyManager:
        async def test_connection(self, values=None):
            assert values == {"type": "none"}
            return True, "Pixiv 连接成功（HTTP 200）"

    monkeypatch.setattr(app_module, "proxy_manager", StubProxyManager())

    response = app_module.app.test_client().post(
        "/api/proxy/test", json={"type": "none"}
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "message": "Pixiv 连接成功（HTTP 200）",
    }


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
