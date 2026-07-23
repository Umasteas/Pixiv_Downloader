"""Flask 应用主入口"""
import asyncio
import webbrowser
import secrets
import copy
import atexit
from datetime import date
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from .auth import AuthError, AuthManager
from .bookmarks import (
    BookmarksValidationError,
    normalize_bookmarks_query,
    normalize_persisted_bookmarks_query,
)
from .config_manager import ConfigManager
from .download_policy import DownloadPolicy
from .database import DatabaseManager
from .downloader import ArtistDownloader
from .file_organizer import (
    create_bookmarks_directory,
    create_ranking_directory,
    create_search_directory,
)
from .follow_latest import FollowLatestError, FollowLatestScanner
from .filters import FilterValidationError, normalize_filters
from .logging_setup import configure_logging
from .proxy import ProxyConfigError, ProxyManager
from .ranking import RankingValidationError, normalize_ranking_query
from .runtime import runtime_paths
from .search import SearchValidationError, normalize_search_query
from .task_manager import TaskManager, TaskManagerError
from .ugoira import check_ffmpeg

RUNTIME_PATHS = runtime_paths()
app = Flask(
    __name__,
    template_folder=str(RUNTIME_PATHS.resource_dir / 'templates'),
    static_folder=str(RUNTIME_PATHS.resource_dir / 'static'),
)
app.config['SECRET_KEY'] = secrets.token_hex(16)

socketio = SocketIO(app, cors_allowed_origins="*")


def _publish_log_entry(payload):
    """广播已脱敏的 INFO 及以上日志。"""
    socketio.emit("log_entry", payload)


config_manager = ConfigManager(str(RUNTIME_PATHS.config_file))
configure_logging(
    RUNTIME_PATHS.logs_dir,
    config_manager.config.get("logging", {}),
    publish=_publish_log_entry,
)
auth_manager = AuthManager(config_manager)
proxy_manager = ProxyManager(config_manager)
database_manager = DatabaseManager(RUNTIME_PATHS.database_file)
atexit.register(database_manager.close)


