from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse, urlencode

from app.core.base_provider import BaseProvider
from app.models import MediaItem, MediaType, TargetInfo

logger = logging.getLogger(__name__)

_BASE_URL = "https://m.weibo.cn/api/container/getIndex"
_BROWSER_DATA_DIR = str(Path(__file__).parent.parent.parent / "data" / "weibo_browser")
_executor = ThreadPoolExecutor(max_workers=1)


_CONFIG_URL = "https://m.weibo.cn/api/config"


@contextmanager
def _open_browser(*, headless: bool):
    from playwright.sync_api import sync_playwright

    Path(_BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_DATA_DIR,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
        )
        try:
            yield context, context.pages[0] if context.pages else context.new_page()
        finally:
            context.close()


def _read_json(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded")
    try:
        result = json.loads(page.inner_text("body", timeout=8000))
    except json.JSONDecodeError as exc:
        raise ValueError("微博未返回预期数据，请在微博窗口登录后重试") from exc
    if not isinstance(result, dict):
        raise ValueError("微博未返回预期数据，请重试")
    return result


def _logged_in(page) -> bool:
    url = urlparse(page.url)
    if url.hostname != "m.weibo.cn":
        return False
    try:
        if url.path == "/api/config":
            data = json.loads(page.inner_text("body", timeout=2000))
        else:
            data = page.evaluate("""async () => {
                const response = await fetch('/api/config', {
                    cache: 'no-store', signal: AbortSignal.timeout(4000)
                });
                return response.json();
            }""")
        return (data.get("data") or {}).get("login") is True
    except Exception:
        return False


def _login_in_browser(context, page) -> None:
    from playwright.sync_api import Error

    try:
        config = _read_json(page, _CONFIG_URL).get("data") or {}
        if config.get("login") is True:
            return
        login_url = config.get("loginUrl") or ""
        login_page = urlparse(login_url)
        if login_page.scheme != "https" or login_page.hostname not in {"passport.weibo.com", "passport.weibo.cn"}:
            raise ValueError("微博未返回登录入口，请重试")
        page.goto(login_url, wait_until="domcontentloaded")

        deadline = monotonic() + 180
        while monotonic() < deadline:
            pages = [candidate for candidate in context.pages if not candidate.is_closed()]
            if not pages:
                raise ValueError("微博登录窗口已关闭")
            if any(_logged_in(candidate) for candidate in pages):
                return
            pages[0].wait_for_timeout(1000)
    except Error as exc:
        if page.is_closed():
            raise ValueError("微博登录窗口已关闭") from exc
        raise
    raise ValueError("微博登录超时，请重新打开登录窗口")


def _sync_login() -> None:
    with _open_browser(headless=False) as (context, page):
        _login_in_browser(context, page)


def _needs_verification(data: dict) -> bool:
    return str(data.get("ok")) == "-100" or str(data.get("errno")) == "-100"


def _sync_browser_request(url: str) -> dict:
    with _open_browser(headless=True) as (_, page):
        data = _read_json(page, url)
    if not _needs_verification(data):
        return data

    with _open_browser(headless=False) as (context, page):
        redirect_url = data.get("url") or ""
        redirect = urlparse(redirect_url)
        if redirect.scheme == "https" and redirect.hostname == "m.weibo.cn" and "captcha" in redirect.path:
            page.goto(redirect_url, wait_until="domcontentloaded")
            deadline = monotonic() + 120
            while "captcha" in urlparse(page.url).path:
                if monotonic() >= deadline:
                    raise ValueError("微博验证超时，请重新下载")
                page.wait_for_timeout(1000)
        else:
            _login_in_browser(context, page)
        data = _read_json(page, url)
        if _needs_verification(data):
            raise ValueError("微博仍需登录或验证，请完成后重试")
        return data


class WeiboProvider(BaseProvider):
    name = "weibo"
    display_name = "微博"
    description = "从微博用户主页批量下载原图（使用浏览器引擎）"

    async def login(self) -> None:
        await asyncio.get_running_loop().run_in_executor(_executor, _sync_login)

    async def _api_get(self, params: dict[str, Any]) -> dict:
        qs = urlencode(params)
        url = f"{_BASE_URL}?{qs}"
        return await asyncio.get_running_loop().run_in_executor(
            _executor, _sync_browser_request, url
        )

    @staticmethod
    def extract_uid(url_or_id: str) -> str:
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
        uid = self.extract_uid(url_or_id)
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
                if not media_items:
                    raise
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

    def get_download_headers(self) -> dict[str, str]:
        return {"Referer": "https://weibo.com/"}
