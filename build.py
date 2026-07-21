#!/usr/bin/env python
"""使用项目虚拟环境中的 PyInstaller 构建 Windows 单文件程序。"""
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SPEC_FILE = PROJECT_ROOT / "PixivDownloader.spec"


def build() -> Path:
    """清理旧缓存并构建，返回生成的可执行文件路径。"""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            str(SPEC_FILE),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    executable = PROJECT_ROOT / "dist" / "PixivDownloader.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller 未生成预期文件：{executable}")
    return executable


if __name__ == "__main__":
    print(build())
