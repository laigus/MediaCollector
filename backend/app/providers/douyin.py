from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.base_provider import BaseProvider
from app.models import MediaItem, MediaType, TargetInfo

logger = logging.getLogger(__name__)

_BROWSER_DATA_DIR = str(Path(__file__).parent.parent.parent / "data" / "douyin_browser")
_executor = ThreadPoolExecutor(max_workers=1)
_browser_lock = threading.Lock()

DOUYIN_SOURCE_TYPES = {
    "favorite": "收藏",
    "like": "点赞",
}


def _sanitize_name(text: str) -> str:
    """清理文本使其可安全用作文件名的一部分。"""
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', text)
    text = re.sub(r'[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\-_.()（）【】]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:80] if text else "untitled"


def _extract_no_watermark_video_url(video_data: dict) -> str:
    """从视频数据中提取无水印视频 URL。"""
    play_addr = video_data.get("play_addr", {})
    url_list = play_addr.get("url_list", [])
    for url in url_list:
        if "watermark" not in url.lower():
            return url
    if url_list:
        return url_list[0].replace("playwm", "play")
    return ""


def _extract_images(aweme: dict) -> list[str]:
    """从图集数据中提取图片 URL。"""
    images = aweme.get("images") or []
    urls = []
    for img in images:
        url_list = img.get("url_list", [])
        if url_list:
            urls.append(url_list[-1])
    return urls


def _check_login_popup(page) -> bool:
    """检查页面上是否有登录弹窗（QR 码、登录表单等）。"""
    selectors = [
        '[class*="login-guide"]',
        '[class*="qrcode"]',
        '[class*="login-panel"]',
        '[class*="login-modal"]',
        '[class*="dy-account-close"]',
        'div[class*="mask"]',
        'img[src*="qrcode"]',
        'iframe[src*="passport"]',
    ]
    for sel in selectors:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass

    try:
        text = page.inner_text("body")
        if "扫码登录" in text or "登录后查看" in text or "请先登录" in text:
            return True
    except Exception:
        pass

    return False


def _wait_for_login(context, page, target_url: str, timeout: int = 180) -> bool:
    """等待用户在浏览器中完成登录，返回是否成功。"""
    logger.info("检测到需要登录，请在弹出的浏览器中扫码登录（最多 %d 秒）...", timeout)
    for i in range(timeout):
        page.wait_for_timeout(1000)
        cookies = context.cookies("https://www.douyin.com")
        has_session = any(c["name"] in ("sessionid", "sessionid_ss") for c in cookies)
        if has_session:
            if not _check_login_popup(page):
                logger.info("登录成功！（用时 %d 秒）刷新页面...", i)
                page.goto(target_url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                return True
        if i % 10 == 0:
            logger.info("  等待扫码登录... (%ds / %ds)", i, timeout)
    return False


def _sync_fetch_collects_list(cookie: str) -> list[dict]:
    """获取用户的收藏夹列表（名称 + ID）。"""
    from playwright.sync_api import sync_playwright

    with _browser_lock:
        return _sync_fetch_collects_list_impl(cookie)


def _sync_fetch_collects_list_impl(cookie: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    Path(_BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
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

    if cookie:
        cookies_to_add = []
        for part in cookie.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookies_to_add.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".douyin.com",
                "path": "/",
            })
        if cookies_to_add:
            context.add_cookies(cookies_to_add)

    page = context.pages[0] if context.pages else context.new_page()

    captured_folders: list[dict] = []

    def handle_response(response):
        url = response.url
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            return
        if "douyin.com" not in url or "application/json" not in content_type:
            return
        if "collects/list" in url:
            logger.info("COLLECTS LIST API: %s", url[:200])
            try:
                body = response.json()
                if isinstance(body, dict) and "collects_list" in body and body["collects_list"]:
                    captured_folders.append(body)
                    logger.info("  Found %d folders", len(body["collects_list"]))
            except Exception as e:
                logger.warning("  Failed to parse collects list: %s", e)

    page.on("response", handle_response)

    target_url = "https://www.douyin.com/user/self?showTab=favorite_collection"
    logger.info("Fetching collects list, navigating to: %s", target_url)
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    current_url = page.url
    need_login = (
        "login" in current_url.lower()
        or "passport" in current_url.lower()
        or _check_login_popup(page)
    )
    if not need_login:
        cookies_list = context.cookies("https://www.douyin.com")
        has_session = any(c["name"] in ("sessionid", "sessionid_ss") for c in cookies_list)
        if not has_session:
            need_login = True

    if need_login:
        logged_in = _wait_for_login(context, page, target_url)
        if not logged_in:
            context.close()
            p.stop()
            raise ValueError("登录超时，请重试")

    page.wait_for_timeout(3000)

    for _ in range(3):
        page.evaluate("""
            () => {
                const el = document.scrollingElement || document.documentElement || document.body;
                if (el) el.scrollTop = el.scrollHeight;
            }
        """)
        page.wait_for_timeout(2000)

    folders = []
    seen_ids = set()
    for data in captured_folders:
        for item in data.get("collects_list", []):
            folder_id = str(item.get("collects_id", ""))
            if not folder_id or folder_id in seen_ids:
                continue
            seen_ids.add(folder_id)
            name = item.get("collects_name", "未命名")
            count = item.get("total_number", item.get("vcount", item.get("total_count", 0)))
            folders.append({
                "id": folder_id,
                "name": name,
                "count": count,
            })

    context.close()
    p.stop()

    logger.info("Found %d collection folders", len(folders))
    return folders


def _sync_fetch_douyin_page(
    source_type: str,
    page_num: int,
    cookie: str,
) -> tuple[list[dict], bool, str]:
    """在独立线程中用 Playwright 打开抖音页面，通过网络拦截获取数据。

    Returns:
        (aweme_list, has_more, browser_cookie_str)
    """
    from playwright.sync_api import sync_playwright

    Path(_BROWSER_DATA_DIR).mkdir(parents=True, exist_ok=True)

    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
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
                "domain": ".douyin.com",
                "path": "/",
            })
        if cookies:
            context.add_cookies(cookies)

    page = context.pages[0] if context.pages else context.new_page()

    captured_data: list[dict] = []

    is_collects = source_type.startswith("collects_")
    collects_id = source_type.replace("collects_", "") if is_collects else ""

    def handle_response(response):
        url = response.url
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            return
        if "douyin.com" not in url or "application/json" not in content_type:
            return

        if is_collects:
            if "collects/video" not in url:
                return
            if collects_id and f"collects_id={collects_id}" not in url:
                return
        elif source_type == "like":
            if "aweme/favorite" not in url and "aweme/v1/web/aweme/favorite" not in url:
                return
        else:
            if "listcollection" not in url:
                return

        logger.info("TARGET API: %s", url[:200])
        try:
            body = response.json()
            if isinstance(body, dict) and "aweme_list" in body:
                count = len(body["aweme_list"])
                logger.info("  CAPTURED data! items=%d, has_more=%s", count, body.get("has_more"))
                captured_data.append(body)
        except Exception as e:
            logger.warning("  Failed to parse response: %s", e)

    page.on("response", handle_response)

    if source_type == "like":
        target_url = "https://www.douyin.com/user/self?showTab=like"
    else:
        target_url = "https://www.douyin.com/user/self?showTab=favorite_collection"

    logger.info("Navigating to: %s", target_url)
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    current_url = page.url
    logger.info("Current URL after navigation: %s", current_url[:200])

    need_login = (
        "login" in current_url.lower()
        or "passport" in current_url.lower()
        or _check_login_popup(page)
    )

    if not need_login:
        cookies_list = context.cookies("https://www.douyin.com")
        has_session = any(c["name"] in ("sessionid", "sessionid_ss") for c in cookies_list)
        if not has_session:
            need_login = True
            logger.info("没有有效的 session cookie，需要登录")

    if need_login:
        logged_in = _wait_for_login(context, page, target_url)
        if not logged_in:
            context.close()
            p.stop()
            raise ValueError("登录超时（180秒），请重启后端重试")

    if is_collects:
        logger.info("正在进入收藏夹 (collects_id=%s)...", collects_id)
        try:
            found = False
            folder_links = page.query_selector_all('a[href*="collection"], div[class*="collect"]')
            for link in folder_links:
                href = link.get_attribute("href") or ""
                text = link.inner_text() or ""
                logger.info("  Found link: href=%s, text=%s", href[:80], text[:30])
                if collects_id in href:
                    link.click()
                    found = True
                    break

            if not found:
                logger.info("未找到收藏夹链接，尝试通过 URL 直接访问...")
                page.goto(
                    f"https://www.douyin.com/collection/{collects_id}",
                    wait_until="domcontentloaded",
                )

            page.wait_for_timeout(5000)
            logger.info("进入收藏夹页面: %s", page.url[:200])
        except Exception as e:
            logger.warning("进入收藏夹失败: %s", e)

    scroll_count = max(page_num + 2, 5)
    logger.info("Page loaded, scrolling %d times...", scroll_count)
    no_new_count = 0
    for scroll in range(scroll_count):
        prev_count = len(captured_data)
        try:
            page.evaluate("""
                () => {
                    const el = document.scrollingElement || document.documentElement || document.body;
                    if (el) el.scrollTop = el.scrollHeight;
                }
            """)
        except Exception as e:
            logger.warning("  Scroll failed: %s", e)
            break
        page.wait_for_timeout(3000)
        new_captured = len(captured_data) - prev_count
        logger.info("  Scroll %d done, captured: %d total (%d new)", scroll + 1, len(captured_data), new_captured)

        if new_captured == 0:
            no_new_count += 1
            if no_new_count >= 3 and len(captured_data) > 0:
                logger.info("  连续 %d 次无新数据，停止滚动", no_new_count)
                break
        else:
            no_new_count = 0

    all_awemes = []
    has_more = False
    for data in captured_data:
        aweme_list = data.get("aweme_list") or []
        all_awemes.extend(aweme_list)
        if data.get("has_more"):
            has_more = True

    logger.info("Total captured: %d awemes, has_more=%s", len(all_awemes), has_more)

    browser_cookies = context.cookies("https://www.douyin.com")
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in browser_cookies)

    context.close()
    p.stop()

    return all_awemes, has_more, cookie_str


