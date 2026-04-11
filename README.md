# MediaCollector

可扩展的媒体批量下载工具，支持从微博、抖音等多个平台批量下载壁纸/图片/视频。

## 功能

- **微博图片下载** — 输入用户主页 URL，自动抓取所有原图并批量下载
- **抖音收藏/点赞下载** — 下载抖音收藏夹、收藏、点赞中的视频和图片（无水印）
- **收藏夹分类下载** — 支持按抖音收藏夹分别下载到对应本地文件夹
- **可扩展架构** — Provider 插件机制，轻松添加新平台
- **Web UI** — 美观的浏览器管理界面，创建任务、查看进度、管理配置
- **浏览器引擎** — 使用 Playwright 模拟真实浏览器，自动处理验证码和登录
- **异步高性能** — 基于 aiohttp 的异步并发下载引擎，自动重试失败文件
- **失败重试** — 下载失败的文件可一键重试，自动重新获取最新链接

## 快速开始

### 1. 安装依赖

```bash
cd MediaCollector
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate   # Linux/Mac

cd backend
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. 启动后端

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> **注意**：Windows 上不要使用 `--reload` 参数，否则 Playwright 可能无法正常工作。

### 3. 访问 Web UI

打开浏览器访问 http://localhost:8000

### 4. 配置微博

#### 方式一：自动登录（推荐）

不需要手动配置 Cookie。首次创建微博任务时，如果需要登录或触发验证码，程序会**自动弹出浏览器窗口**，在其中完成登录/验证即可。登录状态会自动保存到 `backend/data/weibo_browser/`。

#### 方式二：手动配置 Cookie

1. 浏览器登录 https://m.weibo.cn
2. F12 打开开发者工具 → Network → 刷新页面
3. 点击任意请求，复制 Headers 中的 Cookie 值
4. 在 Web UI 的「设置」页面粘贴保存

### 5. 处理微博验证码

微博有反爬机制，短时间内请求过多会触发验证码。如果遇到验证码错误：

1. 在浏览器中访问 https://m.weibo.cn/captcha/show?backUrl=https%3A%2F%2Fm.weibo.cn%2F
2. 完成验证码验证
3. 回到 MediaCollector 重新创建任务

程序也会在检测到验证码时自动弹出浏览器窗口让你操作。

### 6. 创建下载任务

#### 微博

在「新建任务」页面：
- 选择「微博」
- 输入用户主页 URL（如 `https://weibo.com/u/6279793937`）
- 设置页数范围（可选）
- 点击「创建并立即开始」

#### 抖音

在「新建任务」页面：
- 选择「抖音」
- 从下拉菜单选择「我的收藏（全部）」或「我的点赞」
- 或点击「加载收藏夹列表」获取所有收藏夹，选择特定收藏夹下载
- 首次使用会弹出浏览器窗口，需要扫码登录抖音（登录状态会自动保存）
- 点击「创建并立即开始」

> **注意**：抖音视频默认下载无水印版本。图片 CDN 需要有效 Cookie，程序会自动从浏览器提取。

### 7. 下载失败处理

如果部分文件下载失败（如 CDN 链接过期返回 403）：

1. 任务完成后会在下载目录生成 `_下载失败列表.txt` 和 `_failed.json`，记录失败文件和原因
2. 任务面板中可点击「失败详情」查看完整列表
3. 点击「重试失败」按钮，程序会**重新打开浏览器获取最新的下载链接**，然后重新下载失败的文件
4. 成功下载的文件会自动从失败列表中移除
5. 每次下载自带 3 次自动重试（网络波动时会自动重试，403/404 直接跳过）

## 技术栈

- **后端**: Python / FastAPI / Playwright / aiohttp / SQLite
- **前端**: Vue 3 / Element Plus / Vite

## 项目结构

```
MediaCollector/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── config.py         # 配置
│   │   ├── models.py         # 数据模型
│   │   ├── database.py       # SQLite 数据库
│   │   ├── core/             # 核心引擎
│   │   │   ├── base_provider.py  # Provider 抽象基类
│   │   │   ├── downloader.py     # 异步下载引擎
│   │   │   └── task_manager.py   # 任务管理器
│   │   ├── providers/        # 平台 Provider
│   │   │   ├── weibo.py      # 微博（Playwright 浏览器引擎）
│   │   │   └── douyin.py     # 抖音（Playwright + 网络拦截）
│   │   └── api/              # API 路由
│   ├── data/                 # 运行时数据
│   │   ├── weibo_browser/    # Playwright 微博浏览器持久化数据
│   │   └── douyin_browser/   # Playwright 抖音浏览器持久化数据
│   └── requirements.txt
├── frontend/                 # Vue 3 前端
└── README.md
```

## 添加新平台

在 `backend/app/providers/` 下新建文件，继承 `BaseProvider` 并实现三个方法：

```python
class MyProvider(BaseProvider):
    name = "my_platform"
    display_name = "我的平台"

    async def parse_target(self, url_or_id: str) -> TargetInfo: ...
    async def fetch_media_list(self, target, ...) -> list[MediaItem]: ...
    def get_config_schema(self) -> dict: ...
```

然后在 `providers/__init__.py` 中注册即可。
