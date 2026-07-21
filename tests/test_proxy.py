"""代理配置与网络适配行为测试。"""
import importlib.util
from pathlib import Path

import pytest

from src.pixiv_downloader.config_manager import ConfigManager
from src.pixiv_downloader.proxy import ProxyConfigError, ProxyManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_socks_dependencies_are_declared_and_importable():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "aiohttp-socks==" in requirements
    assert "PySocks==" in requirements
    assert importlib.util.find_spec("aiohttp_socks") is not None
    assert importlib.util.find_spec("socks") is not None


def test_http_proxy_builds_authenticated_url_from_saved_configuration(tmp_path):
    config = ConfigManager(str(tmp_path / "config.yaml"))
    config.config["proxy"] = {
        "type": "http",
        "host": "proxy.example.com",
        "port": 8080,
        "username": "user@example.com",
        "password": "p@ss word",
    }

    manager = ProxyManager(config)

    assert manager.proxy_url == (
        "http://user%40example.com:p%40ss%20word@proxy.example.com:8080"
    )


def test_save_validates_proxy_type_host_and_port_before_persisting(tmp_path):
    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config)

    with pytest.raises(ProxyConfigError, match="代理类型"):
        manager.save({"type": "ftp"})
    with pytest.raises(ProxyConfigError, match="地址"):
        manager.save({"type": "http", "host": "", "port": 8080})
    with pytest.raises(ProxyConfigError, match="端口"):
        manager.save({"type": "socks5", "host": "localhost", "port": 70000})
    with pytest.raises(ProxyConfigError, match="地址"):
        manager.save({"type": "http", "host": "http://proxy.local/path", "port": 8080})
    with pytest.raises(ProxyConfigError, match="地址"):
        manager.save({"type": "http", "host": "bad host", "port": 8080})

    saved = manager.save(
        {
            "type": "socks5",
            "host": "localhost",
            "port": "1080",
            "username": "alice",
            "password": "secret",
        }
    )

    assert saved == {
        "type": "socks5",
        "host": "localhost",
        "port": 1080,
        "username": "alice",
        "password": "secret",
    }
    reloaded = ConfigManager(str(tmp_path / "config.yaml"))
    assert reloaded.config["proxy"]["type"] == "socks5"
    assert "password" not in reloaded.config["proxy"]
    assert ProxyManager(reloaded).settings == saved


def test_legacy_disabled_proxy_migrates_without_accidentally_enabling_socks(tmp_path):
    config_path = tmp_path / "config.yaml"
    config = ConfigManager(str(config_path))
    config.config["proxy"] = {
        "enabled": False,
        "type": "socks5",
        "host": "127.0.0.1",
        "port": 1080,
        "username": "",
        "password": "",
    }
    config.save_config()

    manager = ProxyManager(config)

    assert manager.settings["type"] == "none"
    persisted = ConfigManager(str(config_path)).config["proxy"]
    assert persisted["type"] == "none"
    assert "enabled" not in persisted
    assert "password" not in persisted


def test_saved_proxy_password_is_encrypted_and_can_be_explicitly_cleared(tmp_path):
    config_path = tmp_path / "config.yaml"
    config = ConfigManager(str(config_path))
    manager = ProxyManager(config)

    manager.save(
        {
            "type": "http",
            "host": "proxy.local",
            "port": 8080,
            "username": "alice",
            "password": "secret-password",
        }
    )

    raw = config_path.read_text(encoding="utf-8")
    assert "secret-password" not in raw
    assert "password_encrypted:" in raw
    assert manager.settings["password"] == "secret-password"

    manager.save(
        {
            "type": "http",
            "host": "proxy.local",
            "port": 8080,
            "username": "alice",
            "password": "***",
            "clear_password": True,
        }
    )
    assert manager.settings["password"] == ""

    manager.save({"type": "none"})
    assert manager.settings == {
        "type": "none",
        "host": "",
        "port": 0,
        "username": "",
        "password": "",
    }


def test_pixivpy_requests_receive_selected_proxy_and_none_disables_environment(tmp_path):
    class FakeSession:
        trust_env = True

    class FakePixivAPI:
        def __init__(self):
            self.requests = FakeSession()
            self.requests_kwargs = {}

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config)
    api = FakePixivAPI()

    manager.save({"type": "http", "host": "127.0.0.1", "port": 7890})
    manager.apply_to_pixiv(api)
    assert api.requests_kwargs["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }

    manager.save({"type": "system"})
    manager.apply_to_pixiv(api)
    assert "proxies" not in api.requests_kwargs
    assert api.requests.trust_env is True

    manager.save(
        {
            "type": "socks5",
            "host": "127.0.0.1",
            "port": 1080,
            "username": "alice",
            "password": "p@ss word",
        }
    )
    manager.apply_to_pixiv(api)
    assert api.requests_kwargs["proxies"] == {
        "http": "socks5h://alice:p%40ss%20word@127.0.0.1:1080",
        "https": "socks5h://alice:p%40ss%20word@127.0.0.1:1080",
    }
    assert api.requests.trust_env is False

    manager.save({"type": "none"})
    manager.apply_to_pixiv(api)
    assert "proxies" not in api.requests_kwargs
    assert api.requests.trust_env is False


@pytest.mark.asyncio
async def test_aiohttp_options_cover_none_system_http_and_socks5(tmp_path):
    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config)

    manager.save({"type": "none"})
    assert manager.aiohttp_options() == ({"trust_env": False}, {})

    manager.save({"type": "system"})
    assert manager.aiohttp_options() == ({"trust_env": True}, {})

    manager.save({"type": "http", "host": "proxy.local", "port": 3128})
    assert manager.aiohttp_options() == (
        {"trust_env": False},
        {"proxy": "http://proxy.local:3128"},
    )

    manager.save({"type": "socks5", "host": "proxy.local", "port": 1080})
    session_options, request_options = manager.aiohttp_options()
    assert session_options["trust_env"] is False
    assert session_options["connector"].__class__.__name__ == "ProxyConnector"
    assert session_options["connector"]._rdns is True
    assert request_options == {}


@pytest.mark.asyncio
async def test_connection_uses_aiohttp_proxy_options_without_real_network(tmp_path):
    calls = []

    class FakeResponse:
        status = 200

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

    def session_factory(**kwargs):
        calls.append(("session", kwargs))
        return FakeSession()

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config, session_factory=session_factory)
    manager.save({"type": "http", "host": "proxy.local", "port": 3128})

    success, message = await manager.test_connection()

    assert success is True
    assert "连接成功" in message
    assert calls == [
        ("session", {"trust_env": False}),
        ("https://www.pixiv.net", {
            "proxy": "http://proxy.local:3128",
            "timeout": 10,
        }),
    ]


@pytest.mark.asyncio
async def test_connection_error_never_exposes_proxy_credentials(tmp_path):
    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            raise RuntimeError(
                "failed through http://alice:secret-password@proxy.local:3128"
            )

    config = ConfigManager(str(tmp_path / "config.yaml"))
    manager = ProxyManager(config, session_factory=lambda **kwargs: FailingSession())
    manager.save(
        {
            "type": "http",
            "host": "proxy.local",
            "port": 3128,
            "username": "alice",
            "password": "secret-password",
        }
    )

    success, message = await manager.test_connection()

    assert success is False
    assert message == "Pixiv 连接失败，请检查网络或代理设置。"
    assert "alice" not in message
    assert "secret-password" not in message
