from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from threading import Event

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.core.folder_picker import choose_folder
from app.models import DouyinDownload, DouyinSession, FolderSelection, TaskInfo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/providers", tags=["providers"])
_folder_picker_lock = asyncio.Lock()
_session_changes: set[str] = set()


def _get_tm():
    from app.main import task_manager
    return task_manager


def _get_db():
    from app.main import db
    return db


def _provider(name: str):
    provider = _get_tm().get_provider(name)
    if not provider:
        raise HTTPException(status_code=404, detail="平台不存在")
    return provider


async def _destination_target(provider_name: str, source: str | None) -> str | None:
    provider = _provider(provider_name)
    if source is None:
        return None
    if provider_name != "douyin":
        raise HTTPException(status_code=400, detail="该平台按页签设置下载文件夹")
    try:
        return (await provider.parse_target(source)).target_id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{provider_name}/destination")
async def get_destination(
    provider_name: str, source: str | None = Query(default=None, min_length=1, max_length=256),
):
    target_id = await _destination_target(provider_name, source)
    return {"download_dir": await _get_tm().destination(provider_name, target_id)}


@router.post("/{provider_name}/login")
async def login(provider_name: str, request: Request):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="请在下载工作台内打开登录窗口")
    provider = _provider(provider_name)
    if provider_name in _session_changes:
        raise HTTPException(status_code=409, detail="正在处理账号登录状态")
    _session_changes.add(provider_name)
    try:
        await provider.login()
        if provider_name == "douyin":
            await _get_db().clear_douyin_catalog()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Platform login failed: %s", provider_name)
        raise HTTPException(status_code=502, detail="登录窗口打开失败，请重试") from exc
    finally:
        _session_changes.discard(provider_name)
    return {"logged_in": True}


@router.post("/{provider_name}/destination")
async def select_destination(provider_name: str, body: FolderSelection):
    target_id = await _destination_target(provider_name, body.source)

    if _folder_picker_lock.locked():
        raise HTTPException(status_code=409, detail="文件夹选择窗口已打开")
    async with _folder_picker_lock:
        initial = await _get_tm().destination(provider_name, target_id)
        try:
            selected = await choose_folder(initial)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="选择窗口已超时，请重新打开") from exc
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Folder picker failed")
            raise HTTPException(status_code=500, detail="打开文件夹选择窗口失败") from exc
        if not selected:
            return {"download_dir": None}
        try:
            directory = Path(selected).resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("Expected a directory")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="所选文件夹已不可用，请重新选择") from exc

        path = str(directory)
        changes = {"source_download_dirs": {target_id: path}} if target_id else {"download_dir": path}
        await _get_db().save_provider_config(provider_name, changes)
        return {"download_dir": path}


@router.get("/douyin/collects")
async def get_collects_list():
    try:
        return {"folders": await _provider("douyin").fetch_collects_list()}
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Douyin collections could not be loaded")
        raise HTTPException(status_code=502, detail="收藏夹读取失败，请刷新后重试") from exc


@router.get("/douyin/session", response_model=DouyinSession)
async def douyin_session(response: Response):
    try:
        account = await _provider("douyin").get_session()
        response.headers["Cache-Control"] = "no-store"
        return account
    except Exception as exc:
        logger.exception("Douyin session check failed")
        raise HTTPException(status_code=502, detail="暂未读取到抖音登录状态，请重试") from exc


@router.post("/douyin/logout", response_model=DouyinSession)
async def logout_douyin(request: Request):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="请在下载工作台内退出登录")
    provider = _provider("douyin")
    if "douyin" in _session_changes:
        raise HTTPException(status_code=409, detail="正在处理账号登录状态")
    _session_changes.add("douyin")
    try:
        account = await provider.logout()
        await _get_db().clear_douyin_catalog()
        return account
    except Exception as exc:
        logger.exception("Douyin logout failed")
        raise HTTPException(status_code=502, detail="退出登录失败，请重试") from exc
    finally:
        _session_changes.discard("douyin")


async def _read_connected(request: Request, read):
    cancelled = Event()
    job = asyncio.create_task(read(cancelled))
    try:
        while True:
            done, _ = await asyncio.wait((job,), timeout=.2)
            if await request.is_disconnected():
                cancelled.set()
                raise HTTPException(status_code=499, detail="已停止读取作品")
            if done:
                return await job
    finally:
        if not job.done():
            cancelled.set()
            job.cancel()
        await asyncio.gather(job, return_exceptions=True)


@router.get("/douyin/works")
async def browse_douyin_works(
    request: Request, source: str = Query(min_length=1, max_length=256), refresh: bool = False,
):
    provider = _provider("douyin")
    database = _get_db()
    try:
        target = await provider.parse_target(source)
        catalog = None if refresh else await database.get_douyin_catalog(target.target_id)
        if catalog is None:
            snapshot = await _read_connected(request, lambda cancelled: provider.read_works(target, cancelled))
            catalog = {
                "works": [work.model_dump(mode="json") for work in snapshot.works],
                "partial": snapshot.partial,
            }
            await database.save_douyin_catalog(target.target_id, catalog)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Douyin works could not be loaded")
        raise HTTPException(status_code=502, detail="作品读取失败，请刷新后重试") from exc
    return catalog


@router.post("/douyin/downloads", response_model=TaskInfo, status_code=202)
async def download_douyin_works(body: DouyinDownload):
    try:
        return await _get_tm().download_selection(body.source, body.work_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
