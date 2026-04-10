from __future__ import annotations

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


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    ok = await _get_tm().delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}
