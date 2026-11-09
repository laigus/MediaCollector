from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.downloader import AsyncDownloader
from app.models import MediaItem, TargetInfo, TaskCreate, TaskInfo, TaskStatus

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._downloader = AsyncDownloader(max_concurrent=settings.max_concurrent_downloads)
        self._running_tasks: dict[int, asyncio.Task] = {}
        self._db: Any = None

    def set_db(self, db: Any) -> None:
        self._db = db

    def register_provider(self, provider: Any) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> Any | None:
        return self._providers.get(name)

    async def destination(self, provider_name: str, target_id: str | None = None) -> str:
        config = await self._db.get_provider_config(provider_name)
        specific = config.get("source_download_dirs", {}).get(target_id)
        return specific or config.get("download_dir") or ""

    async def _require_destination(self, provider_name: str, target_id: str | None = None) -> Path:
        directory = await self.destination(provider_name, target_id)
        if not directory:
            raise ValueError("请先选择下载文件夹")
        return Path(directory)

    async def download_weibo(self, body: TaskCreate) -> TaskInfo:
        if body.end_page and body.end_page < body.start_page:
            raise ValueError("结束页须大于或等于起始页")
        source = body.target_url.strip()
        if not source:
            raise ValueError("请输入微博主页链接或用户 ID")
        uid = self._providers["weibo"].extract_uid(source)
        target = TargetInfo(provider="weibo", target_id=uid, display_name=uid)
        return await self._start(
            target, source, await self._require_destination("weibo"),
            body.start_page, body.end_page,
        )

    async def download_selection(self, source: str, work_ids: list[str]) -> TaskInfo:
        provider = self._providers["douyin"]
        target = await provider.parse_target(source)
        catalog = await self._db.get_douyin_catalog(target.target_id)
        available = {work["id"] for work in catalog["works"]} if catalog else set()
        selected = list(dict.fromkeys(work_ids))
        if not available.issuperset(selected):
            raise ValueError("有作品不在当前列表，请刷新后重新选择")
        directory = await self._require_destination("douyin", target.target_id)
        media = provider.cached_selection(target, selected)
        return await self._start(target, source, directory, prepared=media, selected_work_ids=selected)

    async def _start(
        self,
        target: TargetInfo,
        source: str,
        save_dir: Path,
        start_page: int = 1,
        end_page: int = 0,
        prepared: list[MediaItem] | None = None,
        selected_work_ids: list[str] | None = None,
    ) -> TaskInfo:
        task = await self._db.create_task(TaskInfo(
            provider=target.provider,
            target_url=source,
            target_name=target.display_name,
            save_dir=str(save_dir),
            status=TaskStatus.RUNNING,
            start_page=start_page,
            end_page=end_page,
            total_count=len(prepared or []),
            progress_message="正在准备下载",
        ))
        self._running_tasks[task.id] = asyncio.create_task(
            self._run(task, prepared, selected_work_ids=selected_work_ids)
        )
        return task

    async def _run(
        self,
        task: TaskInfo,
        prepared: list[MediaItem] | None = None,
        previous_failures: list[dict] | None = None,
        selected_work_ids: list[str] | None = None,
    ) -> None:
        try:
            provider = self._providers[task.provider]
            if prepared is None and selected_work_ids is not None:
                target = await provider.parse_target(task.target_url)
                await self._db.update_task(task.id, progress_message="正在获取所选作品的下载链接")
                media = await provider.fetch_selection(target, selected_work_ids)
            elif prepared is None:
                target = await provider.parse_target(task.target_url)
                if previous_failures is None:
                    await self._db.update_task(task.id, target_name=target.display_name)

                async def on_fetch_progress(count: int, message: str) -> None:
                    await self._db.update_task(task.id, progress_message=f"{message} · 已发现 {count} 个文件")

                media = await provider.fetch_media_list(
                    target, start_page=task.start_page, end_page=task.end_page,
                    progress_callback=on_fetch_progress,
                )
            else:
                media = prepared

            unmatched = []
            already_downloaded = 0
            if previous_failures is not None:
                wanted = {item["source_id"] for item in previous_failures}
                media = [item for item in media if item.source_id in wanted]
                found = {item.source_id for item in media}
                unmatched = [
                    {**item, "error": "作品已不在当前列表或链接已失效"}
                    for item in previous_failures if item["source_id"] not in found
                ]
                already_downloaded = task.downloaded_count

            if media:
                await self._db.update_task(
                    task.id,
                    total_count=already_downloaded + len(media) + len(unmatched),
                    progress_message="正在下载",
                )

                async def on_download_progress(success: int, failed: int, total: int) -> None:
                    counts = {}
                    if previous_failures is None:
                        counts = {"downloaded_count": success, "failed_count": failed}
                    action = "正在重试" if previous_failures is not None else "正在下载"
                    await self._db.update_task(task.id, progress_message=f"{action} · {success + failed}/{total}", **counts)

                headers = provider.get_download_headers()
                success, _, failed_items = await self._downloader.download_batch(
                    media, Path(task.save_dir), headers=headers, progress_callback=on_download_progress
                )
            else:
                success, failed_items = 0, []

            failures = [
                {key: item[key] for key in ("source_id", "filename", "error")}
                for item in failed_items
            ] + unmatched
            downloaded = already_downloaded + success
            await self._db.save_task_failures(task.id, failures)
            await self._db.update_task(
                task.id,
                status=TaskStatus.COMPLETED,
                downloaded_count=downloaded,
                failed_count=len(failures),
                progress_message=(
                    f"已保存 {downloaded} 个文件，{len(failures)} 个失败" if failures
                    else f"已保存 {downloaded} 个文件" if downloaded else "没有找到可下载的内容"
                ),
            )
        except Exception as exc:
            logger.exception("Download %d failed", task.id)
            await self._db.update_task(
                task.id, status=TaskStatus.FAILED, error_message=str(exc), progress_message=str(exc)
            )
        finally:
            self._running_tasks.pop(task.id, None)

    async def get_task(self, task_id: int) -> TaskInfo | None:
        return await self._db.get_task(task_id)

    async def get_failures(self, task_id: int) -> list[dict]:
        return await self._db.get_task_failures(task_id)

    async def retry_failed(self, task_id: int) -> TaskInfo:
        task = await self.get_task(task_id)
        if not task:
            raise LookupError("下载记录不存在")
        if task_id in self._running_tasks or task.status == TaskStatus.RUNNING:
            return task
        failures = await self.get_failures(task_id)
        if not failures:
            raise ValueError("这次下载没有可重试的失败文件")
        task.status = TaskStatus.RUNNING
        task.progress_message = "正在重新加载失败文件的链接"
        await self._db.update_task(
            task_id, status=task.status, error_message="", progress_message=task.progress_message
        )
        self._running_tasks[task_id] = asyncio.create_task(self._run(task, previous_failures=failures))
        return task

    async def close(self) -> None:
        running = list(self._running_tasks.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        await self._downloader.close()
        for provider in self._providers.values():
            await provider.close()