class DouyinProvider(BaseProvider):
    name = "douyin"
    display_name = "抖音"
    description = "下载抖音收藏/点赞的视频和图片（无水印）"

    def __init__(self) -> None:
        self._cookie: str = ""
        self._browser_cookie: str = ""

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie

    def _parse_source_type(self, url_or_id: str) -> tuple[str, str]:
        """返回 (source_type, display_name)。"""
        text = url_or_id.strip()
        if text.startswith("collects_"):
            folder_name = text.split("|", 1)[1] if "|" in text else text
            collects_id = text.split("|")[0]
            safe_folder = _sanitize_name(folder_name)
            return collects_id, f"收藏夹-{safe_folder}"
        lower = text.lower()
        if "like" in lower or "点赞" in lower or "喜欢" in lower:
            return "like", "我的点赞"
        return "favorite", "我的收藏"

    async def parse_target(self, url_or_id: str) -> TargetInfo:
        source_type, display = self._parse_source_type(url_or_id)

        return TargetInfo(
            provider=self.name,
            target_id=f"my_{source_type}",
            display_name=display,
            extra={"source_type": source_type},
        )

    async def fetch_collects_list(self) -> list[dict]:
        """获取用户的收藏夹列表。"""
        loop = asyncio.get_event_loop()
        folders = await loop.run_in_executor(
            _executor,
            _sync_fetch_collects_list,
            self._cookie,
        )
        return folders

    async def fetch_media_list(
        self,
        target: TargetInfo,
        *,
        start_page: int = 1,
        end_page: int = 0,
        downloaded_ids: set[str] | None = None,
        progress_callback: Any = None,
    ) -> list[MediaItem]:
        source_type = target.extra.get("source_type", "favorite")
        logger.info("fetch_media_list: douyin %s, pages=%d-%s", source_type, start_page, end_page or "∞")

        media_items: list[MediaItem] = []
        page = 1
        seen_ids: set[str] = set()

        while True:
            in_range = page >= start_page

            try:
                loop = asyncio.get_event_loop()
                with _browser_lock:
                    aweme_list, has_more, browser_cookie = await loop.run_in_executor(
                        _executor,
                        _sync_fetch_douyin_page,
                        source_type,
                        page,
                        self._cookie,
                    )
                if browser_cookie:
                    self._browser_cookie = browser_cookie
            except Exception as e:
                logger.error("Fetch page %d failed: %s", page, e)
                break

            new_items = 0
            for aweme in aweme_list:
                aweme_id = str(aweme.get("aweme_id", ""))
                if not aweme_id or aweme_id in seen_ids:
                    continue
                seen_ids.add(aweme_id)

                if not in_range:
                    continue

                aweme_type = aweme.get("aweme_type", 0)
                desc = aweme.get("desc", "").strip()[:50]
                author = aweme.get("author", {}).get("nickname", "unknown")
                safe_desc = _sanitize_name(desc) if desc else aweme_id
                safe_author = _sanitize_name(author)

                if aweme_type == 68:
                    image_urls = _extract_images(aweme)
                    for idx, img_url in enumerate(image_urls):
                        media_items.append(
                            MediaItem(
                                url=img_url,
                                media_type=MediaType.IMAGE,
                                source_id=f"{aweme_id}_img_{idx}",
                                filename=f"{safe_author}_{safe_desc}_{idx}.jpg",
                            )
                        )
                        new_items += 1
                else:
                    video = aweme.get("video", {})
                    video_url = _extract_no_watermark_video_url(video)
                    if video_url:
                        media_items.append(
                            MediaItem(
                                url=video_url,
                                media_type=MediaType.VIDEO,
                                source_id=aweme_id,
                                filename=f"{safe_author}_{safe_desc}.mp4",
                            )
                        )
                        new_items += 1

            status_prefix = "" if in_range else "[跳过] "
            if progress_callback:
                await progress_callback(
                    len(media_items),
                    f"{status_prefix}第 {page} 页滚动，{new_items} 个新文件",
                )

            logger.info(
                "%sPage %d: %d new items (total: %d, has_more: %s)",
                status_prefix, page, new_items, len(media_items), has_more,
            )

            if not has_more or not aweme_list:
                break

            page += 1
            if end_page and page > end_page:
                break

            delay = random.uniform(2.0, 4.0)
            await asyncio.sleep(delay)

        return media_items

    def get_download_headers(self) -> dict[str, str] | None:
        cookie = self._browser_cookie or self._cookie
        headers = {"Referer": "https://www.douyin.com/"}
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "cookie": {
                "type": "string",
                "title": "抖音 Cookie（可选）",
                "description": (
                    "可选：如果不填，首次运行时会弹出浏览器让你登录。"
                    "如需手动填写：登录 douyin.com → F12 → Network → 复制 Cookie"
                ),
            }
        }

    async def validate_config(self, config: dict[str, Any]) -> bool:
        return True

    async def close(self) -> None:
        pass