def _execute_download_task(task, control):
    """执行持久化下载任务，并从已完成的连续前缀恢复。"""
    query = task.get("query")
    if not isinstance(query, dict):
        raise TaskManagerError("任务缺少有效查询")
    task_type = task["task_type"]
    if task_type == "artist":
        value = query.get("artist_id")
        if type(value) is not int or value <= 0:
            raise TaskManagerError("画师任务缺少有效 ID")
        filters = normalize_filters(task.get("filters"))
    elif task_type == "search":
        try:
            value = normalize_search_query(query)
            filters = normalize_filters(task.get("filters"))
            task_date = date.fromisoformat(str(task.get("created_time", ""))[:10])
        except (SearchValidationError, FilterValidationError, ValueError):
            raise TaskManagerError("搜索任务缺少有效查询") from None
    elif task_type == "ranking":
        try:
            value = normalize_ranking_query(query)
            filters = normalize_filters(task.get("filters"))
        except (RankingValidationError, FilterValidationError):
            raise TaskManagerError("排行榜任务缺少有效查询") from None
    elif task_type == "bookmarks":
        try:
            value = normalize_persisted_bookmarks_query(query)
            filters = normalize_filters(task.get("filters"))
            task_date = date.fromisoformat(str(task.get("created_time", ""))[:10])
        except (BookmarksValidationError, FilterValidationError, ValueError):
            raise TaskManagerError("收藏任务缺少有效查询") from None
    elif task_type in {"follow_latest", "retry_failed"}:
        value = query.get("artwork_ids")
        if (
            not isinstance(value, list)
            or not value
            or any(type(item) is not int or item <= 0 for item in value)
        ):
            raise TaskManagerError("作品 ID 任务缺少有效作品 ID")
    else:
        raise TaskManagerError("暂不支持该任务类型")
    auth_manager.get_access_token()
    if auth_manager.api is None:
        raise AuthError("Pixiv 登录状态不可用")
    runtime_config = config_manager.load_config()
    download_config = runtime_config.get("download", {})
    ugoira_config = runtime_config.get("ugoira", {})
    policy = DownloadPolicy.from_mapping(download_config)
    configured_output = Path(download_config.get("output_dir", "./downloads"))
    output_dir = (
        configured_output
        if configured_output.is_absolute()
        else RUNTIME_PATHS.application_dir / configured_output
    )
    processed = sum(
        int(task.get(key) or 0)
        for key in ("downloaded_count", "skipped_count", "failed_count")
    )
    downloader_options = {
        "output_dir": output_dir.resolve(),
        "policy": policy,
        "ugoira_options": ugoira_config,
    }
    if task_type == "search":
        downloader_options.update({
            "collection_dir": create_search_directory(
                output_dir.resolve(),
                value["keyword"],
                task_date,
            ),
            "source": "search",
        })
    elif task_type == "ranking":
        downloader_options.update({
            "collection_dir": create_ranking_directory(
                output_dir.resolve(),
                value["ranking_type"],
                date.fromisoformat(value["date"]),
            ),
            "source": "ranking",
            "metadata_context": {
                "ranking_type": value["ranking_type"],
                "ranking_date": value["date"],
            },
        })
    elif task_type == "bookmarks":
        downloader_options.update({
            "collection_dir": create_bookmarks_directory(
                output_dir.resolve(),
                task_date,
            ),
            "source": "bookmarks",
            "metadata_context": {
                "visibility": value["visibility"],
                "tag": value["tag"],
                "sort": value["sort"],
            },
            "user_id": auth_manager.get_user_id(),
        })
    downloader = ArtistDownloader(
        auth_manager.api,
        database_manager,
        proxy_manager,
        **downloader_options,
    )
    options = {
        "task_id": task["task_id"],
        "start_index": processed,
        "control": control,
    }
    if task_type == "artist":
        operation = downloader.download_artist(
            value,
            filters=filters,
            **options,
        )
    elif task_type == "search":
        operation = downloader.download_search(
            value["keyword"],
            value["search_type"],
            value["sort"],
            value["limit"],
            filters=filters,
            **options,
        )
    elif task_type == "ranking":
        operation = downloader.download_ranking(
            value["ranking_type"],
            value["date"],
            value["limit"],
            filters=filters,
            **options,
        )
    elif task_type == "bookmarks":
        operation = downloader.download_bookmarks(
            value["visibility"],
            value["tag"],
            value["sort"],
            value["limit"],
            filters=filters,
            artwork_ids=value.get("artwork_ids"),
            snapshot_callback=lambda artwork_ids: database_manager.set_bookmark_snapshot(
                task["task_id"], artwork_ids
            ),
            **options,
        )
    else:
        operation = downloader.download_artwork_ids(value, **options)
    asyncio.run(operation)


def _publish_task_event(event, payload):
    """从后台任务线程广播本机单用户任务事件。"""
    socketio.emit(event, payload)


def _public_task(task):
    """投影可公开给前端的任务字段，隐藏内部候选快照与私密标签。"""
    result = dict(task)
    query = task.get("query")
    if not isinstance(query, dict):
        return result
    public_query = dict(query)
    public_query.pop("artwork_ids", None)
    if (
        task.get("task_type") == "bookmarks"
        and public_query.get("visibility") == "private"
    ):
        public_query["tag_redacted"] = public_query.get("tag") is not None
        public_query["tag"] = None
    result["query"] = public_query
    return result


def _public_task_failures(task, failures):
    """私密收藏及其重试链不向 REST 响应暴露作品 ID。"""
    redact_artwork_ids = task.get("task_type") == "retry_failed" or (
        task.get("task_type") == "bookmarks"
        and isinstance(task.get("query"), dict)
        and task["query"].get("visibility") == "private"
    )
    if not redact_artwork_ids:
        return failures
    return [
        {key: value for key, value in failure.items() if key != "artwork_id"}
        for failure in failures
    ]


