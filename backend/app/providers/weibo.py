from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlencode

from app.core.base_provider import BaseProvider
from app.models import MediaItem, MediaType, TargetInfo

logger = logging.getLogger(__name__)

_BASE_URL = "https://m.weibo.cn/api/container/getIndex"
_BROWSER_DATA_DIR = str(Path(__file__).parent.parent.parent / "data" / "weibo_browser")
_executor = ThreadPoolExecutor(max_workers=1)
_browser_lock = threading.Lock()


def _sync_browser_request(url: str, cookie: str, need_login: bool = False) -> dict:
    """在独立线程中用 Playwright sync API 请求微博。"""
    from playwright.sync_api import sync_playwright

    Path(_BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_DATA_DIR,
            headless=not need_login,
            args=["--disable-blink-features=AutomationControlled"],
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
        )

        if cookie:
            cookies = []
            for part in cookie.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".weibo.cn",
                    "path": "/",
                })
            if cookies:
                context.add_cookies(cookies)

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        text = page.inner_text("body")

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            context.close()
            raise ValueError(f"非 JSON 响应: {text[:200]}")

        if data.get("ok") == -100 or data.get("errno") == "-100":
            context.close()

            logger.warning("需要验证码/登录，弹出浏览器窗口...")
            context2 = p.chromium.launch_persistent_context(
                _BROWSER_DATA_DIR,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
            )

            page2 = context2.pages[0] if context2.pages else context2.new_page()
            redirect_url = data.get("url", "")
            if "captcha" in redirect_url:
                page2.goto(redirect_url)
            else:
                page2.goto("https://m.weibo.cn/")

            logger.info("请在弹出的浏览器中完成验证码/登录（最多等待 120 秒）...")
            for i in range(120):
                page2.wait_for_timeout(1000)
                current_url = page2.url
                if "captcha" not in current_url and "login" not in current_url and i > 3:
                    break
                if i % 10 == 0:
                    logger.info("  等待中... (%ds) url=%s", i, current_url[:80])

            page2.goto(url, wait_until="domcontentloaded")
            text2 = page2.inner_text("body")
            data = json.loads(text2)

            context2.close()

            if data.get("ok") != 1:
                raise ValueError(
                    f"验证后仍然失败: ok={data.get('ok')}, errno={data.get('errno')}"
                )
        else:
            context.close()

        return data


