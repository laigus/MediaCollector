# MediaCollector 技术架构文档

> 面向开发者 / AI 的项目全貌文档，帮助快速理解系统设计并继续开发。

## 一、项目概述

MediaCollector 是一个可扩展的媒体批量下载工具。用户通过 Web UI 创建下载任务（如输入微博用户主页 URL），后端自动抓取媒体列表并批量下载到本地。

**核心设计目标**：
- 平台可扩展：通过 Provider 插件机制支持新平台（微博、抖音、B站等）
- 反爬对抗：微博 Provider 使用 Playwright 浏览器引擎，绕过 TLS 指纹检测和验证码
- 增量下载：记录已下载项，避免重复
- 异步高性能：aiohttp 并发下载 + asyncio 任务调度

## 二、技术栈

| 层 | 技术 | 版本要求 |
|---|---|---|
| 后端框架 | FastAPI + Uvicorn | Python 3.10+ |
| 浏览器引擎 | Playwright (sync API + ThreadPoolExecutor) | ≥1.40 |
| HTTP 下载 | aiohttp | ≥3.9 |
| 数据库 | SQLite (aiosqlite) | — |
| 配置管理 | pydantic-settings + .env | — |
| 前端框架 | Vue 3 + Vite | Node 18+ |
| UI 组件库 | Element Plus | — |
| 路由 | Vue Router (Hash mode) | — |
| HTTP 客户端 | Axios | — |

## 三、目录结构

```
MediaCollector/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI 入口、生命周期、路由注册
│   │   ├── config.py            # pydantic-settings 配置类
│   │   ├── models.py            # Pydantic 数据模型与枚举
│   │   ├── database.py          # SQLite 数据层（aiosqlite）
│   │   ├── core/
│   │   │   ├── base_provider.py # Provider 抽象基类
│   │   │   ├── downloader.py    # 异步批量下载引擎
│   │   │   └── task_manager.py  # 任务生命周期管理
│   │   ├── providers/
│   │   │   ├── __init__.py      # Provider 注册表
│   │   │   ├── weibo.py         # 微博 Provider（Playwright）
│   │   │   └── douyin.py        # 抖音 Provider（Playwright + 网络拦截）
│   │   └── api/
│   │       ├── tasks.py         # 任务 CRUD API
│   │       └── providers.py     # Provider 配置 + 收藏夹列表 API
│   ├── data/                    # 运行时数据（gitignore）
│   │   ├── media_collector.db   # SQLite 数据库
│   │   ├── weibo_browser/       # Playwright 微博持久化浏览器数据
│   │   └── douyin_browser/      # Playwright 抖音持久化浏览器数据
│   ├── .env                     # 环境配置
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── main.js              # Vue 入口
│   │   ├── App.vue              # 根组件（侧栏布局）
│   │   ├── router.js            # 路由定义
│   │   ├── api.js               # Axios API 封装
│   │   ├── style.css            # 全局样式
│   │   └── views/
│   │       ├── Dashboard.vue    # 任务面板
│   │       ├── NewTask.vue      # 新建任务
│   │       └── Settings.vue     # Provider 配置
│   ├── vite.config.js           # Vite 配置（含 /api 代理）
│   ├── index.html
│   └── dist/                    # 构建产物（由后端 StaticFiles 托管）
└── README.md
```

## 四、架构设计

### 4.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    Web Browser                       │
│  Vue 3 SPA (Element Plus)                           │
│  Dashboard | NewTask | Settings                     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP (Axios)
                       ▼
