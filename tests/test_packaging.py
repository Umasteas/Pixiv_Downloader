"""PyInstaller 单文件打包与便携模式验收测试。"""
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_paths_use_executable_directory_for_portable_data(monkeypatch, tmp_path):
    from src.pixiv_downloader.runtime import runtime_paths

    executable = tmp_path / "portable" / "PixivDownloader.exe"
    bundle = tmp_path / "_MEI12345"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    paths = runtime_paths()

    assert paths.application_dir == executable.parent
    assert paths.resource_dir == bundle
    assert paths.config_file == executable.parent / "config.yaml"
    assert paths.database_file == executable.parent / "history.db"
    assert paths.logs_dir == executable.parent / "logs"
    assert paths.logs_dir.is_dir()


def test_pyinstaller_spec_build_script_dependencies_and_assets_are_declared():
    spec_path = PROJECT_ROOT / "PixivDownloader.spec"
    build_path = PROJECT_ROOT / "build.py"
    readme_path = PROJECT_ROOT / "README.md"

    assert spec_path.is_file()
    assert build_path.is_file()
    assert readme_path.is_file()

    spec = spec_path.read_text(encoding="utf-8")
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    readme = readme_path.read_text(encoding="utf-8")

    assert "PyInstaller==" in requirements
    assert "pystray==" in requirements
    assert "('templates', 'templates')" in spec
    assert "('static', 'static')" in spec
    for dependency in (
        "flask",
        "flask_socketio",
        "pixivpy3",
        "aiohttp",
        "aiohttp_socks",
        "socks",
        "engineio.async_drivers.threading",
        "pystray",
    ):
        assert dependency in spec
    assert "name='PixivDownloader'" in spec
    assert "console=False" in spec
    assert "PyInstaller" in readme
    assert "便携模式" in readme


def test_system_tray_menu_can_open_interface_and_exit(monkeypatch):
    from types import SimpleNamespace

    import pystray
    from src.pixiv_downloader.tray import create_tray_icon

    opened = []
    exited = []

    class FakeIcon:
        def __init__(self, name, image, title, menu):
            self.name = name
            self.image = image
            self.title = title
            self.menu = menu
            self.stopped = False

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(pystray, "Icon", FakeIcon)
    monkeypatch.setattr(pystray, "Menu", lambda *items: items)
    monkeypatch.setattr(
        pystray,
        "MenuItem",
        lambda text, action, **kwargs: SimpleNamespace(
            text=text, action=action, default=kwargs.get("default", False)
        ),
    )

    icon = create_tray_icon(
        "http://127.0.0.1:5000/?token=safe",
        browser_open=opened.append,
        exit_process=exited.append,
    )
    open_item, exit_item = icon.menu

    open_item.action(icon, open_item)
    exit_item.action(icon, exit_item)

    assert icon.name == "PixivDownloader"
    assert icon.title == "Pixiv 批量下载工具"
    assert open_item.text == "打开界面"
    assert open_item.default is True
    assert exit_item.text == "退出"
    assert opened == ["http://127.0.0.1:5000/?token=safe"]
    assert icon.stopped is True
    assert exited == [0]


def test_start_app_runs_tray_in_background_with_same_token_url(monkeypatch):
    from src.pixiv_downloader import app as app_module

    tray_urls = []

    class StubProxyManager:
        async def test_connection(self, values=None):
            return True, "Pixiv 连接成功（HTTP 200）"

    class StubTray:
        def run_detached(self):
            tray_urls.append("detached")

    def tray_factory(url):
        tray_urls.append(url)
        return StubTray()

    monkeypatch.setattr(app_module, "proxy_manager", StubProxyManager())
    monkeypatch.setattr(app_module.socketio, "run", lambda *args, **kwargs: None)

    app_module.start_app(
        host="127.0.0.1",
        port=54325,
        auto_open=False,
        enable_tray=True,
        tray_factory=tray_factory,
    )

    assert tray_urls == [
        f"http://127.0.0.1:54325?token={app_module.SECURITY_TOKEN}",
        "detached",
    ]
    assert "enable_tray=True" in (PROJECT_ROOT / "main.py").read_text(
        encoding="utf-8"
    )


def test_application_services_and_assets_use_runtime_paths():
    from src.pixiv_downloader import app as app_module

    assert Path(app_module.app.template_folder).resolve() == PROJECT_ROOT / "templates"
    assert Path(app_module.app.static_folder).resolve() == PROJECT_ROOT / "static"
    assert app_module.config_manager.config_path.resolve() == PROJECT_ROOT / "config.yaml"
    assert Path(app_module.database_manager.database_path).resolve() == PROJECT_ROOT / "history.db"
    assert (PROJECT_ROOT / "logs").is_dir()


def test_runtime_paths_use_project_root_during_source_execution(monkeypatch):
    from src.pixiv_downloader.runtime import runtime_paths

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    paths = runtime_paths()

    assert paths.application_dir == PROJECT_ROOT
    assert paths.resource_dir == PROJECT_ROOT
