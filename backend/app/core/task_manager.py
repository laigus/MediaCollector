from __future__ import annotations

import asyncio
import json
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

            success, failed, failed_details = await self._downloader.download_batch(
                media_list,
                save_dir,
                headers=download_headers,
                progress_callback=on_download_progress,
            )

            for item in media_list:
                if item.source_id:
                    await self._db.add_downloaded_id(task_id, item.source_id)

            self._save_failed_report(save_dir, failed_details)

            progress_msg = f"完成！成功 {success}，失败 {failed}"
            if failed_details:
                failed_names = [d["filename"] for d in failed_details[:10]]
                progress_msg += "\n失败文件: " + "、".join(failed_names)
                if len(failed_details) > 10:
                    progress_msg += f"...等共 {len(failed_details)} 个"
                progress_msg += f"\n详见: _下载失败列表.txt"

            await self._db.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                downloaded_count=success,
                failed_count=failed,
                progress_message=progress_msg,
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

    @staticmethod
    def _parse_failed_txt(txt_path: Path) -> list[dict]:
        """从纯文本失败报告中解析出失败项（兼容旧格式）。"""
        import re
        content = txt_path.read_text(encoding="utf-8")
        items = []
        current: dict | None = None
        for line in content.splitlines():
            m = re.match(r'^\d+\.\s+(.+)', line)
            if m:
                if current:
                    items.append(current)
                current = {"filename": m.group(1).strip(), "url": "", "error": ""}
            elif current and line.strip().startswith("错误:"):
                current["error"] = line.strip().removeprefix("错误:").strip()
            elif current and line.strip().startswith("URL:"):
                current["url"] = line.strip().removeprefix("URL:").strip()
        if current and current.get("url"):
            items.append(current)
        return items

    @staticmethod
    def _save_failed_report(save_dir: Path, failed_details: list[dict]) -> None:
        """保存失败列表为 txt（可读）和 json（机器可读）。"""
        json_path = save_dir / "_failed.json"
        txt_path = save_dir / "_下载失败列表.txt"

        if not failed_details:
            for p in (json_path, txt_path):
                if p.exists():
                    p.unlink()
            return

        json_path.write_text(
            json.dumps(failed_details, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        lines = [f"下载失败文件列表（共 {len(failed_details)} 个）\n"]
        lines.append("=" * 60 + "\n")
        for i, detail in enumerate(failed_details, 1):
            lines.append(f"\n{i}. {detail['filename']}")
            lines.append(f"   错误: {detail['error']}")
            lines.append(f"   URL: {detail['url']}")
        txt_path.write_text("\n".join(lines), encoding="utf-8")

    async def retry_failed(self, task_id: int) -> dict:
        """重试下载失败的文件：重新获取最新 URL 再下载。"""
        if not self._db:
            raise RuntimeError("Database not initialized")

        task_info = await self._db.get_task(task_id)
        if not task_info:
            raise ValueError(f"Task {task_id} not found")

        save_dir = Path(task_info.save_dir)
        json_path = save_dir / "_failed.json"
        txt_path = save_dir / "_下载失败列表.txt"

        failed_items_raw: list[dict] = []
        if json_path.exists():
            failed_items_raw = json.loads(json_path.read_text(encoding="utf-8"))
        elif txt_path.exists():
            failed_items_raw = self._parse_failed_txt(txt_path)

        if not failed_items_raw:
            return {"retried": 0, "success": 0, "still_failed": 0}

        failed_source_ids = {
            item.get("source_id", "")
            for item in failed_items_raw
            if item.get("source_id")
        }
        failed_filenames = {item.get("filename", "") for item in failed_items_raw}
        total_to_retry = len(failed_items_raw)

        provider = self._providers.get(task_info.provider)
        if not provider:
            raise ValueError(f"Provider not found: {task_info.provider}")

        await self._db.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress_message=f"正在重新获取 {total_to_retry} 个失败文件的最新链接...",
        )

        target = await provider.parse_target(task_info.target_url)

        async def on_fetch_progress(count: int, msg: str) -> None:
            await self._db.update_task(
                task_id,
                progress_message=f"正在重新加载内容: {msg}（已发现 {count} 个文件）",
            )

        fresh_media_list = await provider.fetch_media_list(
            target,
            start_page=task_info.start_page,
            end_page=task_info.end_page,
            progress_callback=on_fetch_progress,
        )

        retry_items = []
        if failed_source_ids:
            for item in fresh_media_list:
                if item.source_id in failed_source_ids:
                    retry_items.append(item)
        else:
            fresh_by_name = {item.filename: item for item in fresh_media_list}
            for fname in failed_filenames:
                if fname in fresh_by_name:
                    retry_items.append(fresh_by_name[fname])

        if not retry_items:
            await self._db.update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress_message=f"重试失败：无法重新获取失败文件的链接（可能已被删除）",
            )
            return {"retried": 0, "success": 0, "still_failed": total_to_retry}

        await self._db.update_task(
            task_id,
            progress_message=f"已重新获取 {len(retry_items)} 个文件的最新链接，开始下载...",
        )

        download_headers = None
        if hasattr(provider, "get_download_headers_async"):
            download_headers = await provider.get_download_headers_async()
        elif hasattr(provider, "get_download_headers"):
            download_headers = provider.get_download_headers()

        async def on_download_progress(done: int, total: int) -> None:
            await self._db.update_task(
                task_id,
                progress_message=f"正在重试下载: {done}/{total}",
            )

        _, _, new_failed = await self._downloader.download_batch(
            retry_items,
            save_dir,
            headers=download_headers,
            progress_callback=on_download_progress,
        )

        matched_source_ids = {item.source_id for item in retry_items}
        unmatched = [
            raw for raw in failed_items_raw
            if raw.get("source_id", "") not in matched_source_ids
            and raw.get("filename", "") not in {item.filename for item in retry_items}
        ]

        still_failed = new_failed + [
            {**item, "error": "无法重新获取链接"}
            for item in unmatched
        ]

        success_count = len(retry_items) - len(new_failed)

        self._save_failed_report(save_dir, still_failed)

        new_downloaded = task_info.downloaded_count + success_count
        new_fail_count = len(still_failed)

        if still_failed:
            failed_names = [d["filename"] for d in still_failed[:10]]
            msg = (
                f"重试完成！本次成功 {success_count}，仍失败 {new_fail_count}\n"
                f"失败文件: {'、'.join(failed_names)}"
            )
            if len(still_failed) > 10:
                msg += f"...等共 {new_fail_count} 个"
            msg += "\n详见: _下载失败列表.txt"
        else:
            msg = f"重试完成！全部 {success_count} 个文件下载成功"

        await self._db.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            downloaded_count=new_downloaded,
            failed_count=new_fail_count,
            progress_message=msg,
        )

        return {
            "retried": total_to_retry,
            "success": success_count,
            "still_failed": new_fail_count,
        }

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
