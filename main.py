#!/usr/bin/env python
"""Pixiv 批量下载工具启动脚本"""
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from pixiv_downloader.app import start_app

if __name__ == '__main__':
    start_app(enable_tray=True)
