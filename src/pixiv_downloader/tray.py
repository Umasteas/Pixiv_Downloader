"""Windows 系统托盘入口。"""
import os
import webbrowser
from collections.abc import Callable

from PIL import Image, ImageDraw
import pystray


def _tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (20, 28, 45, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(70, 130, 255, 255))
    draw.ellipse((20, 17, 44, 41), fill=(255, 255, 255, 255))
    draw.rectangle((27, 36, 37, 50), fill=(255, 255, 255, 255))
    return image


def create_tray_icon(
    url: str,
    *,
    browser_open: Callable[[str], object] = webbrowser.open,
    exit_process: Callable[[int], object] = os._exit,
):
    """创建含“打开界面/退出”的托盘图标。"""

    def open_interface(icon, item):
        browser_open(url)

    def exit_application(icon, item):
        icon.stop()
        exit_process(0)

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", open_interface, default=True),
        pystray.MenuItem("退出", exit_application),
    )
    return pystray.Icon(
        "PixivDownloader",
        _tray_image(),
        "Pixiv 批量下载工具",
        menu,
    )
