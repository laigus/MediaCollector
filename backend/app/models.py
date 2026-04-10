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
    provider: str
    target_url: str
    save_dir: str = ""
    start_page: int = 1
    end_page: int = 0


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


class ProviderInfo(BaseModel):
    name: str
    display_name: str
    description: str = ""
    config_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ProviderConfigUpdate(BaseModel):
    provider: str
    config: dict[str, Any]
