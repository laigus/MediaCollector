from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.core.base_provider import BaseProvider
from app.models import DouyinSession, DouyinWork, MediaItem, MediaType, TargetInfo

logger = logging.getLogger(__name__)

_BROWSER_DATA_DIR = str(Path(__file__).parent.parent.parent / "data" / "douyin_browser")
_executor = ThreadPoolExecutor(max_workers=1)
_browser_lock = threading.Lock()


class _InteractiveBrowserRequired(RuntimeError):
    pass


class _ReadCancelled(RuntimeError):
    pass


def _check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise _ReadCancelled()


def _wait(page, milliseconds: int, cancelled: threading.Event | None) -> None:
    if cancelled is None:
        page.wait_for_timeout(milliseconds)
        return
    remaining = milliseconds
    while remaining > 0:
        _check_cancelled(cancelled)
        interval = min(remaining, 200)
        page.wait_for_timeout(interval)
        remaining -= interval
    _check_cancelled(cancelled)


@dataclass
class CollectionSnapshot:
    works: list[DouyinWork]
    media: dict[str, list[MediaItem]]
    partial: bool
    created_at: float


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
        'img[src*="qrcode"]',
        'iframe[src*="passport"]',
    ]
    for sel in selectors:
        try:
            if page.locator(sel).first.is_visible():
                return True
        except Exception:
            pass

    try:
        text = page.inner_text("body", timeout=500)
        if "扫码登录" in text or "登录后查看" in text or "请先登录" in text:
            return True
    except Exception:
        pass

    return False


def _wait_for_login(context, page, target_url: str, timeout: int = 180,
                    cancelled: threading.Event | None = None) -> bool:
    """等待用户在浏览器中完成登录，返回是否成功。"""
    logger.info("检测到需要登录，请在弹出的浏览器中扫码登录（最多 %d 秒）...", timeout)
    for i in range(timeout):
        _wait(page, 1000, cancelled)
        cookies = context.cookies("https://www.douyin.com")
        has_session = any(c["name"] in ("sessionid", "sessionid_ss") for c in cookies)
        if has_session:
            if not _check_login_popup(page):
                logger.info("登录成功！（用时 %d 秒）刷新页面...", i)
                page.goto(target_url, wait_until="domcontentloaded", timeout=12000)
                _wait(page, 3000, cancelled)
                return True
        if i % 10 == 0:
            logger.info("  等待扫码登录... (%ds / %ds)", i, timeout)
    return False


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
            page = context.pages[0] if context.pages else context.new_page()
            yield context, page
        finally:
            context.close()


def _has_session(context) -> bool:
    return any(
        cookie["name"] in ("sessionid", "sessionid_ss")
        for cookie in context.cookies("https://www.douyin.com")
    )


def _open_source(context, page, tab: str, *, interactive: bool,
                 cancelled: threading.Event | None = None) -> None:
    _check_cancelled(cancelled)
    if not interactive and not _has_session(context):
        raise _InteractiveBrowserRequired()
    target_url = f"https://www.douyin.com/user/self?showTab={tab}"
    page.goto(target_url, wait_until="domcontentloaded", timeout=12000)
    _wait(page, 2500, cancelled)
    if not _has_session(context) or "passport" in page.url or _check_login_popup(page):
        if not interactive:
            raise _InteractiveBrowserRequired()
        if not _wait_for_login(context, page, target_url, cancelled=cancelled):
            raise ValueError("抖音登录超时，请重新打开登录窗口")


def _account_from_profiles(profiles: list[dict]) -> DouyinSession | None:
    for profile in reversed(profiles):
        user = profile.get("user")
        if not isinstance(user, dict) or not user:
            continue
        avatar_url = next((
            url
            for key in ("avatar_thumb", "avatar_medium")
            for url in ((user.get(key) or {}).get("url_list") or [])
            if isinstance(url, str) and url.startswith("https://")
        ), "")
        account_id = next((
            str(value) for value in (user.get("unique_id"), user.get("short_id"))
            if value and str(value) != "0"
        ), "")
        return DouyinSession(
            logged_in=True, nickname=user.get("nickname") or "",
            account_id=account_id, avatar_url=avatar_url,
        )
    return None


