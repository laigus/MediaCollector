from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"


class TargetInfo(BaseModel):
    provider: str
    target_id: str
    display_name: str = ""
    avatar_url: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class MediaItem(BaseModel):
    url: str
    media_type: MediaType = MediaType.IMAGE
    filename: str = ""
    source_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class TaskCreate(BaseModel):
    target_url: str = Field(min_length=1)
    start_page: int = Field(default=1, ge=1)
    end_page: int = Field(default=0, ge=0)


class DouyinDownload(BaseModel):
    source: str = Field(min_length=1, max_length=256)
    work_ids: list[str] = Field(min_length=1)


class DouyinWork(BaseModel):
    id: str
    title: str
    author: str
    cover_url: str = ""
    kind: MediaType
    file_count: int
    duration_seconds: int = 0


class DouyinSession(BaseModel):
    logged_in: bool
    nickname: str = ""
    account_id: str = ""
    avatar_url: str = ""


class TaskInfo(BaseModel):
    id: int = 0
    provider: str
    target_url: str
    target_name: str = ""
    save_dir: str = ""
    status: TaskStatus = TaskStatus.PENDING
    total_count: int = 0
    downloaded_count: int = 0
    failed_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    start_page: int = 1
    end_page: int = 0
    error_message: str = ""
    progress_message: str = ""


class FolderSelection(BaseModel):
    source: str | None = Field(default=None, min_length=1, max_length=256)