┌─────────────────────────────────────────────────────┐
│                 FastAPI Backend                       │
│                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ API 路由  │→│ TaskManager  │→│ BaseProvider   │  │
│  │ /api/*   │  │ 任务调度      │  │ (抽象基类)     │  │
│  └──────────┘  └──────┬───────┘  └───────┬───────┘  │
│                       │                   │          │
│                       ▼                   ▼          │
│              ┌────────────────┐  ┌────────────────┐  │
│              │ AsyncDownloader│  │ WeiboProvider  │  │
│              │ (aiohttp)     │  │ DouyinProvider │  │
│              └────────────────┘  │ (Playwright)   │  │
│                       │          └────────────────┘  │
│                       │                              │
│                       ▼                              │
│              ┌────────────────┐                      │
│              │ SQLite (async) │                      │
│              │ tasks / items  │                      │
│              └────────────────┘                      │
└─────────────────────────────────────────────────────┘
```

### 4.2 Provider 插件机制

所有平台适配器继承 `BaseProvider`，需实现以下接口：

```python
class BaseProvider(ABC):
    name: str                    # 唯一标识，如 "weibo"
    display_name: str            # 显示名，如 "微博"
    description: str             # 描述

    async def parse_target(self, url_or_id: str) -> TargetInfo
    # 解析用户输入的 URL/ID，返回目标信息（用户名、头像、container_id 等）

    async def fetch_media_list(self, target, *, max_pages, downloaded_ids, progress_callback) -> list[MediaItem]
    # 抓取媒体列表，支持分页、增量、进度回调

    def get_config_schema(self) -> dict[str, Any]
    # 返回配置 Schema（前端动态渲染表单）

    def get_download_headers(self) -> dict[str, str] | None
    # 下载文件时需要的额外 HTTP headers（如 Referer）

    async def validate_config(self, config) -> bool
    async def close(self) -> None
```

注册新 Provider 只需：
1. 在 `providers/` 下新建文件
2. 继承 `BaseProvider` 实现接口
3. 在 `providers/__init__.py` 的 `ALL_PROVIDERS` 列表中添加

### 4.3 任务执行流程

```
用户点击「创建并开始」
    │
    ▼
POST /api/tasks          → TaskManager.create_task()
    │                        → provider.parse_target(url)
    │                        → 写入 DB (status=pending)
    ▼
POST /api/tasks/{id}/start → TaskManager.start_task()
    │                        → asyncio.create_task(_run_task)
    ▼
_run_task():
    1. provider.parse_target(url)    → 获取目标信息
    2. db.get_downloaded_ids()       → 获取已下载 ID 集合
    3. provider.fetch_media_list()   → 分页抓取媒体列表（增量）
    4. downloader.download_batch()   → 并发下载文件（每个文件最多重试3次）
    5. 每下载一个 → db.add_downloaded_id() + 更新进度
    6. 失败的文件 → 保存 _failed.json + _下载失败列表.txt
    7. 完成 → db.update_task(status=completed)

retry_failed():
    1. 读取 _failed.json 获取失败文件列表
    2. provider.fetch_media_list()   → 重新获取最新 URL（CDN链接有时效）
    3. 通过 source_id 或 filename 匹配失败项的新 URL
    4. downloader.download_batch()   → 用新 URL 重新下载
    5. 成功的从失败列表移除，仍失败的更新列表文件
```

### 4.4 微博 Provider 实现细节

**核心挑战**：微博 m.weibo.cn 的 API 有严格的反爬机制：
- TLS 指纹检测：普通 HTTP 库（aiohttp/requests/curl_cffi）即使带正确 Cookie 也会被拒绝
- 验证码机制：短时间请求过多触发 captcha
- Cookie 绑定：Cookie 被标记后需要过验证码才能恢复

**解决方案**：使用 Playwright 持久化浏览器上下文

```
_sync_browser_request() ← 在 ThreadPoolExecutor 中运行
    │
    ├─ 启动 Playwright (sync API)
    ├─ launch_persistent_context(data/weibo_browser/)
    │   └─ 复用已保存的浏览器数据（Cookie、localStorage 等）
    ├─ page.goto(API_URL)
    ├─ 解析 JSON 响应
    │
    ├─ 如果 ok=-100（需要验证码/登录）：
    │   ├─ 关闭无头浏览器
    │   ├─ 重新启动有头浏览器（headless=False）
    │   ├─ 导航到验证码/登录页面
    │   ├─ 等待用户完成（最多 120 秒）
    │   ├─ 重新请求 API 验证
    │   └─ 关闭有头浏览器
    │
    └─ 返回 JSON 数据
```

**关键设计决策**：
- 使用 sync API 而非 async API：Windows 上 Playwright async API 与 uvicorn 的 ProactorEventLoop 不兼容（`NotImplementedError`）
- ThreadPoolExecutor(max_workers=1)：确保同一时间只有一个浏览器实例
- threading.Lock：防止并发请求竞争浏览器资源
- 翻页间隔 3 秒：降低触发频率限制的风险

**微博 API 端点**：
- `https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}` — 获取用户信息
- `https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid={cid}&since_id={sid}` — 分页获取微博列表
- 图片 URL 格式：`https://wx{n}.sinaimg.cn/large/{pid}.jpg`
- 下载图片需要 `Referer: https://weibo.com/` header

### 4.5 抖音 Provider 实现细节

**核心挑战**：抖音 Web API 有 `X-Bogus` / `a_bogus` 签名机制，直接调用 API 会被拒绝。

**解决方案**：Playwright 页面滚动 + 网络拦截

```
_sync_fetch_douyin_page() ← 在 ThreadPoolExecutor 中运行
    │
    ├─ 启动 Playwright (sync API, headless=False)
    ├─ launch_persistent_context(data/douyin_browser/)
    ├─ 注册 page.on("response") 拦截器
    │   └─ 根据 source_type 精确匹配目标 API：
    │       ├─ favorite → listcollection API
    │       ├─ like → aweme/favorite API
    │       └─ collects_{id} → collects/video/list?collects_id={id}
    │
    ├─ 导航到目标页面
    ├─ 检测登录状态（DOM 弹窗 + Cookie 检查）
    │   └─ 未登录 → 等待用户扫码（最多 180 秒）
    │
    ├─ 如果是收藏夹模式 → 点击进入对应收藏夹
    ├─ 滚动页面触发 API 请求
    ├─ 拦截器捕获 aweme_list 数据
    ├─ 提取浏览器 Cookie 用于后续下载
    └─ 返回 (aweme_list, has_more, cookie_str)
```

**收藏夹列表获取**：
- 导航到 `user/self?showTab=favorite_collection`
- 拦截 `aweme/v1/web/collects/list/` API 响应
- 提取 `collects_id`、`collects_name`、`total_number`

**无水印视频**：
- 从 `video.play_addr.url_list` 中选择不含 "watermark" 的 URL
- 兜底方案：将 URL 中的 "playwm" 替换为 "play"

**文件名安全处理**：
- `_sanitize_name()` 清理换行符、控制字符、emoji、特殊符号
- 只保留中文、字母、数字和常用标点，限制 80 字符
- `downloader._sanitize_filename()` 作为最后防线，限制 200 字符

## 五、数据模型

### 5.1 数据库表

**tasks** — 下载任务

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 ID |
| provider | TEXT | 平台标识 |
| target_url | TEXT | 用户输入的 URL |
| target_name | TEXT | 目标显示名 |
| save_dir | TEXT | 保存目录 |
| status | TEXT | pending/running/completed/failed |
| total_count | INTEGER | 总媒体数 |
| downloaded_count | INTEGER | 已下载数 |
| failed_count | INTEGER | 失败数 |
| start_page | INTEGER | 起始页（默认 1） |
| end_page | INTEGER | 结束页（0=不限） |
| progress_message | TEXT | 实时进度信息 |
| error_message | TEXT | 错误信息 |
| created_at | TEXT | ISO 时间 |
| updated_at | TEXT | ISO 时间 |

**downloaded_items** — 任务级已下载记录（随任务删除而清除）

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 ID |
| task_id | INTEGER | 关联任务 ID |
| source_id | TEXT | 媒体唯一标识（如 `{mid}_{pid}`） |
| downloaded_at | TEXT | ISO 时间 |
| 约束 | UNIQUE(task_id, source_id) | |

**provider_configs** — Provider 配置

| 列 | 类型 | 说明 |
|---|---|---|
| provider | TEXT PK | Provider 名 |
| config_json | TEXT | JSON 格式配置 |
| updated_at | TEXT | ISO 时间 |

### 5.2 Pydantic 模型

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"

class TargetInfo(BaseModel):
    provider: str
    target_id: str
    display_name: str = ""
    avatar_url: str = ""
    extra: dict = {}

class MediaItem(BaseModel):
    url: str
    media_type: MediaType
    filename: str = ""
    source_id: str = ""
    extra: dict = {}

class TaskCreate(BaseModel):
    provider: str
    target_url: str
    save_dir: str = ""
    start_page: int = 1
    end_page: int = 0
```

## 六、API 接口

| 方法 | 路径 | 功能 | 请求体 | 响应 |
|---|---|---|---|---|
| GET | `/api/health` | 健康检查 | — | `{status, version}` |
| POST | `/api/tasks` | 创建任务 | `TaskCreate` | `TaskInfo` |
| GET | `/api/tasks` | 任务列表 | — | `TaskInfo[]` |
| GET | `/api/tasks/{id}` | 任务详情 | — | `TaskInfo` |
| POST | `/api/tasks/{id}/start` | 启动任务 | — | `TaskInfo` |
| GET | `/api/tasks/{id}/failed` | 获取失败文件列表 | — | `{content, count}` |
| POST | `/api/tasks/{id}/retry-failed` | 重试失败文件 | — | `{retried, success, still_failed}` |
| DELETE | `/api/tasks/{id}` | 删除任务 | — | `{ok}` |
| GET | `/api/providers` | Provider 列表 | — | `ProviderInfo[]` |
| GET | `/api/providers/{name}/config` | 读取配置 | — | `{config}` (敏感字段脱敏) |
| PUT | `/api/providers/{name}/config` | 更新配置 | `{config}` | `{ok}` |
| GET | `/api/providers/{name}/collects` | 收藏夹列表（抖音） | — | `{folders: [{id, name, count}]}` |

**配置更新注意**：PUT 接口会跳过值中包含 `***` 的字段（防止前端脱敏值覆盖真实值）。

## 七、前端架构

### 7.1 路由

| 路径 | 组件 | 功能 |
|---|---|---|
| `/#/` | Dashboard.vue | 任务面板：统计卡片 + 任务表格（轮询刷新） |
| `/#/new-task` | NewTask.vue | 新建任务：选择 Provider → 输入 URL → 创建 |
| `/#/settings` | Settings.vue | 配置管理：按 Provider 动态渲染配置表单 |

### 7.2 API 封装

`api.js` 导出的方法与后端一一对应：
- `listTasks()`, `getTask(id)`, `createTask(data)`, `startTask(id)`, `deleteTask(id)`
- `getFailedList(id)` — 获取失败文件详情
- `retryFailed(id)` — 重试失败文件下载（超时 5 分钟）
- `listProviders()`, `getProviderConfig(name)`, `updateProviderConfig(name, config)`
- `getCollectsList(name)` — 获取抖音收藏夹列表（超时 5 分钟，因需要浏览器登录）
- `health()`

开发环境通过 Vite 代理 `/api` → `http://localhost:8000`；生产环境由后端 StaticFiles 托管前端 dist。

### 7.3 敏感字段处理

Settings 页面对敏感配置（如 Cookie）的处理：
1. 后端返回脱敏值（如 `XSRF-T***_w==`）
2. 前端将脱敏值存入 `maskedHints`，input 的 `v-model` 保持空字符串
3. placeholder 显示 `已配置: XSRF-T***_w==（留空保持不变，输入新值覆盖）`
4. 保存时空字段不发送，后端跳过含 `***` 的值

## 八、配置

### 8.1 环境变量（.env）

```env
DOWNLOAD_DIR=D:\LAIGUS\壁纸     # 默认下载目录
MAX_CONCURRENT_DOWNLOADS=5       # 最大并发下载数
PORT=8000                        # 服务端口
WEIBO_COOKIE=                    # 微博 Cookie（可选，推荐通过 UI 配置）
```

### 8.2 配置优先级

微博 Cookie：UI 配置（DB） > .env 文件 > 空（触发浏览器登录）

## 九、已知限制与注意事项

1. **Windows 限制**：不能使用 `uvicorn --reload`，Playwright 的子进程管理与 reload 机制冲突
2. **单线程浏览器**：微博/抖音 API 请求通过单线程 Playwright 执行，高并发场景下是瓶颈
3. **验证码**：微博频率限制严格，翻页间隔已设为 3 秒，但仍可能触发验证码
4. **微博图片下载**：下载仍使用 aiohttp（非 Playwright），需要 `Referer: https://weibo.com/` header
5. **抖音 CDN 认证**：抖音图片/视频 CDN 需要有效 Cookie（含 `tt_chain_token`），程序会自动从浏览器提取
6. **抖音登录**：首次使用需扫码登录，登录状态保存在 `data/douyin_browser/` 持久化目录中
7. **CDN 链接时效**：抖音图片/视频 URL 包含签名和过期时间，过期后返回 403。重试功能会重新获取最新 URL
8. **下载重试**：每个文件自动重试 3 次（网络波动重试，403/404 跳过）。失败文件可通过 UI 一键重试

## 十、扩展指南

### 添加新 Provider

1. 在 `backend/app/providers/` 下新建文件（如 `bilibili.py`）
2. 继承 `BaseProvider`，实现所有抽象方法
3. 在 `providers/__init__.py` 的 `ALL_PROVIDERS` 列表中添加
4. 前端无需修改 — 配置表单根据 `get_config_schema()` 自动渲染

已实现的 Provider：
- `WeiboProvider` — 微博图片下载（Playwright API 请求）
- `DouyinProvider` — 抖音收藏/点赞/收藏夹下载（Playwright 网络拦截）

### 添加新功能建议

- **任务暂停/恢复**：在 TaskManager 中实现协程取消与恢复
- **WebSocket 实时进度**：替代前端轮询
- **代理支持**：在 downloader 和 Playwright 中配置代理
- **定时任务**：APScheduler 定期检查新内容
- **预览筛选**：下载前显示缩略图，用户勾选要下载的内容
