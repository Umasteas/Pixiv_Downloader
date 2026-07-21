"""Flask 应用主入口"""
import asyncio
import webbrowser
import secrets
import copy
import atexit
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from .auth import AuthError, AuthManager
from .config_manager import ConfigManager
from .database import DatabaseManager
from .proxy import ProxyConfigError, ProxyManager
from .runtime import runtime_paths

RUNTIME_PATHS = runtime_paths()
app = Flask(
    __name__,
    template_folder=str(RUNTIME_PATHS.resource_dir / 'templates'),
    static_folder=str(RUNTIME_PATHS.resource_dir / 'static'),
)
app.config['SECRET_KEY'] = secrets.token_hex(16)

socketio = SocketIO(app, cors_allowed_origins="*")
config_manager = ConfigManager(str(RUNTIME_PATHS.config_file))
auth_manager = AuthManager(config_manager)
proxy_manager = ProxyManager(config_manager)
database_manager = DatabaseManager(RUNTIME_PATHS.database_file)
atexit.register(database_manager.close)

# 生成随机 token 用于 URL
SECURITY_TOKEN = secrets.token_urlsafe(16)
startup_connectivity = None
tray_icon = None

@app.route('/')
def index():
    """主页"""
    token = request.args.get('token', '')
    if token != SECURITY_TOKEN:
        return "Unauthorized", 403
    return render_template(
        'index.html',
        needs_setup=not auth_manager.has_saved_token(),
        startup_connectivity=startup_connectivity,
    )

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """返回本机是否已保存 Pixiv 凭据。"""
    return jsonify({"configured": auth_manager.has_saved_token()})

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """验证并加密保存用户提交的 Refresh Token。"""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get('refresh_token', '')
    try:
        auth_manager.login(refresh_token)
    except AuthError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({
        "success": True,
        "message": "Token 验证成功，已安全保存。",
    })

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置"""
    config = copy.deepcopy(config_manager.load_config())
    # 隐藏敏感信息，同时避免把掩码写回内存中的真实配置。
    if 'pixiv' in config and 'refresh_token' in config['pixiv']:
        config['pixiv']['refresh_token'] = '***' if config['pixiv']['refresh_token'] else ''
    if 'proxy' in config:
        encrypted_password = config['proxy'].pop('password_encrypted', '')
        legacy_password = config['proxy'].pop('password', '')
        config['proxy']['password'] = (
            '***' if encrypted_password or legacy_password else ''
        )
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置"""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    if "refresh_token" in data.get("pixiv", {}):
        return jsonify({
            "error": "Refresh Token 必须通过认证接口验证并加密保存"
        }), 400
    if "proxy" in data:
        return jsonify({
            "error": "代理配置必须通过专用代理接口校验并保存"
        }), 400
    
    for section, values in data.items():
        if isinstance(values, dict):
            for key, value in values.items():
                config_manager.update_setting(section, key, value)
    
    return jsonify({"success": True})


def _public_proxy_settings(settings):
    public = dict(settings)
    if public.get("password"):
        public["password"] = "***"
    return public


@app.route('/api/proxy', methods=['GET'])
def get_proxy():
    """返回不泄露密码的代理配置。"""
    return jsonify({"proxy": _public_proxy_settings(proxy_manager.settings)})


@app.route('/api/proxy', methods=['POST'])
def update_proxy():
    """校验并保存代理配置。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "代理配置必须是 JSON 对象",
        }), 400
    if data.get("password") == "***":
        data["password"] = proxy_manager.settings.get("password", "")
    try:
        saved = proxy_manager.save(data)
    except ProxyConfigError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    active_api = getattr(auth_manager, "api", None)
    if active_api is not None:
        proxy_manager.apply_to_pixiv(active_api)
    return jsonify({"success": True, "proxy": _public_proxy_settings(saved)})


@app.route('/api/proxy/test', methods=['POST'])
def test_proxy():
    """使用候选设置探测 Pixiv，可用性测试不会改写配置。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "代理配置必须是 JSON 对象",
        }), 400
    success, message = asyncio.run(proxy_manager.test_connection(data))
    status = 200 if success else 502
    return jsonify({"success": success, "message": message}), status


@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """获取任务列表"""
    # TODO: 实现任务查询
    return jsonify({"tasks": []})

@app.route('/api/tasks', methods=['POST'])
def create_task():
    """创建任务"""
    # TODO: 实现任务创建
    data = request.json
    return jsonify({"success": True, "task_id": 1})

@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    print('Client disconnected')

def start_app(
    host='127.0.0.1',
    port=5000,
    auto_open=True,
    *,
    enable_tray=False,
    tray_factory=None,
):
    """启动应用"""
    global startup_connectivity, tray_icon
    url = f'http://{host}:{port}?token={SECURITY_TOKEN}'
    print(f'Starting Pixiv Downloader...')
    print(f'Access URL: {url}')

    reachable, connectivity_message = asyncio.run(proxy_manager.test_connection())
    startup_connectivity = {
        "reachable": reachable,
        "message": connectivity_message,
    }
    print(connectivity_message)
    if not reachable:
        print('Pixiv 当前不可达，请在设置页面配置代理后重试。')

    if auth_manager.has_saved_token():
        try:
            auth_manager.get_access_token()
            print('Pixiv Token restored successfully.')
        except AuthError as exc:
            # 认证失败不阻止本地界面启动，用户仍可在引导页重新配置。
            print(f'Pixiv Token restore failed: {exc}')
    
    if enable_tray:
        if tray_factory is None:
            from .tray import create_tray_icon

            tray_factory = create_tray_icon
        tray_icon = tray_factory(url)
        tray_icon.run_detached()

    if auto_open:
        webbrowser.open(url)
    
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