def _sync_session() -> DouyinSession:
    if not Path(_BROWSER_DATA_DIR).is_dir():
        return DouyinSession(logged_in=False)
    with _browser_lock, _open_browser(headless=True) as (context, page):
        if not _has_session(context):
            return DouyinSession(logged_in=False)
        profiles = _capture_pages(page, "/user/profile/self/", "user")
        page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=12000)
        for _ in range(20):
            if account := _account_from_profiles(profiles):
                return account
            if "passport" in page.url:
                return DouyinSession(logged_in=False)
            page.wait_for_timeout(300)
        if not _has_session(context) or _check_login_popup(page):
            return DouyinSession(logged_in=False)
        raise ValueError("暂未读取到抖音登录状态，请重试")


def _sync_login() -> DouyinSession | None:
    from playwright.sync_api import Error

    with _browser_lock, _open_browser(headless=False) as (context, page):
        profiles = _capture_pages(page, "/user/profile/self/", "user")
        try:
            _open_source(context, page, "favorite_collection", interactive=True)
        except Error as exc:
            if page.is_closed():
                raise ValueError("抖音登录窗口已关闭") from exc
            raise
        return _account_from_profiles(profiles)


def _sync_logout() -> None:
    if not Path(_BROWSER_DATA_DIR).is_dir():
        return
    with _browser_lock, _open_browser(headless=True) as (context, page):
        page.goto("about:blank")
        for tab in context.pages:
            if tab != page:
                tab.close()
        context.clear_cookies()


def _read_in_browser(read, cancelled: threading.Event | None = None):
    from playwright.sync_api import Error

    with _browser_lock:
        _check_cancelled(cancelled)
        try:
            with _open_browser(headless=True) as (context, page):
                return read(context, page, interactive=False)
        except (_InteractiveBrowserRequired, Error):
            logger.debug("Opening interactive Douyin browser")
        _check_cancelled(cancelled)
        with _open_browser(headless=False) as (context, page):
            return read(context, page, interactive=True)


def _require_pages(pages: list[dict], interactive: bool, message: str) -> None:
    if not pages:
        if not interactive:
            raise _InteractiveBrowserRequired()
        raise ValueError(message)


def _capture_pages(page, path: str, key: str, *, folder_id: str = "") -> list[dict]:
    pages: list[dict] = []

    def on_response(response):
        url = urlsplit(response.url)
        if url.hostname != "douyin.com" and not (url.hostname or "").endswith(".douyin.com"):
            return
        if path not in url.path:
            return
        try:
            if folder_id:
                ids = parse_qs(url.query).get("collects_id", [])
                if not ids and response.request.method == "POST":
                    post = response.request.post_data_json
                    if isinstance(post, dict):
                        ids = [str(post.get("collects_id", ""))]
                if folder_id not in ids:
                    return
            body = response.json()
            if isinstance(body, dict) and key in body and not body.get("status_code"):
                pages.append(body)
        except Exception:
            logger.debug("Ignoring an unreadable response from %s", url.path)

    page.on("response", on_response)
    return pages


def _scroll_content(page) -> None:
    page.evaluate("""() => {
        const root = document.scrollingElement;
        if (root) root.scrollTop += Math.max(root.clientHeight, 500);
        const containers = document.querySelectorAll('main, [role="main"], [class*="scroll"], [class*="Scroll"], [class*="collect"]');
        for (const element of containers) {
            if (element === root || element.clientHeight < 100 || element.scrollHeight <= element.clientHeight) continue;
            const style = getComputedStyle(element);
            if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                element.scrollTop += Math.max(element.clientHeight, 400);
            }
        }
    }""")


