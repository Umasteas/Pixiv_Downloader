"""认证模块行为测试。"""
from pathlib import Path

import pytest

from src.pixiv_downloader.auth import AuthError, AuthManager
from src.pixiv_downloader.config_manager import ConfigManager


class FakePixivAPI:
    def __init__(self, *, reject=False):
        self.reject = reject
        self.auth_calls = []
        self.access_token = None

    def auth(self, *, refresh_token):
        self.auth_calls.append(refresh_token)
        if self.reject:
            raise RuntimeError("invalid refresh token")
        self.access_token = f"access-for-{refresh_token}"


def make_manager(tmp_path: Path, api: FakePixivAPI):
    config = ConfigManager(str(tmp_path / "config.yaml"))
    return AuthManager(
        config,
        key_path=tmp_path / ".pixiv_downloader.key",
        api_factory=lambda: api,
    ), config


def test_login_encrypts_refresh_token_and_returns_access_token(tmp_path):
    api = FakePixivAPI()
    manager, config = make_manager(tmp_path, api)

    access_token = manager.login("secret-refresh-token")

    assert access_token == "access-for-secret-refresh-token"
    assert api.auth_calls == ["secret-refresh-token"]
    stored_value = config.get_setting("pixiv", "refresh_token")
    assert stored_value
    assert stored_value != "secret-refresh-token"
    assert "secret-refresh-token" not in (tmp_path / "config.yaml").read_text(
        encoding="utf-8"
    )
    assert manager.has_saved_token()


def test_failed_login_does_not_overwrite_saved_token(tmp_path):
    first_api = FakePixivAPI()
    manager, config = make_manager(tmp_path, first_api)
    manager.login("working-token")
    original_ciphertext = config.get_setting("pixiv", "refresh_token")

    failing_manager = AuthManager(
        config,
        key_path=tmp_path / ".pixiv_downloader.key",
        api_factory=lambda: FakePixivAPI(reject=True),
    )

    with pytest.raises(AuthError, match="Token 验证失败"):
        failing_manager.login("bad-token")

    assert config.get_setting("pixiv", "refresh_token") == original_ciphertext


def test_get_access_token_restores_saved_refresh_token_on_next_start(tmp_path):
    first_api = FakePixivAPI()
    manager, config = make_manager(tmp_path, first_api)
    manager.login("persisted-token")

    restored_api = FakePixivAPI()
    restored_manager = AuthManager(
        config,
        key_path=tmp_path / ".pixiv_downloader.key",
        api_factory=lambda: restored_api,
    )

    assert restored_manager.get_access_token() == "access-for-persisted-token"
    assert restored_api.auth_calls == ["persisted-token"]


def test_validate_token_reports_success_and_failure_without_leaking_exceptions(tmp_path):
    valid_manager, _ = make_manager(tmp_path / "valid", FakePixivAPI())
    invalid_manager, _ = make_manager(
        tmp_path / "invalid", FakePixivAPI(reject=True)
    )

    assert valid_manager.validate_token("valid-token") is True
    assert invalid_manager.validate_token("invalid-token") is False