task_manager = TaskManager(
    database_manager,
    _execute_download_task,
    publish=_publish_task_event,
)

# 生成随机 token 用于 URL
SECURITY_TOKEN = secrets.token_urlsafe(16)
startup_connectivity = None
startup_ffmpeg = None
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
        startup_ffmpeg=startup_ffmpeg,
        security_token=SECURITY_TOKEN,
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
    except AuthError:
        return jsonify({
            "success": False,
            "error": "Token 验证失败，请检查 Refresh Token 或网络设置。",
        }), 400
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
    if "ugoira" in data:
        return jsonify({
            "error": "动图配置必须通过专用接口校验并保存"
        }), 400
    
    for section, values in data.items():
        if isinstance(values, dict):
            for key, value in values.items():
                config_manager.update_setting(section, key, value)
    if "logging" in data:
        configure_logging(
            RUNTIME_PATHS.logs_dir,
            config_manager.config.get("logging", {}),
            publish=_publish_log_entry,
        )
    
    return jsonify({"success": True})


def _ffmpeg_payload(status):
    return {
        "available": status.available,
        "message": status.message,
        "download_url": status.download_url,
    }


def _public_ugoira_settings(settings):
    return {
        key: bool(settings.get(key))
        for key in ("download_original", "convert_to_gif", "convert_to_mp4")
    }


@app.route('/api/ugoira', methods=['GET', 'POST'])
def ugoira_settings():
    """读取或原子保存三个可叠加 Ugoira 输出选项。"""
    keys = ("download_original", "convert_to_gif", "convert_to_mp4")
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if (
            not isinstance(data, dict)
            or set(data) != set(keys)
            or any(type(data[key]) is not bool for key in keys)
        ):
            return jsonify({"error": "Ugoira 设置必须包含三个布尔选项。"}), 400
        if not any(data.values()):
            return jsonify({"error": "Ugoira 至少选择一种保存格式。"}), 400
        config_manager.config.setdefault("ugoira", {}).update(data)
        config_manager.save_config()
    settings = config_manager.load_config().get("ugoira", {})
    status = check_ffmpeg(str(settings.get("ffmpeg_path", "auto")))
    return jsonify({
        "success": True,
        "ugoira": _public_ugoira_settings(settings),
        "ffmpeg": _ffmpeg_payload(status),
    })


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


@app.route('/api/download/artist', methods=['POST'])
def download_artist():
    """下载指定画师的单图作品。"""
    data = request.get_json(silent=True)
    raw_artist_id = data.get("artist_id") if isinstance(data, dict) else None
    if type(raw_artist_id) is not int or raw_artist_id <= 0:
        return jsonify({"success": False, "error": "画师 ID 必须是正整数"}), 400
    artist_id = raw_artist_id
    task_id = task_manager.create_task(
        task_type="artist",
        query={"artist_id": artist_id},
    )
    return jsonify({
        "success": True,
        "task_id": task_id,
        "status": "pending",
    }), 201


@app.route('/api/follow/latest', methods=['POST'])
def fetch_follow_latest():
    """手动扫描公开及私密关注画师的新作品。"""
    try:
        auth_manager.get_access_token()
        if auth_manager.api is None:
            raise AuthError("Pixiv 登录状态不可用")
        result = FollowLatestScanner(
            api=auth_manager.api,
            database=database_manager,
        ).fetch_follow_latest()
    except AuthError:
        return jsonify({
            "success": False,
            "error": "Pixiv 登录失败，请重新配置 Token。",
        }), 401
    except FollowLatestError:
        return jsonify({
            "success": False,
            "error": "关注画师列表不可用，请重新登录后重试。",
        }), 502
    except Exception:
        return jsonify({
            "success": False,
            "error": "关注新作检查失败，请检查网络和代理设置。",
        }), 502
    return jsonify({"success": True, "result": result})


