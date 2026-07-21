"""源代码与 PyInstaller 单文件运行时的路径解析。"""
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    application_dir: Path
    resource_dir: Path
    config_file: Path
    database_file: Path
    logs_dir: Path


def runtime_paths() -> RuntimePaths:
    """定位内置资源和可执行文件旁的便携数据目录。"""
    project_root = Path(__file__).resolve().parents[2]
    frozen = bool(getattr(sys, "frozen", False))
    application_dir = (
        Path(sys.executable).resolve().parent if frozen else project_root
    )
    resource_dir = (
        Path(getattr(sys, "_MEIPASS")).resolve() if frozen else project_root
    )
    logs_dir = application_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        application_dir=application_dir,
        resource_dir=resource_dir,
        config_file=application_dir / "config.yaml",
        database_file=application_dir / "history.db",
        logs_dir=logs_dir,
    )
