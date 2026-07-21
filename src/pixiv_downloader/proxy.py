"""统一管理 Pixiv API 与下载请求使用的代理配置。"""
from urllib.parse import quote

from .config_manager import ConfigManager


class ProxyConfigError(ValueError):
    """代理配置无效。"""


class ProxyManager:
    """读取、校验并应用保存在 YAML 中的代理设置。"""

    def __init__(self, config_manager: ConfigManager, *, session_factory=None) -> None:
        self.config_manager = config_manager
        if session_factory is None:
            from aiohttp import ClientSession

            session_factory = ClientSession
        self.session_factory = session_factory

    @property
    def settings(self) -> dict:
        settings = dict(self.config_manager.config.get("proxy", {}))
        # 兼容早期 enabled + type 配置；禁用时不得意外启用默认 SOCKS5。
        if settings.get("enabled") is False:
            settings["type"] = "none"
        settings.pop("enabled", None)
        return settings

    def save(self, values: dict) -> dict:
        """校验并原子替换代理配置。"""
        proxy_type = str(values.get("type", "none")).strip().lower()
        if proxy_type not in {"none", "system", "http", "socks5"}:
            raise ProxyConfigError("不支持的代理类型")

        host = str(values.get("host", "")).strip()
        raw_port = values.get("port", "")
        if proxy_type in {"http", "socks5"} and not host:
            raise ProxyConfigError("代理地址不能为空")
        try:
            port = int(raw_port) if raw_port not in {"", None} else 0
        except (TypeError, ValueError) as exc:
            raise ProxyConfigError("代理端口必须是 1-65535 的整数") from exc
        if proxy_type in {"http", "socks5"} and not 1 <= port <= 65535:
            raise ProxyConfigError("代理端口必须是 1-65535 的整数")

        normalized = {
            "type": proxy_type,
            "host": host,
            "port": port,
            "username": str(values.get("username", "")),
            "password": str(values.get("password", "")),
        }
        self.config_manager.config["proxy"] = normalized
        self.config_manager.save_config()
        return dict(normalized)

    def apply_to_pixiv(self, api: object) -> None:
        """把网络代理注入 pixivpy 的 requests 调用层。"""
        settings = self.settings
        proxy_type = settings.get("type", "none")
        requests_kwargs = getattr(api, "requests_kwargs")
        requests_kwargs.pop("proxies", None)
        session = getattr(api, "requests")
        session.trust_env = proxy_type == "system"
        if proxy_type in {"http", "socks5"}:
            url = self.proxy_url
            requests_kwargs["proxies"] = {"http": url, "https": url}

    def aiohttp_options(self) -> tuple[dict, dict]:
        """返回 ClientSession 参数与每次请求参数。"""
        proxy_type = self.settings.get("type", "none")
        session_options: dict = {"trust_env": proxy_type == "system"}
        request_options: dict = {}
        if proxy_type == "http":
            request_options["proxy"] = self.proxy_url
        elif proxy_type == "socks5":
            from aiohttp_socks import ProxyConnector

            session_options["connector"] = ProxyConnector.from_url(self.proxy_url)
        return session_options, request_options

    async def test_connection(self) -> tuple[bool, str]:
        """通过当前代理访问 Pixiv，供设置页与启动检查复用。"""
        session_options, request_options = self.aiohttp_options()
        request_options["timeout"] = 10
        try:
            async with self.session_factory(**session_options) as session:
                async with session.get(
                    "https://www.pixiv.net", **request_options
                ) as response:
                    if response.status < 400:
                        return True, f"Pixiv 连接成功（HTTP {response.status}）"
                    return False, f"Pixiv 返回 HTTP {response.status}"
        except Exception as exc:
            return False, f"Pixiv 连接失败：{exc}"

    @property
    def proxy_url(self) -> str | None:
        settings = self.settings
        proxy_type = settings.get("type", "none")
        if proxy_type in {"none", "system"}:
            return None

        scheme = "http" if proxy_type == "http" else "socks5"
        host = str(settings.get("host", "")).strip()
        port = settings.get("port")
        credentials = ""
        username = str(settings.get("username", ""))
        password = str(settings.get("password", ""))
        if username:
            credentials = quote(username, safe="")
            if password:
                credentials += f":{quote(password, safe='')}"
            credentials += "@"
        return f"{scheme}://{credentials}{host}:{port}"