@app.route('/api/follow/download', methods=['POST'])
def create_follow_download_task():
    """把前端选中的新作品创建为一个待执行任务。"""
    data = request.get_json(silent=True)
    raw_ids = data.get("artwork_ids") if isinstance(data, dict) else None
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or len(raw_ids) > 10000
        or any(type(value) is not int or value <= 0 for value in raw_ids)
    ):
        return jsonify({
            "success": False,
            "error": "作品 ID 列表必须包含一至 10000 个正整数。",
        }), 400

    artwork_ids = list(dict.fromkeys(raw_ids))
    task_id = database_manager.create_task(
        task_type="follow_latest",
        query={"artwork_ids": artwork_ids},
        total_count=len(artwork_ids),
    )
    return jsonify({
        "success": True,
        "task_id": task_id,
        "selected_count": len(artwork_ids),
    })


@app.route('/api/stats', methods=['GET'])
def get_download_stats():
    """返回下载历史统计。"""
    time_range = request.args.get("range", "all")
    if time_range not in {"7", "30", "all"}:
        return jsonify({
            "success": False,
            "error": "时间范围必须是 7、30 或 all。",
        }), 400
    return jsonify({
        "success": True,
        "stats": database_manager.get_download_stats(time_range),
    })


@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """获取任务列表"""
    return jsonify({"tasks": [
        _public_task(task) for task in task_manager.list_tasks()
    ]})


@app.route('/api/tasks/<int:task_id>', methods=['GET'])
def get_task_detail(task_id):
    """返回任务计数与失败作品明细。"""
    task = database_manager.get_task(task_id)
    if task is None:
        return jsonify({"success": False, "error": "任务不存在。"}), 404
    return jsonify({
        "success": True,
        "task": _public_task(task),
        "failed_artworks": _public_task_failures(
            task,
            database_manager.list_task_failures(task_id),
        ),
    })


@app.route('/api/tasks/<int:task_id>/retry-failed', methods=['POST'])
def retry_failed_task(task_id):
    """创建仅包含指定任务失败作品的新任务。"""
    task = database_manager.get_task(task_id)
    if task is None:
        return jsonify({"success": False, "error": "任务不存在。"}), 404
    if task["status"] not in {"completed", "failed"}:
        return jsonify({
            "success": False,
            "error": "只能重试已结束任务的失败作品。",
        }), 409
    artwork_ids = database_manager.failed_artwork_ids(task_id)
    if not artwork_ids:
        return jsonify({"success": False, "error": "该任务没有失败作品。"}), 409
    retry_task_id, created = task_manager.create_retry_task(
        source_task_id=task_id,
        artwork_ids=artwork_ids,
    )
    if not created:
        return jsonify({
            "success": False,
            "error": "该任务已有失败项重试任务正在处理。",
            "existing_task_id": retry_task_id,
        }), 409
    return jsonify({
        "success": True,
        "task_id": retry_task_id,
        "selected_count": len(artwork_ids),
        "status": "pending",
    }), 201

@app.route('/api/bookmarks/tags', methods=['GET'])
def get_bookmark_tags():
    """返回当前登录用户指定公开范围的收藏标签。"""
    visibility = request.args.get("visibility", "public")
    try:
        query = normalize_bookmarks_query({"visibility": visibility})
    except BookmarksValidationError:
        return jsonify({"success": False, "error": "收藏类型无效。"}), 400
    if query["visibility"] == "private" and not secrets.compare_digest(
        request.headers.get("X-App-Token", ""),
        SECURITY_TOKEN,
    ):
        return jsonify({"success": False, "error": "无权读取私密收藏标签。"}), 403
    try:
        user_id = auth_manager.get_user_id()
        downloader = ArtistDownloader(
            auth_manager.api,
            database_manager,
            proxy_manager,
            output_dir=RUNTIME_PATHS.application_dir,
            user_id=user_id,
        )
        tags = downloader.fetch_bookmark_tags(query["visibility"])
    except AuthError:
        return jsonify({"success": False, "error": "请先完成 Pixiv 登录。"}), 401
    except Exception:
        return jsonify({"success": False, "error": "收藏标签获取失败。"}), 502
    return jsonify({
        "success": True,
        "visibility": query["visibility"],
        "tags": tags,
    })