def _scroll_for_responses(page, pages: list[dict], max_scrolls: int,
                          cancelled: threading.Event | None) -> None:
    idle = 0
    for _ in range(max_scrolls):
        _check_cancelled(cancelled)
        if pages and pages[-1].get("has_more") in (0, False, "0"):
            break
        before = len(pages)
        _scroll_content(page)
        _wait(page, 2200, cancelled)
        idle = 0 if len(pages) > before else idle + 1
        if idle == 4:
            break


def _named_controls(page, *names: str):
    return [
        page.get_by_role(role, name=name, exact=True)
        for name in names for role in ("tab", "button", "link")
    ] + [page.get_by_text(name, exact=True) for name in names]


def _click_visible(locators, cancelled: threading.Event | None) -> bool:
    from playwright.sync_api import TimeoutError

    for group in locators:
        for node in group.all()[:12]:
            _check_cancelled(cancelled)
            if not node.is_visible():
                continue
            try:
                node.click(timeout=850)
                return True
            except TimeoutError:
                continue
    return False


def _open_collection_tab(page, cancelled: threading.Event | None) -> bool:
    controls = _named_controls(page, "收藏夹", "我的收藏夹")
    for _ in range(8):
        if _click_visible(controls, cancelled):
            _wait(page, 700, cancelled)
            return True
        _wait(page, 250, cancelled)
    if _click_visible(_named_controls(page, "收藏"), cancelled):
        for _ in range(8):
            if _click_visible(controls, cancelled):
                _wait(page, 700, cancelled)
                return True
            _wait(page, 250, cancelled)
    return False


def _open_folder(page, folder_id: str, folder_name: str,
                 cancelled: threading.Event | None) -> list[dict] | None:
    from playwright.sync_api import TimeoutError

    if not _open_collection_tab(page, cancelled):
        return None

    card_selector = 'ul[data-e2e="scroll-list"] > li'
    cards = page.locator(card_selector)
    controls = [page.locator(
        f'{card_selector}[data-collects-id="{folder_id}"], {card_selector}[data-folder-id="{folder_id}"]'
    )]
    if folder_name:
        controls.append(cards.filter(has=page.get_by_text(folder_name, exact=True)))
        label = re.compile(rf"^{re.escape(folder_name)}\s*\d+\s*(?:个|条)?\s*(?:作品|视频)?$")
        controls.append(cards.filter(has=page.get_by_text(label)))

    pages = None
    for _ in range(12):
        _check_cancelled(cancelled)
        for group in controls:
            matches = [card for card in group.all() if card.is_visible()]
            if len(matches) != 1:
                continue
            if pages is None:
                pages = _capture_pages(page, "/collects/video", "aweme_list", folder_id=folder_id)
            try:
                matches[0].click(timeout=1500)
                return pages
            except TimeoutError:
                continue
        _scroll_content(page)
        _wait(page, 500, cancelled)
    return None


def _sync_fetch_collects_list() -> list[dict]:
    def read(context, page, *, interactive):
        pages = _capture_pages(page, "/collects/list", "collects_list")
        _open_source(context, page, "favorite_collection", interactive=interactive)
        if not pages:
            _open_collection_tab(page, None)
        _scroll_for_responses(page, pages, 15, None)
        _require_pages(pages, interactive, "抖音未返回收藏夹列表，请检查登录状态后刷新")
        return pages

    folders: dict[str, dict] = {}
    for data in _read_in_browser(read):
        for item in data.get("collects_list") or []:
            folder_id = str(item.get("collects_id") or "")
            if not folder_id.isdigit():
                continue
            folders[folder_id] = {
                "id": folder_id,
                "name": item.get("collects_name") or "未命名收藏夹",
                "count": item.get("total_number", item.get("vcount", item.get("total_count"))),
            }
    return list(folders.values())


