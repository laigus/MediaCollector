# MediaCollector 前端

Vue 3 工作台包含抖音与微博两个页签。各平台视图位于 `src/views/`，保存目录、登录设置和下载状态组件位于 `src/components/`。

## 开发

启动后端后，在 `frontend/` 运行：

```powershell
pnpm install
pnpm dev
```

访问 Vite 打印的地址。开发服务器将 `/api` 代理到 `127.0.0.1:8000`。

## 构建

```powershell
pnpm build
```

`dist/` 是一键启动时由 FastAPI 提供的页面。更新界面时同步提交 `dist/`。端到端验证方式见[项目 README](../README.md)。
