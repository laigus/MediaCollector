from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models import TaskCreate, TaskInfo

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _get_tm():
    from app.main import task_manager
    return task_manager


@router.post("", response_model=TaskInfo)
async def create_task(body: TaskCreate):
    tm = _get_tm()
    try:
        task = await tm.create_task(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return task


@router.get("", response_model=list[TaskInfo])
async def list_tasks():
    return await _get_tm().list_tasks()


@router.get("/{task_id}", response_model=TaskInfo)
async def get_task(task_id: int):
    task = await _get_tm().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/start", response_model=TaskInfo)
async def start_task(task_id: int):
    tm = _get_tm()
    try:
        task = await tm.start_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return task


@router.get("/{task_id}/failed")
async def get_failed_list(task_id: int):
    """获取任务的下载失败文件列表。"""
    task = await _get_tm().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    report_path = Path(task.save_dir) / "_下载失败列表.txt"
    if not report_path.exists():
        return {"content": "", "count": 0}
    content = report_path.read_text(encoding="utf-8")
    count = task.failed_count
    return {"content": content, "count": count}


@router.post("/{task_id}/retry-failed")
async def retry_failed(task_id: int):
    """重试下载失败的文件。"""
    tm = _get_tm()
    try:
        result = await tm.retry_failed(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    ok = await _get_tm().delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}