class WeiboProvider(BaseProvider):
    name = "weibo"
    display_name = "微博"
    description = "从微博用户主页批量下载原图（使用浏览器引擎）"

    def __init__(self) -> None:
        self._cookie: str = ""

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie

    async def _api_get(self, params: dict[str, Any]) -> dict:
        qs = urlencode(params)
        url = f"{_BASE_URL}?{qs}"
        loop = asyncio.get_event_loop()
        with _browser_lock:
            data = await loop.run_in_executor(
                _executor,
                _sync_browser_request,
                url,
                self._cookie,
                False,
            )
        return data

    @staticmethod
    def _extract_uid(url_or_id: str) -> str:
        url_or_id = url_or_id.strip()
        if url_or_id.isdigit():
            return url_or_id

        parsed = urlparse(url_or_id)
        path = parsed.path.rstrip("/")

        m = re.search(r"/u/(\d+)", path)
        if m:
            return m.group(1)

        m = re.search(r"/profile/(\d+)", path)
        if m:
            return m.group(1)

        m = re.search(r"/(\d+)$", path)
        if m:
            return m.group(1)

        raise ValueError(f"无法从 URL 中提取用户 ID: {url_or_id}")

    async def parse_target(self, url_or_id: str) -> TargetInfo:
        uid = self._extract_uid(url_or_id)
        logger.info("parse_target: uid=%s", uid)

        display_name = uid
        avatar_url = ""
        container_id = f"107603{uid}"

        try:
            data = await self._api_get({"type": "uid", "value": uid})

            if data.get("ok") == 1:
                user_info = data.get("data", {}).get("userInfo", {})
                display_name = user_info.get("screen_name", uid)
                avatar_url = user_info.get("avatar_hd", "")
                for tab in data.get("data", {}).get("tabsInfo", {}).get("tabs", []):
                    if tab.get("tab_type") == "weibo":
                        container_id = tab.get("containerid", container_id)
                        break
        except Exception as e:
            logger.warning("parse_target failed for %s: %s (will use defaults)", uid, e)

        return TargetInfo(
            provider=self.name,
            target_id=uid,
            display_name=display_name,
            avatar_url=avatar_url,
            extra={"container_id": container_id},
        )

    async def fetch_media_list(
        self,
        target: TargetInfo,
        *,
        start_page: int = 1,
        end_page: int = 0,
        downloaded_ids: set[str] | None = None,
        progress_callback: Any = None,
    ) -> list[MediaItem]:
        container_id = target.extra.get("container_id", f"107603{target.target_id}")
        logger.info(
            "fetch_media_list: uid=%s, container_id=%s, pages=%d-%s",
            target.target_id, container_id, start_page, end_page or "∞",
        )

        media_items: list[MediaItem] = []
        page = 1
        since_id: str | None = None

        while True:
            params: dict[str, Any] = {
                "type": "uid",
                "value": target.target_id,
                "containerid": container_id,
            }
            if since_id:
                params["since_id"] = since_id

            try:
                data = await self._api_get(params)
            except Exception as e:
                logger.error("Fetch page %d failed: %s", page, e)
                break

            if data.get("ok") != 1:
                logger.warning("API error on page %d: ok=%s", page, data.get("ok"))
                break

            card_list_info = data.get("data", {}).get("cardlistInfo", {})
            since_id = card_list_info.get("since_id")

            cards = data.get("data", {}).get("cards", [])
            if not cards:
                break

            in_range = page >= start_page
            new_items_on_page = 0

            if in_range:
                for card in cards:
                    mblog = card.get("mblog")
                    if not mblog:
                        continue

                    mid = str(mblog.get("id", ""))
                    pics = mblog.get("pics") or []

                    for pic in pics:
                        large = pic.get("large", {})
                        url = large.get("url", "")
                        if not url:
                            url = pic.get("url", "")
                        if not url:
                            continue

                        pid = pic.get("pid", "")
                        source_id = f"{mid}_{pid}" if pid else mid

                        media_items.append(
                            MediaItem(
                                url=url,
                                media_type=MediaType.IMAGE,
                                source_id=source_id,
                            )
                        )
                        new_items_on_page += 1

            status_prefix = "" if in_range else f"[跳过] "
            if progress_callback:
                await progress_callback(
                    len(media_items),
                    f"{status_prefix}第 {page} 页，{new_items_on_page} 张图片",
                )

            logger.info(
                "%sPage %d: %d images (total collected: %d)",
                status_prefix, page, new_items_on_page, len(media_items),
            )

            page += 1
            if end_page and page > end_page:
                break
            if not since_id:
                break

            consecutive = page - max(start_page, 1)
            if consecutive > 0 and consecutive % 20 == 0:
                pause_secs = random.randint(30, 60)
                logger.info(
                    "已连续请求 %d 页，暂停 %d 秒...", consecutive, pause_secs,
                )
                if progress_callback:
                    await progress_callback(
                        len(media_items),
                        f"安全暂停 {pause_secs} 秒（已抓取 {consecutive} 页）",
                    )
                await asyncio.sleep(pause_secs)
            else:
                delay = random.uniform(2.5, 5.0)
                await asyncio.sleep(delay)

        return media_items

    def get_download_headers(self) -> dict[str, str] | None:
        headers = {"Referer": "https://weibo.com/"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "cookie": {
                "type": "string",
                "title": "微博 Cookie（可选）",
                "description": (
                    "可选：如果不填，首次运行时会弹出浏览器让你登录。"
                    "如需手动填写：登录 m.weibo.cn → F12 → Network → 复制 Cookie"
                ),
            }
        }

    async def validate_config(self, config: dict[str, Any]) -> bool:
        return True

    async def close(self) -> None:
        pass
