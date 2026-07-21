# PRD: Pixiv 批量下载工具

## Problem Statement

用户需要从 Pixiv 批量下载大量图片作品，但目前缺乏一个功能完善、易于使用的工具。现有痛点包括：

- **手动下载效率低**：逐个点击保存，耗时且容易遗漏
- **缺乏筛选能力**：无法按收藏数、标签、时间等条件批量筛选
- **下载管理困难**：无法暂停/恢复，网络中断后需要重新开始
- **文件组织混乱**：下载的文件命名不规范，难以分类管理
- **无法追踪历史**：不知道哪些作品已下载，容易重复下载
- **动图格式不通用**：Pixiv 动图（Ugoira）是特殊格式，需要专用播放器

用户需要一个自动化工具来解决这些问题，提高下载效率和管理便利性。

## Solution

开发一个基于 Web 界面的 Pixiv 批量下载工具，提供：

- **多种内容来源**：支持画师作品集、搜索结果、排行榜、书签/收藏、关注画师新作
- **灵活的筛选条件**：按作品类型、分级、收藏数、时间、分辨率、标签筛选
- **完整的任务管理**：支持任务队列、暂停/恢复/取消、断点续传
- **智能文件组织**：按任务类型自动分类，多图作品创建子文件夹，保存完整元数据
- **实时进度反馈**：WebSocket 实时推送下载进度、速度、统计信息
- **动图格式转换**：支持保留原始 ZIP、转换为 GIF、转换为 MP4（可叠加选择）
- **易于使用**：Web 界面操作直观，首次启动引导配置，打包成单文件可执行程序

## User Stories

1. 作为 Pixiv 用户，我想要输入画师 ID 批量下载该画师的所有作品，以便收藏喜欢的画师的完整作品集
2. 作为 Pixiv 用户，我想要搜索关键词并按收藏数筛选，以便只下载高质量的作品
3. 作为 Pixiv 用户，我想要下载每日排行榜前 100 名的作品，以便及时获取热门内容
4. 作为 Pixiv 用户，我想要批量下载我的收藏夹内容，以便本地备份我收藏的所有作品
5. 作为 Pixiv 用户，我想要检查关注的画师是否有新作品，以便不错过喜欢的画师的更新
6. 作为 Pixiv 用户，我想要设置收藏数阈值（如 ≥500），以便过滤掉低质量作品
7. 作为 Pixiv 用户，我想要排除 R-18 内容，以便下载适合公开场合查看的作品
8. 作为 Pixiv 用户，我想要只下载插画类型作品，以便排除漫画和动图
9. 作为 Pixiv 用户，我想要按发布时间筛选（如最近一年），以便获取最新的作品
10. 作为 Pixiv 用户，我想要设置最小分辨率要求，以便只下载高清图片
11. 作为 Pixiv 用户，我想要包含/排除特定标签，以便精确控制下载内容
12. 作为 Pixiv 用户，我想要在下载过程中暂停任务，以便节省带宽用于其他活动
13. 作为 Pixiv 用户，我想要恢复暂停的任务，以便从中断的地方继续下载
14. 作为 Pixiv 用户，我想要取消正在执行的任务，以便停止不再需要的下载
15. 作为 Pixiv 用户，我想要查看任务队列，以便管理多个待执行的下载任务
16. 作为 Pixiv 用户，我想要看到实时的下载进度条，以便了解任务完成情况
17. 作为 Pixiv 用户，我想要看到当前下载速度和预计剩余时间，以便评估等待时长
18. 作为 Pixiv 用户，我想要看到已下载/跳过/失败的作品统计，以便了解任务执行结果
19. 作为 Pixiv 用户，我想要下载的文件自动按画师分类存储，以便后续按画师浏览
20. 作为 Pixiv 用户，我想要多图作品自动创建子文件夹，以便整体查看同一作品的所有图片
21. 作为 Pixiv 用户，我想要每个作品保存元数据 JSON 文件，以便在本地也能搜索标题、标签等信息
22. 作为 Pixiv 用户，我想要自定义文件命名规则，以便生成符合我习惯的文件名
23. 作为 Pixiv 用户，我想要工具自动去重，以便避免重复下载已有的作品
24. 作为 Pixiv 用户，我想要将 Pixiv 动图转换为 GIF，以便在任何设备上查看
25. 作为 Pixiv 用户，我想要将 Pixiv 动图转换为 MP4，以便获得高质量且文件小的视频
26. 作为 Pixiv 用户，我想要同时保留动图的原始 ZIP 和转换后的格式，以便既能备份原始文件又能方便预览
27. 作为 Pixiv 用户，我想要配置网络代理，以便在网络受限的地区访问 Pixiv
28. 作为 Pixiv 用户，我想要调整并发下载数，以便在速度和稳定性之间平衡
29. 作为 Pixiv 用户，我想要失败的下载自动重试，以便应对临时的网络问题
30. 作为 Pixiv 用户，我想要看到详细的错误日志，以便排查下载失败的原因
31. 作为 Pixiv 用户，我想要对失败的作品单独重试，以便不重新下载已成功的部分
32. 作为 Pixiv 用户，我想要首次使用时有引导向导，以便快速完成配置
33. 作为 Pixiv 用户，我想要工具自动测试 Pixiv 连接，以便知道认证是否成功
34. 作为 Pixiv 用户，我想要工具检测 ffmpeg 是否安装，以便知道动图转换功能是否可用
35. 作为 Pixiv 用户，我想要下载完成后一键打包成 ZIP，以便方便分享或备份
36. 作为 Pixiv 用户，我想要查看历史下载统计，以便了解自己的下载偏好和趋势
37. 作为 Pixiv 用户，我想要工具打包成单个可执行文件，以便无需安装 Python 环境
38. 作为 Pixiv 用户，我想要双击启动后自动打开浏览器界面，以便快速开始使用
39. 作为 Pixiv 用户，我想要配置文件保存在工具目录，以便整个工具便携使用
40. 作为 Pixiv 用户，我想要查看当前正在下载的文件列表和每个文件的进度，以便了解下载细节

