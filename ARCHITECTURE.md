# MediaCollector 技术架构

## 应用入口

- `start.bat` 使用根目录的 `.venv/Scripts/python.exe` 在 `127.0.0.1:8000` 运行 Uvicorn。健康检查成功后打开本地页面。
- FastAPI 在 API 路由之后挂载 `frontend/dist/`。开发时 Vite 将 `/api` 代理到同一个后端。
- Vue 工作台默认显示抖音页签。两个页签保持挂载，切换时保留各自的表单、选择状态和当前下载。

## 工作台组件

| 组件 | 职责 |
|---|---|
| `App.vue` | 抖音／微博页签与键盘切换 |
| `views/DouyinView.vue` | 收藏来源、作品列表、搜索筛选、勾选和下载 |
| `views/WeiboView.vue` | 主页输入、页数范围和下载 |
| `components/SaveLocation.vue` | 顶部路径栏，左侧打开系统文件夹选择窗口，右侧复制路径 |
| `components/PlatformLogin.vue` | 打开平台登录窗口；点击抖音头像查看账号和退出登录 |
| `components/DownloadStatus.vue`、`useDownload.js` | 当前下载进度、失败详情、重试及页面刷新后的恢复 |

前端只在下载进行中轮询对应的下载记录，浏览器本地存储各平台的最近下载 ID，以及抖音上次读取的收藏夹列表。

## 平台读取

### 抖音

1. `DouyinProvider` 在线程池中串行运行 Playwright。登录按钮打开抖音窗口；读取作品时优先在后台复用登录状态，需要时显示浏览器窗口。浏览器资料保存在 `backend/data/douyin_browser/`。
2. 浏览器先点开收藏夹标签，再从收藏夹列表定位目标卡片。同名导航标签不参与匹配；作品读取从点击目标卡片开始，按收藏夹 ID 筛选响应。滚动读取在平台标记到末页、连续无新响应或达到滚动上限时结束。
3. 作品按 ID 去重，展示数据按来源写入 SQLite 的 `douyin_catalog`。打开来源时优先读取本地记录，手动刷新才访问抖音。返回收藏中止当前请求，后端停止相应浏览器读取。
4. 下载提交来源和所选作品 ID。后端复用近期的下载链接，链接未缓存或已过期时，在下载阶段重新获取。下载链接不写入作品目录缓存；重试重新获取失败项的链接。

### 微博

`WeiboProvider` 先校验用户 ID，再在后台查询昵称与主页列表。读取由单线程执行器中的 Playwright 完成，浏览器资料保存在 `backend/data/weibo_browser/`。登录按钮打开由[微博登录状态接口](https://m.weibo.cn/api/config)返回的入口；下载按所选页码范围收集原图，平台要求验证时显示浏览器窗口。

## 下载与存储

- `TaskManager` 管理后台下载，`AsyncDownloader` 使用 aiohttp 并发写文件。已存在的同名文件复用；抖音文件名包含作品 ID，图集文件附带序号。
- 下载目录由本机用户选择，项目不预设路径。抖音优先使用当前来源选中的目录，否则使用抖音页签选中的目录。文件直接写入所选文件夹。
- `folder_picker.py` 在独立进程中打开 Windows 文件夹选择窗口。后端校验并保存确认的目录，取消时保留原设置。
- SQLite 的 `tasks` 保存目标、目录、下载状态、计数和消息；`task_failures` 按下载 ID 保存失败文件和原因；`provider_configs` 保存平台目录及抖音按来源配置的目录。新下载读取当前目录，失败重试保留原下载目录。
- 重试按原下载的失败文件 ID 匹配新链接，保留仍未取得链接的失败项。后端重启时把仍标记为运行中的下载标为中断。
- 登录状态保留在平台各自的浏览器资料中。登录接口只返回登录结果。
- 抖音首页在后台从已有浏览器会话读取本人资料，返回登录状态、昵称、抖音号和头像地址。点击头像显示账号；退出时清除下载器专用会话及收藏缓存，保留目录配置和下载记录。
- 保存目录由独立的本地接口读取，与作品抓取互不等待。

## HTTP 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 本地服务健康检查 |
| POST | `/api/providers/{name}/login` | 打开平台登录窗口并等待登录结果 |
| GET | `/api/providers/douyin/session` | 检查现有抖音会话并读取账号资料 |
| POST | `/api/providers/douyin/logout` | 退出下载器内的抖音会话 |
| GET | `/api/providers/{name}/destination` | 读取平台或抖音来源的保存目录 |
| POST | `/api/providers/{name}/destination` | 打开系统选择窗口并保存所选目录，抖音可指定来源 |
| GET | `/api/providers/douyin/collects` | 读取抖音收藏夹 |
| GET | `/api/providers/douyin/works?source=…` | 读取来源的作品，`refresh=true` 同步平台数据 |
| POST | `/api/providers/douyin/downloads` | 提交 `source`、`work_ids`，启动选中作品的下载 |
| POST | `/api/tasks` | 校验微博主页或 ID 并启动下载 |
| GET | `/api/tasks/{id}` | 查询下载进度与结果 |
| GET | `/api/tasks/{id}/failed` | 查看该下载的失败文件 |
| POST | `/api/tasks/{id}/retry-failed` | 后台重试该下载的失败项 |

## 配置与验证

后端从 `backend/.env` 读取配置：

- `MAX_CONCURRENT_DOWNLOADS`：每批下载的并发数。
- `DB_PATH`：SQLite 路径，默认 `backend/data/media_collector.db`。Git 忽略 `backend/data/`。

`backend/tests/workspace_smoke.py` 使用真实 FastAPI、前端构建和下载引擎，配合本地平台样例验证收藏夹浏览、筛选、选中项落盘、重试、微博页签和状态恢复。
