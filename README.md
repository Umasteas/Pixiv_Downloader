# Pixiv 批量下载工具

本项目提供本地 Web 界面的 Pixiv 批量下载工具。应用只监听 `127.0.0.1:5000`，启动时自动打开带随机安全 token 的浏览器地址。

## 从源码运行

```bash
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python main.py
```

## 构建 Windows 单文件程序

项目使用 PyInstaller 构建单个 `PixivDownloader.exe`：

```bash
venv/Scripts/python build.py
```

构建产物位于：

```text
dist/PixivDownloader.exe
```

`PixivDownloader.spec` 会打包 Flask、Flask-SocketIO、pixivpy、aiohttp、SOCKS 代理依赖、系统托盘依赖，以及 `templates/`、`static/` 资源。

## 使用可执行程序

1. 将 `PixivDownloader.exe` 放入一个可写目录。
2. 双击运行；应用会启动本地服务器，并打开包含随机 token 的浏览器页面。
3. 系统托盘菜单提供“打开界面”和“退出”。
4. 首次使用时在页面中配置 Pixiv Refresh Token 和可选代理。

## 便携模式

以下用户数据始终保存在 `PixivDownloader.exe` 同目录，而不是 PyInstaller 的临时解压目录：

```text
config.yaml
history.db
logs/
.pixiv_downloader.key
```

因此可以连同可执行文件和这些数据一起移动或备份。请保护 `.pixiv_downloader.key`；它用于解密 Refresh Token 和代理密码。

## 安全说明

- Web 页面必须携带每次启动随机生成的 token。
- 服务器仅绑定本机回环地址。
- 代理密码和 Pixiv Refresh Token 使用本地 Fernet 密钥加密。
- 不要将 `config.yaml`、`.pixiv_downloader.key` 或 `history.db` 提交到版本控制。