## Implementation Decisions

### 技术栈

**后端框架：Flask + Flask-SocketIO**
- Flask 作为 Web 服务器，处理 HTTP 请求
- Flask-SocketIO 提供 WebSocket 实时通信，推送下载进度
- 后台线程运行 asyncio 事件循环，管理异步下载任务

**前端技术：Bootstrap 5 + Vanilla JavaScript**
- Bootstrap 5 提供现代化 UI 组件
- Socket.IO Client 处理实时通信
- 不引入 React/Vue，保持简单

**认证：Refresh Token + pixivpy**
- 使用 pixivpy 库与 Pixiv API 交互
- 用户配置 Refresh Token，工具自动刷新 Access Token
- Token 使用 cryptography 库加密存储

**并发下载：asyncio + aiohttp**
- asyncio.Semaphore 控制并发数（默认 5-10）
- aiohttp 进行异步 HTTP 请求
- 智能限流：检测 HTTP 429 时自动降速
- 重试机制：1s → 3s → 5s 指数退避

**数据存储：SQLite + YAML**
- SQLite 存储任务历史、下载记录（用于去重和断点续传）
- YAML 存储用户配置（Token、路径、偏好设置）
- 所有数据保存在工具目录（便携模式）

**打包分发：PyInstaller**
- 打包成单文件可执行程序
- 启动时自动启动 Flask 服务器
- 自动打开浏览器，URL 包含随机 token 防止外部访问

### 核心模块

**1. 认证模块 (auth)**
- 接口：`login(refresh_token)`, `get_access_token()`, `validate_token()`
- 负责 Token 管理、加密存储、自动刷新

**2. 任务管理器 (task_manager)**
- 接口：`create_task()`, `start_task()`, `pause_task()`, `resume_task()`, `cancel_task()`
- 维护任务队列和状态机
- 一次只执行一个任务

**3. 内容获取器 (content_fetcher)**
- 接口：`fetch_artist_artworks()`, `fetch_search_results()`, `fetch_ranking()`, `fetch_bookmarks()`, `fetch_follow_latest()`
- 根据任务类型和筛选条件获取作品列表
- 处理分页和 API 限流

**4. 下载引擎 (download_engine)**
- 接口：`download_artwork()`, `download_file()`, `save_metadata()`
- 管理 asyncio 事件循环和并发下载
- 实现重试逻辑和错误处理
- 调用文件组织模块保存文件

**5. 文件组织模块 (file_organizer)**
- 接口：`get_save_path()`, `create_directory_structure()`, `apply_naming_template()`
- 根据任务类型和配置生成目标路径
- 处理文件名特殊字符和长度限制
- 多图作品创建子文件夹

**6. 动图处理模块 (ugoira_processor)**
- 接口：`download_ugoira_zip()`, `convert_to_gif()`, `convert_to_mp4()`
- 下载 Pixiv 动图 ZIP 包
- 解析帧序列和延迟信息
- 调用 ffmpeg 转换格式

**7. 去重模块 (deduplication)**
- 接口：`is_downloaded()`, `mark_as_downloaded()`, `get_download_history()`
- 查询 SQLite 数据库判断作品是否已下载
- 记录下载历史

**8. 配置管理 (config_manager)**
- 接口：`load_config()`, `save_config()`, `get_setting()`, `update_setting()`
- 读写 YAML 配置文件
- 提供默认值

**9. Flask 路由层**
- 端点：`POST /api/tasks`, `GET /api/tasks`, `POST /api/tasks/{id}/pause`, `GET /api/config`, `POST /api/config`
- 处理前端请求，调用任务管理器和配置管理模块
- WebSocket 事件：`progress_update`, `task_complete`, `error`