def _sync_fetch_douyin_page(
    source_type: str, folder_name: str = "", cancelled: threading.Event | None = None,
) -> tuple[list[dict], bool, str]:
    is_folder = source_type.startswith("collects_")
    folder_id = source_type.removeprefix("collects_") if is_folder else ""

    def read(context, page, *, interactive):
        endpoint = "/aweme/favorite" if source_type == "like" else "/listcollection"
        pages = [] if is_folder else _capture_pages(page, endpoint, "aweme_list")
        _open_source(
            context, page, "like" if source_type == "like" else "favorite_collection",
            interactive=interactive, cancelled=cancelled,
        )

        if is_folder:
            opened = _open_folder(page, folder_id, folder_name, cancelled)
            if opened is None:
                if not interactive:
                    raise _InteractiveBrowserRequired()
                raise ValueError("在抖音收藏夹列表中未找到此收藏夹，请返回并刷新收藏夹")
            pages = opened
        elif source_type == "favorite" and not pages:
            _click_visible(_named_controls(page, "全部收藏", "全部"), cancelled)

        _scroll_for_responses(page, pages, 70, cancelled)
        message = (
            "已打开收藏夹，但抖音未返回作品，请刷新作品重试"
            if is_folder else "抖音未返回此来源的作品，请刷新后重试"
        )
        _require_pages(pages, interactive, message)
        return pages, context.cookies("https://www.douyin.com")

    pages, cookies = _read_in_browser(read, cancelled)
    awemes = [aweme for data in pages for aweme in data.get("aweme_list") or []]
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return awemes, bool(pages[-1].get("has_more") not in (None, 0, False, "0")), cookie_str


def _group_works(awemes: list[dict]) -> tuple[list[DouyinWork], dict[str, list[MediaItem]]]:
    works: list[DouyinWork] = []
    media: dict[str, list[MediaItem]] = {}
    for aweme in awemes:
        work_id = str(aweme.get("aweme_id") or "")
        if not re.fullmatch(r"[0-9]+", work_id) or work_id in media:
            continue
        title = (aweme.get("desc") or "").strip() or "未命名作品"
        author = (aweme.get("author") or {}).get("nickname") or "未知作者"
        basename = f"{_sanitize_name(author)[:36]}_{_sanitize_name(title)[:60]}_{work_id}"
        video = aweme.get("video") or {}
        cover = next((
            url for key in ("cover", "origin_cover", "dynamic_cover")
            for url in (video.get(key) or {}).get("url_list", [])
            if isinstance(url, str) and url.startswith("https://")
        ), "")
        images = _extract_images(aweme)
        if images:
            kind = MediaType.IMAGE
            cover = cover or images[0]
            files = [
                MediaItem(
                    url=url,
                    media_type=kind,
                    source_id=f"{work_id}_img_{idx}",
                    filename=f"{basename}_{idx + 1:02}.jpg",
                )
                for idx, url in enumerate(images)
            ]
        else:
            url = _extract_no_watermark_video_url(video)
            if not url:
                continue
            kind = MediaType.VIDEO
            files = [MediaItem(url=url, media_type=kind, source_id=work_id, filename=f"{basename}.mp4")]
        media[work_id] = files
        works.append(DouyinWork(
            id=work_id,
            title=title,
            author=author,
            cover_url=cover,
            kind=kind,
            file_count=len(files),
            duration_seconds=round((video.get("duration") or 0) / 1000) if kind == MediaType.VIDEO else 0,
        ))
    return works, media


