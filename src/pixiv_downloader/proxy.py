"""统一管理 Pixiv API 与下载请求使用的代理配置。"""
import ipaddress
import re
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from .config_manager import ConfigManager


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)


class ProxyConfigError(ValueError):
    """代理配置无效。"""


class ProxyManager:
    """读取、校验并应用保存在 YAML 中的代理设置。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        *,
        session_factory=None,
        key_path: Path | None = None,
    ) -> None:
        self.config_manager = config_manager
        self.key_path = Path(
            key_path
            if key_path is not None
            else config_manager.config_path.with_name(".pixiv_downloader.key")
        )
        if session_factory is None:
            from aiohttp import ClientSession

            session_factory = ClientSession
        self.session_factory = session_factory
        self._migrate_legacy_password()

    @property
    def settings(self) -> dict:
        """返回供网络层使用的明文内存视图；磁盘始终保存密文。"""
        raw = dict(self.config_manager.config.get("proxy", {}))
        if raw.get("enabled") is False:
            raw["type"] = "none"
        encrypted = str(raw.get("password_encrypted", ""))
        password = self._decrypt(encrypted) if encrypted else ""
        return {
            "type": str(raw.get("type", "none")),
            "host": str(raw.get("host", "")),
            "port": int(raw.get("port", 0) or 0),
            "username": str(raw.get("username", "")),
            "password": password,
        }

    def normalize(self, values: dict, *, existing_password: str = "") -> dict:
        """把不可信 API/前端输入规范化为内存代理配置。"""
        if not isinstance(values, dict):
            raise ProxyConfigError("代理配置必须是 JSON 对象")
        proxy_type = str(values.get("type", "none")).strip().lower()
        if proxy_type not in {"none", "system", "http", "socks5"}:
            raise ProxyConfigError("不支持的代理类型")

        if proxy_type in {"none", "system"}:
            return {
                "type": proxy_type,
                "host": "",
                "port": 0,
                "username": "",
                "password": "",
            }

        host = self._normalize_host(values.get("host", ""))
        raw_port = values.get("port", "")
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise ProxyConfigError("代理端口必须是 1-65535 的整数") from exc
        if not 1 <= port <= 65535:
            raise ProxyConfigError("代理端口必须是 1-65535 的整数")

        supplied_password = str(values.get("password", ""))
        if values.get("clear_password") is True:
            password = ""
        elif supplied_password and supplied_password != "***":
            password = supplied_password
        else:
            password = existing_password

        return {
            "type": proxy_type,
            "host": host,
            "port": port,
            "username": str(values.get("username", "")),
            "password": password,
        }

    def save(self, values: dict) -> dict:
        """校验并完整替换代理配置，凭据只以密文落盘。"""
        normalized = self.normalize(
            values,
            existing_password=self.settings.get("password", ""),
        )
        password = normalized["password"]
        persisted = {
            "type": normalized["type"],
            "host": normalized["host"],
            "port": normalized["port"],
            "username": normalized["username"],
            "password_encrypted": self._encrypt(password) if password else "",
        }
        self.config_manager.config["proxy"] = persisted
        self.config_manager.save_config()
        return dict(normalized)

    def apply_to_pixiv(self, api: object) -> None:
        """把网络代理注入 pixivpy 的 requests 调用层。"""
        settings = self.settings
        proxy_type = settings["type"]
        requests_kwargs = getattr(api, "requests_kwargs")
        requests_kwargs.pop("proxies", None)
        session = getattr(api, "requests")
        session.trust_env = proxy_type == "system"
        if proxy_type in {"http", "socks5"}:
            url = self.build_proxy_url(settings)
            requests_kwargs["proxies"] = {"http": url, "https": url}

    def aiohttp_options(self, settings: dict | None = None) -> tuple[dict, dict]:
        """返回 ClientSession 参数与每次请求参数。"""
        settings = settings or self.settings
        proxy_type = settings["type"]
        session_options: dict = {"trust_env": proxy_type == "system"}
        request_options: dict = {}
        if proxy_type == "http":
            request_options["proxy"] = self.build_proxy_url(settings)
        elif proxy_type == "socks5":
            from aiohttp_socks import ProxyConnector

            proxy_url = self.build_proxy_url(settings)
            # aiohttp-socks 0.8.4 只解析 socks5://，通过 rdns=True
            # 明确让代理端解析目标域名，语义等价于 requests 的 socks5h://。
            connector_url = proxy_url.replace("socks5h://", "socks5://", 1)
            session_options["connector"] = ProxyConnector.from_url(
                connector_url,
                rdns=True,
            )
        return session_options, request_options

    async def test_connection(self, values: dict | None = None) -> tuple[bool, str]:
        """用候选或已保存配置访问 Pixiv；候选配置不会写入磁盘。"""
        try:
            settings = (
                self.normalize(values, existing_password=self.settings["password"])
                if values is not None
                else self.settings
            )
            session_options, request_options = self.aiohttp_options(settings)
            request_options["timeout"] = 10
            async with self.session_factory(**session_options) as session:
                async with session.get(
                    "https://www.pixiv.net", **request_options
                ) as response:
                    if response.status < 400:
                        return True, f"Pixiv 连接成功（HTTP {response.status}）"
                    return False, f"Pixiv 返回 HTTP {response.status}"
        except ProxyConfigError as exc:
            return False, str(exc)
        except Exception:
            # 不把可能包含代理 URL 或凭据的底层异常返回 UI/控制台。
            return False, "Pixiv 连接失败，请检查网络或代理设置。"

    @property
    def proxy_url(self) -> str | None:
        return self.build_proxy_url(self.settings)

    def build_proxy_url(self, settings: dict) -> str | None:
        proxy_type = settings["type"]
        if proxy_type in {"none", "system"}:
            return None
        # socks5h 明确让代理端解析目标域名，适用于本地 DNS 同样受限的环境。
        scheme = "http" if proxy_type == "http" else "socks5h"
        host = settings["host"]
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        credentials = ""
        username = settings.get("username", "")
        password = settings.get("password", "")
        if username:
            credentials = quote(username, safe="")
            if password:
                credentials += f":{quote(password, safe='')}"
            credentials += "@"
        return f"{scheme}://{credentials}{host}:{settings['port']}"

    def _normalize_host(self, value: object) -> str:
        host = str(value).strip()
        if not host:
            raise ProxyConfigError("代理地址不能为空")
        candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass
        if not _HOSTNAME_RE.fullmatch(candidate):
            raise ProxyConfigError("代理地址必须是主机名或 IP，不能包含协议、路径或空格")
        return candidate

    def _fernet(self) -> Fernet:
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
        return Fernet(key)

    def _encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise ProxyConfigError("无法解密已保存的代理密码") from exc

    def _migrate_legacy_password(self) -> None:
        raw = self.config_manager.config.get("proxy", {})
        if not isinstance(raw, dict) or (
            "password" not in raw and "enabled" not in raw
        ):
            return
        was_disabled = raw.get("enabled") is False
        if "password" in raw:
            password = str(raw.pop("password", ""))
            raw["password_encrypted"] = self._encrypt(password) if password else ""
        raw.pop("enabled", None)
        if was_disabled:
            raw["type"] = "none"
        elif raw.get("type") not in {"none", "system", "http", "socks5"}:
            raw["type"] = "none"
        self.config_manager.save_config()
