from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.task_manager import TaskManager
from app.database import Database
from app.providers import ALL_PROVIDERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

db = Database(settings.get_db_path())
task_manager = TaskManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    task_manager.set_db(db)

    for provider_cls in ALL_PROVIDERS:
        provider = provider_cls()
        task_manager.register_provider(provider)

        config = await db.get_provider_config(provider.name)
        cookie = config.get("cookie", "")
        if provider.name == "weibo" and not cookie:
            cookie = settings.weibo_cookie
        if cookie and hasattr(provider, "set_cookie"):
            provider.set_cookie(cookie)

    yield

    await task_manager.close()
    await db.close()


app = FastAPI(
    title="MediaCollector",
    description="可扩展的媒体批量下载工具",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


from app.api.tasks import router as tasks_router
from app.api.providers import router as providers_router

app.include_router(tasks_router)
app.include_router(providers_router)

# 静态文件挂载必须在所有 API 路由之后，否则会拦截 /api 请求
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