class DouyinProvider(BaseProvider):
    name = "douyin"
    display_name = "抖音"
    description = "浏览收藏夹、收藏和点赞，下载选中的作品"
    _snapshot_ttl = 20 * 60
    _snapshot_limit = 8

    def __init__(self) -> None:
        self._browser_cookie = ""
        self._snapshots: OrderedDict[str, CollectionSnapshot] = OrderedDict()
        self._session_cache: tuple[float, DouyinSession] | None = None
        self._session_lock = asyncio.Lock()

    async def login(self) -> None:
        account = await asyncio.get_running_loop().run_in_executor(_executor, _sync_login)
        self._session_cache = (monotonic(), account) if account else None
        self._browser_cookie = ""
        self._snapshots.clear()

    async def logout(self) -> DouyinSession:
        async with self._session_lock:
            await asyncio.get_running_loop().run_in_executor(_executor, _sync_logout)
            account = DouyinSession(logged_in=False)
            self._session_cache = (monotonic(), account)
            self._browser_cookie = ""
            self._snapshots.clear()
            return account

    async def get_session(self) -> DouyinSession:
        async with self._session_lock:
            cached = self._session_cache
            if cached and monotonic() - cached[0] < 60:
                return cached[1]
            account = await asyncio.get_running_loop().run_in_executor(_executor, _sync_session)
            self._session_cache = (monotonic(), account)
            return account

    async def parse_target(self, url_or_id: str) -> TargetInfo:
        source, _, folder_name = url_or_id.strip().partition("|")
        if re.fullmatch(r"collects_[0-9]+", source):
            display = f"收藏夹-{_sanitize_name(folder_name or source.removeprefix('collects_'))}"
        elif source in ("favorite", "like"):
            display = "我的收藏" if source == "favorite" else "我的点赞"
        else:
            raise ValueError("请选择抖音收藏夹、我的收藏或我的点赞")
        return TargetInfo(
            provider=self.name, target_id=source, display_name=display,
            extra={"source_type": source, "folder_name": folder_name},
        )

    async def fetch_collects_list(self) -> list[dict]:
        return await asyncio.get_running_loop().run_in_executor(
            _executor, _sync_fetch_collects_list
        )

    async def _load_works(
        self, target: TargetInfo, cancelled: threading.Event | None = None,
    ) -> CollectionSnapshot:
        awemes, has_more, cookie = await asyncio.get_running_loop().run_in_executor(
            _executor, _sync_fetch_douyin_page, target.extra["source_type"],
            target.extra["folder_name"], cancelled,
        )
        _check_cancelled(cancelled)
        if cookie:
            self._browser_cookie = cookie
        works, media = _group_works(awemes)
        return CollectionSnapshot(works, media, has_more, monotonic())

    async def read_works(
        self, target: TargetInfo, cancelled: threading.Event | None = None,
    ) -> CollectionSnapshot:
        snapshot = await self._load_works(target, cancelled)
        self._snapshots[target.target_id] = snapshot
        self._snapshots.move_to_end(target.target_id)
        while len(self._snapshots) > self._snapshot_limit:
            self._snapshots.popitem(last=False)
        return snapshot

    def cached_selection(self, target: TargetInfo, work_ids: list[str]) -> list[MediaItem] | None:
        snapshot = self._snapshots.get(target.target_id)
        if not snapshot or monotonic() - snapshot.created_at >= self._snapshot_ttl:
            return None
        if not all(work_id in snapshot.media for work_id in work_ids):
            return None
        return [item for work_id in work_ids for item in snapshot.media[work_id]]

    async def fetch_selection(self, target: TargetInfo, work_ids: list[str]) -> list[MediaItem]:
        await self.read_works(target)
        media = self.cached_selection(target, work_ids)
        if media is None:
            raise ValueError("部分选中的作品已不在当前来源，请刷新后重新选择")
        return media

    async def fetch_media_list(
        self,
        target: TargetInfo,
        *,
        start_page: int = 1,
        end_page: int = 0,
        progress_callback: Any = None,
    ) -> list[MediaItem]:
        snapshot = await self._load_works(target)
        return [item for items in snapshot.media.values() for item in items]

    def get_download_headers(self) -> dict[str, str]:
        headers = {"Referer": "https://www.douyin.com/"}
        if self._browser_cookie:
            headers["Cookie"] = self._browser_cookie
        return headers
