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
    assert reloaded.config["proxy"] == saved


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
