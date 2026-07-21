"""Pixiv Refresh Token 认证与本地加密存储。"""
from pathlib import Path
from typing import Callable, Optional

from cryptography.fernet import Fernet, InvalidToken
from pixivpy3 import AppPixivAPI

from .config_manager import ConfigManager


class AuthError(RuntimeError):
    """认证失败或本地凭据无法读取。"""


class AuthManager:
    """管理 Pixiv 登录、访问令牌与加密后的 Refresh Token。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        *,
        key_path: Optional[Path] = None,
        api_factory: Callable[[], AppPixivAPI] = AppPixivAPI,
    ) -> None:
        self.config_manager = config_manager
        self.key_path = Path(
            key_path
            if key_path is not None
            else config_manager.config_path.with_name(".pixiv_downloader.key")
        )
        self.api_factory = api_factory
        self.api = None
        self._access_token: Optional[str] = None

    def has_saved_token(self) -> bool:
        """配置中是否存在已加密的 Refresh Token。"""
        return bool(self.config_manager.get_setting("pixiv", "refresh_token", ""))

    def login(self, refresh_token: str) -> str:
        """验证 Refresh Token，成功后加密保存并返回 Access Token。"""
        refresh_token = refresh_token.strip()
        if not refresh_token:
            raise AuthError("Refresh Token 不能为空")

        api = self.api_factory()
        try:
            api.auth(refresh_token=refresh_token)
        except Exception as exc:
            raise AuthError(f"Token 验证失败：{exc}") from exc

        access_token = getattr(api, "access_token", None)
        if not access_token:
            raise AuthError("Token 验证失败：Pixiv 未返回 Access Token")

        ciphertext = self._fernet().encrypt(refresh_token.encode("utf-8")).decode(
            "ascii"
        )
        self.config_manager.update_setting("pixiv", "refresh_token", ciphertext)
        self.api = api
        self._access_token = access_token
        return access_token

    def get_access_token(self) -> str:
        """返回当前 Access Token；必要时用已保存凭据自动刷新。"""
        if self._access_token:
            return self._access_token

        ciphertext = self.config_manager.get_setting("pixiv", "refresh_token", "")
        if not ciphertext:
            raise AuthError("尚未配置 Refresh Token")

        try:
            refresh_token = self._fernet().decrypt(
                ciphertext.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise AuthError("无法解密已保存的 Refresh Token") from exc

        return self.login(refresh_token)

    def validate_token(self, refresh_token: str) -> bool:
        """验证并保存 Token，以布尔值报告结果。"""
        try:
            self.login(refresh_token)
        except AuthError:
            return False
        return True

    def _fernet(self) -> Fernet:
        """读取或创建与配置文件同目录的本地 Fernet 密钥。"""
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            try:
                self.key_path.chmod(0o600)
            except OSError:
                # Windows ACL 不完全遵循 POSIX chmod；文件仍保持在本地工具目录。
                pass
        return Fernet(key)
