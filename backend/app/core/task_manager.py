from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.downloader import AsyncDownloader
from app.models import TaskCreate, TaskInfo, TaskStatus

logger = logging.getLogger(__name__)


class TaskManager:
    """管理下载任务的生命周期。"""

    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._downloader = AsyncDownloader(
            max_concurrent=settings.max_concurrent_downloads
        )
        self._running_tasks: dict[int, asyncio.Task] = {}
        self._db: Any = None

    def set_db(self, db: Any) -> None:
        self._db = db

    def register_provider(self, provider: Any) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> Any | None:
        return self._providers.get(name)

    def list_providers(self) -> list[Any]:
        return list(self._providers.values())

    async def create_task(self, task_create: TaskCreate) -> TaskInfo:
        provider = self._providers.get(task_create.provider)
        if not provider:
            raise ValueError(f"Unknown provider: {task_create.provider}")

        target = await provider.parse_target(task_create.target_url)

        save_dir = task_create.save_dir or str(
            Path(settings.download_dir) / provider.display_name / target.display_name
        )

        task_info = TaskInfo(
            provider=task_create.provider,
            target_url=task_create.target_url,
            target_name=target.display_name,
            save_dir=save_dir,
            status=TaskStatus.PENDING,
            start_page=task_create.start_page,
            end_page=task_create.end_page,
        )

        if self._db:
            task_info = await self._db.create_task(task_info)

        return task_info

    async def start_task(self, task_id: int) -> TaskInfo:
        if not self._db:
            raise RuntimeError("Database not initialized")

        task_info = await self._db.get_task(task_id)
        if not task_info:
            raise ValueError(f"Task {task_id} not found")

        if task_id in self._running_tasks:
            return task_info

        bg_task = asyncio.create_task(self._run_task(task_info))
        self._running_tasks[task_id] = bg_task
        return task_info

    async def _run_task(self, task_info: TaskInfo) -> None:
        task_id = task_info.id
        try:
            await self._db.update_task(
                task_id, status=TaskStatus.RUNNING, progress_message="正在解析目标..."
            )

            provider = self._providers.get(task_info.provider)
            if not provider:
                raise ValueError(f"Provider not found: {task_info.provider}")

            target = await provider.parse_target(task_info.target_url)

            downloaded_ids = set()

            async def on_fetch_progress(count: int, msg: str) -> None:
                logger.info("Task %d fetch progress: %d - %s", task_id, count, msg)
                await self._db.update_task(
                    task_id, progress_message=f"正在抓取列表: {msg}（已发现 {count} 个文件）"
                )

            media_list = await provider.fetch_media_list(
                target,
                start_page=task_info.start_page,
                end_page=task_info.end_page,
                downloaded_ids=downloaded_ids,
                progress_callback=on_fetch_progress,
            )

            await self._db.update_task(
                task_id,
                total_count=len(media_list),
                progress_message=f"列表抓取完成，共 {len(media_list)} 个文件，准备下载...",
            )

            if not media_list:
                await self._db.update_task(
                    task_id, status=TaskStatus.COMPLETED, progress_message="未发现新内容"
                )
                return

            save_dir = Path(task_info.save_dir)

            async def on_download_progress(done: int, total: int) -> None:
                await self._db.update_task(
                    task_id,
                    downloaded_count=done,
                    progress_message=f"正在下载: {done}/{total}",
                )

            download_headers = None
            if hasattr(provider, "get_download_headers_async"):
                download_headers = await provider.get_download_headers_async()
            elif hasattr(provider, "get_download_headers"):
                download_headers = provider.get_download_headers()

            success, failed = await self._downloader.download_batch(
                media_list,
                save_dir,
                headers=download_headers,
                progress_callback=on_download_progress,
            )

            for item in media_list:
                if item.source_id:
                    await self._db.add_downloaded_id(task_id, item.source_id)

            await self._db.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                downloaded_count=success,
                failed_count=failed,
                progress_message=f"完成！成功 {success}，失败 {failed}",
            )
            logger.info(
                "Task %d completed: %d success, %d failed", task_id, success, failed
            )

        except Exception as e:
            logger.exception("Task %d failed: %s", task_id, e)
            await self._db.update_task(
                task_id,
                status=TaskStatus.FAILED,
                error_message=str(e),
                progress_message=f"失败: {e}",
            )
        finally:
            self._running_tasks.pop(task_id, None)

    async def get_task(self, task_id: int) -> TaskInfo | None:
        if self._db:
            return await self._db.get_task(task_id)
        return None

    async def list_tasks(self) -> list[TaskInfo]:
        if self._db:
            return await self._db.list_tasks()
        return []

    async def delete_task(self, task_id: int) -> bool:
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
            self._running_tasks.pop(task_id, None)
        if self._db:
            return await self._db.delete_task(task_id)
        return False

    async def close(self) -> None:
        for t in self._running_tasks.values():
            t.cancel()
        self._running_tasks.clear()
        await self._downloader.close()
        for p in self._providers.values():
            await p.close()
