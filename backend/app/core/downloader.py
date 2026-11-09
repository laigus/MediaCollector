from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.models import MediaItem

logger = logging.getLogger(__name__)


class AsyncDownloader:
    """通用异步批量下载引擎。"""

    def __init__(self, max_concurrent: int = 5, timeout: int = 60):
        self.max_concurrent = max_concurrent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    )
                },
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = name.replace("\n", " ").replace("\r", " ")
        name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)
        name = re.sub(r'\s+', ' ', name).strip()
        if len(name) > 200:
            ext_idx = name.rfind(".")
            if ext_idx > 0:
                ext = name[ext_idx:]
                name = name[:200 - len(ext)] + ext
            else:
                name = name[:200]
        return name

    @staticmethod
    def _resolve_filename(item: MediaItem, index: int) -> str:
        if item.filename:
            return item.filename
        parsed = urlparse(item.url)
        path_name = Path(parsed.path).name
        if path_name and "." in path_name:
            return path_name
        ext = ".jpg" if item.media_type.value == "image" else ".mp4"
        return f"{item.source_id or index:04}{ext}"

    async def download_one(
        self,
        item: MediaItem,
        save_dir: Path,
        index: int = 0,
        headers: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> tuple[bool, str]:
        """下载单个文件，返回 (是否成功, 文件路径或错误信息)。"""
        filename = self._sanitize_filename(self._resolve_filename(item, index))
        filepath = save_dir / filename

        if filepath.exists():
            return True, str(filepath)

        session = await self._get_session()
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                async with session.get(item.url, headers=headers) as resp:
                    if resp.status == 200:
                        save_dir.mkdir(parents=True, exist_ok=True)
                        content = await resp.read()
                        filepath.write_bytes(content)
                        return True, str(filepath)
                    last_error = f"HTTP {resp.status}"
                    if resp.status in (403, 404):
                        break
            except Exception as e:
                last_error = str(e)
                logger.warning("Download attempt %d/%d failed %s: %s", attempt, max_retries, item.url[:100], e)

            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)

        return False, last_error

    async def download_batch(
        self,
        items: list[MediaItem],
        save_dir: Path,
        headers: dict[str, str] | None = None,
        progress_callback: Any = None,
    ) -> tuple[int, int, list[dict]]:
        """批量下载，返回 (成功数, 失败数, 失败详情列表)。"""
        semaphore = asyncio.Semaphore(self.max_concurrent)
        success_count = 0
        fail_count = 0
        failed_details: list[dict] = []

        async def _download_with_sem(item: MediaItem, idx: int) -> tuple[MediaItem, bool, str]:
            async with semaphore:
                ok, msg = await self.download_one(item, save_dir, idx, headers)
                if ok:
                    logger.debug("Downloaded: %s", msg)
                else:
                    logger.warning("Failed: %s -> %s", item.filename or item.url[:80], msg)
                return item, ok, msg

        tasks = [asyncio.create_task(_download_with_sem(item, i)) for i, item in enumerate(items)]
        try:
            for coro in asyncio.as_completed(tasks):
                item, ok, msg = await coro
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                    failed_details.append({
                        "filename": item.filename or "unknown",
                        "source_id": item.source_id or "",
                        "error": msg,
                    })
                if progress_callback:
                    await progress_callback(success_count, fail_count, len(items))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return success_count, fail_count, failed_details
