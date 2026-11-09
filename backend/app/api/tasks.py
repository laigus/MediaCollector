from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models import TaskCreate, TaskInfo

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _get_tm():
    from app.main import task_manager
    return task_manager


@router.post("", response_model=TaskInfo, status_code=202)
async def download_weibo(body: TaskCreate):
    try:
        return await _get_tm().download_weibo(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}", response_model=TaskInfo)
async def get_task(task_id: int):
    task = await _get_tm().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="下载记录不存在")
    return task


@router.get("/{task_id}/failed")
async def get_failed_list(task_id: int):
    await get_task(task_id)
    return {"files": await _get_tm().get_failures(task_id)}


@router.post("/{task_id}/retry-failed", response_model=TaskInfo, status_code=202)
async def retry_failed(task_id: int):
    try:
        return await _get_tm().retry_failed(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
