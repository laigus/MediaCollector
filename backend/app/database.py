from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import TaskInfo, TaskStatus

logger = logging.getLogger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    target_url TEXT NOT NULL,
    target_name TEXT DEFAULT '',
    save_dir TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    total_count INTEGER DEFAULT 0,
    downloaded_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    start_page INTEGER DEFAULT 1,
    end_page INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    progress_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS downloaded_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    UNIQUE(task_id, source_id)
);

CREATE TABLE IF NOT EXISTS provider_configs (
    provider TEXT PRIMARY KEY,
    config_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_INIT_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ── Tasks ──

    async def create_task(self, task: TaskInfo) -> TaskInfo:
        now = _now()
        cursor = await self._conn.execute(
            """INSERT INTO tasks
               (provider, target_url, target_name, save_dir, status,
                total_count, downloaded_count, failed_count, start_page, end_page,
                error_message, progress_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.provider,
                task.target_url,
                task.target_name,
                task.save_dir,
                task.status.value,
                task.total_count,
                task.downloaded_count,
                task.failed_count,
                task.start_page,
                task.end_page,
                task.error_message,
                task.progress_message,
                now,
                now,
            ),
        )
        await self._conn.commit()
        task.id = cursor.lastrowid
        task.created_at = now
        task.updated_at = now
        return task

    async def get_task(self, task_id: int) -> TaskInfo | None:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    async def list_tasks(self) -> list[TaskInfo]:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(r) for r in rows]

    async def update_task(self, task_id: int, **kwargs: Any) -> None:
        if "status" in kwargs and isinstance(kwargs["status"], TaskStatus):
            kwargs["status"] = kwargs["status"].value
        kwargs["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [task_id]
        await self._conn.execute(
            f"UPDATE tasks SET {sets} WHERE id = ?", vals
        )
        await self._conn.commit()

    async def delete_task(self, task_id: int) -> bool:
        await self._conn.execute(
            "DELETE FROM downloaded_items WHERE task_id = ?", (task_id,)
        )
        cursor = await self._conn.execute(
            "DELETE FROM tasks WHERE id = ?", (task_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> TaskInfo:
        return TaskInfo(
            id=row["id"],
            provider=row["provider"],
            target_url=row["target_url"],
            target_name=row["target_name"],
            save_dir=row["save_dir"],
            status=TaskStatus(row["status"]),
            total_count=row["total_count"],
            downloaded_count=row["downloaded_count"],
            failed_count=row["failed_count"],
            start_page=row["start_page"],
            end_page=row["end_page"],
            error_message=row["error_message"],
            progress_message=row["progress_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Downloaded Items (增量下载) ──

    async def get_downloaded_ids(self, task_id: int) -> set[str]:
        cursor = await self._conn.execute(
            "SELECT source_id FROM downloaded_items WHERE task_id = ?",
            (task_id,),
        )
        rows = await cursor.fetchall()
        return {r["source_id"] for r in rows}

    async def add_downloaded_id(self, task_id: int, source_id: str) -> None:
        await self._conn.execute(
            """INSERT OR IGNORE INTO downloaded_items
               (task_id, source_id, downloaded_at) VALUES (?, ?, ?)""",
            (task_id, source_id, _now()),
        )
        await self._conn.commit()

    # ── Provider Configs ──

    async def get_provider_config(self, provider: str) -> dict[str, Any]:
        cursor = await self._conn.execute(
            "SELECT config_json FROM provider_configs WHERE provider = ?",
            (provider,),
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        return json.loads(row["config_json"])

    async def save_provider_config(
        self, provider: str, config: dict[str, Any]
    ) -> None:
        await self._conn.execute(
            """INSERT INTO provider_configs (provider, config_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET
               config_json = excluded.config_json,
               updated_at = excluded.updated_at""",
            (provider, json.dumps(config, ensure_ascii=False), _now()),
        )
        await self._conn.commit()