@app.route('/api/tasks', methods=['POST'])
def create_task():
    """创建任务"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "画师任务必须包含正整数 artist_id。",
        }), 400

    task_type = data.get("task_type")
    query = data.get("query")
    if task_type == "artist":
        artist_id = query.get("artist_id") if isinstance(query, dict) else None
        if type(artist_id) is not int or artist_id <= 0:
            return jsonify({
                "success": False,
                "error": "画师任务必须包含正整数 artist_id。",
            }), 400
        normalized_query = {"artist_id": artist_id}
    elif task_type == "search":
        try:
            normalized_query = normalize_search_query(query)
            filters = normalize_filters(data.get("filters"))
        except (SearchValidationError, FilterValidationError):
            return jsonify({
                "success": False,
                "error": "搜索条件或筛选条件无效。",
            }), 400
        task_id = task_manager.create_task(
            task_type="search",
            query=normalized_query,
            filters=filters,
        )
        return jsonify({
            "success": True,
            "task_id": task_id,
            "status": "pending",
        }), 201
    elif task_type == "ranking":
        try:
            normalized_query = normalize_ranking_query(query)
            filters = normalize_filters(data.get("filters"))
        except (RankingValidationError, FilterValidationError):
            return jsonify({
                "success": False,
                "error": "排行榜条件或筛选条件无效。",
            }), 400
        task_id = task_manager.create_task(
            task_type="ranking",
            query=normalized_query,
            filters=filters,
        )
        return jsonify({
            "success": True,
            "task_id": task_id,
            "status": "pending",
        }), 201
    elif task_type == "bookmarks":
        try:
            normalized_query = normalize_bookmarks_query(query)
            filters = normalize_filters(data.get("filters"))
        except (BookmarksValidationError, FilterValidationError):
            return jsonify({
                "success": False,
                "error": "收藏条件或筛选条件无效。",
            }), 400
        task_id = task_manager.create_task(
            task_type="bookmarks",
            query=normalized_query,
            filters=filters,
        )
        return jsonify({
            "success": True,
            "task_id": task_id,
            "status": "pending",
        }), 201
    else:
        return jsonify({
            "success": False,
            "error": "画师任务必须包含正整数 artist_id。",
        }), 400
    try:
        filters = normalize_filters(data.get("filters"))
    except FilterValidationError:
        return jsonify({
            "success": False,
            "error": "筛选条件无效。",
        }), 400
    task_id = task_manager.create_task(
        task_type="artist",
        query=normalized_query,
        filters=filters,
    )
    return jsonify({"success": True, "task_id": task_id, "status": "pending"}), 201


def _run_task_action(target, task_id):
    """隔离后台异常，避免认证或代理细节泄露到控制台。"""
    try:
        target(task_id)
    except Exception:
        return


@app.route('/api/tasks/<int:task_id>/<action>', methods=['POST'])
def control_task(task_id, action):
    """开始、暂停、恢复或取消持久化任务。"""
    if action in {"start", "resume"}:
        target = task_manager.start_task if action == "start" else task_manager.resume_task
        socketio.start_background_task(_run_task_action, target, task_id)
        return jsonify({"success": True, "status": "accepted"}), 202
    target = {
        "pause": task_manager.pause_task,
        "cancel": task_manager.cancel_task,
    }.get(action)
    if target is None:
        return jsonify({"success": False, "error": "未知任务操作。"}), 404
    try:
        task = target(task_id)
    except TaskManagerError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, "task": _public_task(task)})

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
    global startup_connectivity, startup_ffmpeg, tray_icon
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
        print('无法连接到Pixiv，请在设置页面配置代理后重试。')

    ugoira_config = config_manager.load_config().get("ugoira", {})
    startup_ffmpeg = check_ffmpeg(str(ugoira_config.get("ffmpeg_path", "auto")))
    print(startup_ffmpeg.message)

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
