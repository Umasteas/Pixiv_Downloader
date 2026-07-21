"""Flask 应用主入口"""
import webbrowser
import secrets
import copy
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from .auth import AuthError, AuthManager
from .config_manager import ConfigManager

app = Flask(__name__, 
            template_folder='../../templates',
            static_folder='../../static')
app.config['SECRET_KEY'] = secrets.token_hex(16)

socketio = SocketIO(app, cors_allowed_origins="*")
config_manager = ConfigManager()
auth_manager = AuthManager(config_manager)

# 生成随机 token 用于 URL
SECURITY_TOKEN = secrets.token_urlsafe(16)

@app.route('/')
def index():
    """主页"""
    token = request.args.get('token', '')
    if token != SECURITY_TOKEN:
        return "Unauthorized", 403
    return render_template(
        'index.html', needs_setup=not auth_manager.has_saved_token()
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
    
    for section, values in data.items():
        if isinstance(values, dict):
            for key, value in values.items():
                config_manager.update_setting(section, key, value)
    
    return jsonify({"success": True})

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

def start_app(host='127.0.0.1', port=5000, auto_open=True):
    """启动应用"""
    url = f'http://{host}:{port}?token={SECURITY_TOKEN}'
    print(f'Starting Pixiv Downloader...')
    print(f'Access URL: {url}')

    if auth_manager.has_saved_token():
        try:
            auth_manager.get_access_token()
            print('Pixiv Token restored successfully.')
        except AuthError as exc:
            # 认证失败不阻止本地界面启动，用户仍可在引导页重新配置。
            print(f'Pixiv Token restore failed: {exc}')
    
    if auto_open:
        webbrowser.open(url)
    
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