### 数据库 Schema

**tasks 表**
```sql
CREATE TABLE tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    query TEXT,
    filters TEXT,
    status TEXT NOT NULL,
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_time TIMESTAMP,
    completed_time TIMESTAMP,
    total_count INTEGER,
    downloaded_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0
);
```

**downloads 表**
```sql
CREATE TABLE downloads (
    artwork_id INTEGER PRIMARY KEY,
    artist_id INTEGER,
    artist_name TEXT,
    title TEXT,
    download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task_id INTEGER,
    file_path TEXT,
    page_count INTEGER,
    bookmarks INTEGER,
    tags TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
```

**followed_artists 表**
```sql
CREATE TABLE followed_artists (
    artist_id INTEGER PRIMARY KEY,
    artist_name TEXT,
    last_check_time TIMESTAMP,
    last_artwork_id INTEGER
);
```

### API 契约

**创建任务示例**
```
POST /api/tasks
{
  "task_type": "artist",
  "query": "12345",
  "filters": {
    "work_types": ["illust", "manga"],
    "r18": false,
    "min_bookmarks": 500
  }
}
```

### 文件组织结构

```
downloads/
├── artists/{画师名}_{画师ID}/{作品ID}_{标题}/
├── search/{关键词}_{YYYY-MM-DD}/
├── ranking/{榜单类型}_{YYYY-MM-DD}/
└── bookmarks/{YYYY-MM-DD}/
```

### 配置文件格式

**config.yaml**
```yaml
pixiv:
  refresh_token: "encrypted_token_here"

download:
  output_dir: "./downloads"
  max_concurrent: 5
  retry_times: 3
  retry_delays: [1, 3, 5]

naming:
  template: "{作品ID}_{画师名}_{作品标题}_p{页码}"
  max_filename_length: 200

ugoira:
  download_original: true
  convert_to_gif: false
  convert_to_mp4: true

proxy:
  enabled: false
  type: "socks5"
  host: "127.0.0.1"
  port: 1080
```

### 状态机

**任务状态转换**
```
pending → running → completed
          ↓         ↑
          paused ───┘
          ↓
          cancelled/failed
```

### 错误处理策略

**可恢复错误（自动重试）**
- 网络超时、HTTP 429/500/502/503
- 重试：1s → 3s → 5s

**跳过错误（记录日志）**
- 单个作品 404
- 文件名非法字符

**致命错误（停止任务）**
- Token 失效
- 磁盘空间不足

## Testing Decisions

### 测试原则
只测试外部行为，不测试实现细节。测试通过公共接口进行，对重构具有弹性。

### 主要测试边界

**1. HTTP API 层测试（端到端）**
- 使用 Flask test client
- Mock Pixiv API 和文件系统
- 测试所有路由端点和状态转换

**2. 任务管理器单元测试**
- 测试任务队列和状态机
- 测试并发控制
- 不依赖 Flask 层

**3. 文件组织模块单元测试**
- 测试命名模板和特殊字符处理
- 使用临时目录

**4. 去重模块单元测试**
- 使用内存数据库
- 测试去重逻辑

**5. 动图处理模块集成测试**
- Mock ffmpeg 调用
- 测试格式转换逻辑

### 测试技术
- Mock：`unittest.mock` 或 `responses`
- 临时目录：`tempfile.TemporaryDirectory`
- 测试数据：准备作品 JSON 样本

### 测试覆盖目标
- 核心功能：100%
- HTTP API：100%
- 边缘情况：错误处理、限流、重试

## Out of Scope

### 第一版不包含
- 搜索高级选项（AND/OR、自定义日期、AI 过滤）
- 自动监控和通知
- 多任务并行
- 远程访问
- 云存储集成
- 浏览器扩展
- 移动端适配

### 不会实现
- 违反 Pixiv 服务条款的功能
- 社交功能（评论、点赞）

## Further Notes

### 开发路线

**Phase 1: MVP**
- 基础界面和引导
- 画师作品下载
- 任务管理
- 配置和数据库
- 打包

**Phase 2: 增强**
- 搜索、排行榜、书签
- 关注画师新作（手动）
- 动图处理
- 代理支持

**Phase 3: 高级**
- 搜索高级选项
- 自动监控
- 统计分析

### 技术风险
- Pixiv API 变更 → 使用 pixivpy，社区维护
- IP 封禁 → 智能限流 + 代理支持
- ffmpeg 依赖 → 检测 + 指引或打包

### 性能
- 并发：5-10（可配置）
- 内存：100-200 MB
- 打包：30-50 MB（不含 ffmpeg）

### 安全
- Token 加密存储
- 仅 localhost 访问
- 路径验证

### 文档
- README + 获取 Token 教程
- FAQ + 故障排查

### 许可证
- MIT 或其他开源许可
- 仅供个人学习和备份
