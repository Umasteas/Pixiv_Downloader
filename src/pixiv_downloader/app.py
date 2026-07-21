"""Flask 应用主入口"""
import webbrowser
import secrets
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from .config_manager import ConfigManager

app = Flask(__name__, 
            template_folder='../../templates',
            static_folder='../../static')
app.config['SECRET_KEY'] = secrets.token_hex(16)

socketio = SocketIO(app, cors_allowed_origins="*")
config_manager = ConfigManager()

# 生成随机 token 用于 URL
SECURITY_TOKEN = secrets.token_urlsafe(16)

@app.route('/')
def index():
    """主页"""
    token = request.args.get('token', '')
    if token != SECURITY_TOKEN:
        return "Unauthorized", 403
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置"""
    config = config_manager.load_config()
    # 隐藏敏感信息
    if 'pixiv' in config and 'refresh_token' in config['pixiv']:
        config['pixiv']['refresh_token'] = '***' if config['pixiv']['refresh_token'] else ''
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置"""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
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
    
    if auto_open:
        webbrowser.open(url)
    
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
