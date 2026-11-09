"""使用本地平台样例验证登录、下载落盘和重试。"""

import asyncio
import os
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from playwright.async_api import async_playwright, expect
from playwright.sync_api import sync_playwright
import uvicorn

from app.database import Database
from app.models import DouyinSession, MediaItem, TargetInfo
from app.providers.douyin import DouyinProvider
from app.providers.weibo import WeiboProvider
import app.main as main
import app.providers.douyin as douyin_reader
import app.providers.weibo as weibo_reader


def verify_browser_reader():
    folder_name = "音乐"
    modes = []
    stored_session = False
    tab_reads = []

    def respond(route):
        url = urlsplit(route.request.url)
        if url.path == "/user/self":
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body=f"""<meta charset="utf-8">
                <button role="tab" onclick="fetch('/api/music/listcollection')">音乐</button>
                <button role="tab" onclick="document.getElementById('folders').hidden=false; fetch('/api/collects/list'); fetch('/api/collects/video?collects_id=50&preview=1')">收藏夹</button>
                <ul id="folders" data-e2e="scroll-list" hidden>
                  <li onclick="fetch('/api/collects/video?collects_id=500'); fetch('/api/collects/video?collects_id=50')"><p>{folder_name}</p></li>
                </ul>
                <button role="tab" onclick="fetch('/api/listcollection')">全部收藏</button>""",
            )
        elif url.path == "/api/collects/list":
            tab_reads.append("collections")
            route.fulfill(json={
                "collects_list": [{"collects_id": "50", "collects_name": folder_name, "total_number": 1}],
                "has_more": 0,
            })
        elif url.path == "/api/collects/video":
            query = parse_qs(url.query)
            work_id = "101" if query["collects_id"] == ["50"] and "preview" not in query else "999"
            route.fulfill(json={"aweme_list": [{"aweme_id": work_id}], "has_more": 0})
        elif url.path == "/api/music/listcollection":
            tab_reads.append("music")
            route.fulfill(json={"mc_list": [], "has_more": 0})
        elif url.path == "/api/listcollection":
            tab_reads.append("favorite")
            route.fulfill(json={"aweme_list": [{"aweme_id": "202"}], "has_more": 0})
        else:
            route.abort()

    @contextmanager
    def fixture_browser(*, headless):
        nonlocal stored_session
        modes.append(headless)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            if not headless:
                stored_session = True
            if stored_session:
                context.add_cookies([{"name": "sessionid", "value": "fixture", "url": "https://www.douyin.com"}])
            page = context.new_page()
            page.route("**/*", respond)
            try:
                yield context, page
            finally:
                browser.close()

    with patch.object(douyin_reader, "_open_browser", fixture_browser):
        assert douyin_reader._sync_fetch_collects_list() == [{"id": "50", "name": folder_name, "count": 1}]
        douyin_reader._sync_login()
        awemes, partial, cookie = douyin_reader._sync_fetch_douyin_page("collects_50", folder_name)
        favorites, _, _ = douyin_reader._sync_fetch_douyin_page("favorite")
    assert modes == [True, False, False, True, True], modes
    assert tab_reads == ["collections", "collections", "favorite"], tab_reads
    assert favorites == [{"aweme_id": "202"}]
    assert awemes == [{"aweme_id": "101"}] and not partial and "sessionid=fixture" in cookie
    print("PASS: same-name tab ignored; folder preview and unrelated folder excluded")


def verify_weibo_login():
    config_url = "https://m.weibo.cn/api/config"
    login_url = "https://passport.weibo.com/sso/signin"
    signed_in = False
    opened = []

    def respond(route):
        nonlocal signed_in
        url = route.request.url
        if url == config_url:
            opened.append(url)
            route.fulfill(json={"data": {"login": signed_in, "loginUrl": login_url}})
        elif url == login_url:
            opened.append(url)
            signed_in = True
            route.fulfill(
                content_type="text/html",
                body=f"<script>setTimeout(() => location.assign('{config_url}'), 150)</script>",
            )
        else:
            route.abort()

    @contextmanager
    def fixture_browser(*, headless):
        assert not headless
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            page.route("**/*", respond)
            try:
                yield context, page
            finally:
                browser.close()

    with patch.object(weibo_reader, "_open_browser", fixture_browser):
        weibo_reader._sync_login()
    assert opened == [config_url, login_url, config_url], opened
    print("PASS: Weibo sign-in entry and confirmed session")


def verify_douyin_logout():
    with TemporaryDirectory(prefix="media-collector-logout-") as profile:
        with patch.object(douyin_reader, "_BROWSER_DATA_DIR", profile):
            with douyin_reader._open_browser(headless=True) as (context, _):
                context.add_cookies([{
                    "name": "sessionid", "value": "fixture",
                    "url": "https://www.douyin.com", "expires": time() + 3600,
                }])
                assert douyin_reader._has_session(context)
            with douyin_reader._open_browser(headless=True) as (context, _):
                assert douyin_reader._has_session(context)
            douyin_reader._sync_logout()
            assert not douyin_reader._sync_session().logged_in
    print("PASS: logout clears a saved browser session")


async def run():
    requests: list[str] = []
    failures = {"102", "201"}

    async def serve_media(request):
        name = request.match_info["name"]
        requests.append(name)
        if name in failures:
            return web.Response(status=403)
        return web.Response(body=f"fixture-{name}".encode())

    media_app = web.Application()
    media_app.router.add_get("/media/{name}", serve_media)
    runner = web.AppRunner(media_app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    media_root = f"http://127.0.0.1:{runner.addresses[0][1]}/media/"
    pictures = ["山野短片", "城市漫步", "收藏图集", "晚霞录像"]
    slow_favorites = threading.Event()
    reading = threading.Event()
    stopped = threading.Event()

    def awemes(source, folder, cancelled=None):
        assert source in ("collects_50", "favorite"), source
        if source == "collects_50":
            assert folder == "周末灵感", folder
        elif slow_favorites.is_set():
            reading.set()
            assert cancelled and cancelled.wait(5), "Read did not stop on disconnect"
            stopped.set()
            douyin_reader._check_cancelled(cancelled)
        output = []
        for number, title in enumerate(pictures, 101):
            row = {
                "aweme_id": str(number),
                "desc": title,
                "author": {"nickname": "样例作者"},
                "video": {
                    "duration": 9800,
                    "cover": {"url_list": [f"https://covers.example.invalid/{number}"]},
                    "play_addr": {"url_list": [media_root + str(number)]},
                },
            }
            if number == 103:
                row["images"] = [{"url_list": [media_root + name]} for name in ("201", "202")]
            output.append(row)
        return output + [output[0]], False, ""

    async def cover(route):
        colors = {"101": "#596d94", "102": "#685783", "103": "#a37864", "104": "#54736d"}
        color = colors.get(route.request.url.rsplit("/", 1)[-1], "#686884")
        await route.fulfill(
            content_type="image/svg+xml",
            body=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 240"><rect width="360" height="240" fill="{color}"/><circle cx="265" cy="77" r="37" fill="#f2e2cd" opacity=".8"/><path d="M0 240V185L105 83l92 86 72-38 91 77v32Z" fill="#222538" opacity=".48"/></svg>',
        )

    folder = {"id": "50", "name": "周末灵感", "count": 4}
    weibo = TargetInfo(provider="weibo", target_id="42", display_name="微博测试账号")
    weibo_media = [MediaItem(url=media_root + "weibo", source_id="w1", filename="微博原图.jpg")]
    screenshots = os.environ.get("MEDIACOLLECTOR_SMOKE_SCREENSHOTS")

    async def choose_path(panel, path):
        trigger = panel.get_by_role("button", name="选择下载文件夹")
        await trigger.click()
        await expect(trigger).to_be_enabled()
        if path:
            await expect(panel.locator(".save-card code")).to_have_text(str(path))
        else:
            await expect(panel.locator(".save-card")).to_contain_text("请选择下载文件夹")

    with TemporaryDirectory(prefix="media-collector-smoke-") as directory:
        root = Path(directory)
        douyin_path = root / "抖音归档"
        output = root / "周末精选"
        weibo_output = root / "微博归档"
        for path in (douyin_path, output, weibo_output):
            path.mkdir()
        chooser = AsyncMock(side_effect=[None, str(douyin_path), str(output), None, str(weibo_output)])
        douyin_login = AsyncMock(return_value=None)
        account = DouyinSession(
            logged_in=True, nickname="测试账号", account_id="fixture_user",
            avatar_url="https://covers.example.invalid/avatar",
        )
        signed_out = DouyinSession(logged_in=False)
        douyin_session = AsyncMock(side_effect=[signed_out, account, account, signed_out])
        douyin_logout = AsyncMock(return_value=signed_out)
        weibo_login = AsyncMock(side_effect=[ValueError("微博登录窗口已关闭"), None])
        db_path = root / "catalog.sqlite"
        with (
            patch.object(main, "db", Database(str(db_path))),
            patch("app.api.providers.choose_folder", new=chooser),
            patch("app.providers.douyin._sync_fetch_collects_list", return_value=[folder]),
            patch("app.providers.douyin._sync_fetch_douyin_page", side_effect=awemes) as source_calls,
            patch.object(DouyinProvider, "login", new=douyin_login),
            patch.object(DouyinProvider, "get_session", new=douyin_session),
            patch.object(DouyinProvider, "logout", new=douyin_logout),
            patch.object(WeiboProvider, "login", new=weibo_login),
            patch.object(WeiboProvider, "parse_target", new=AsyncMock(return_value=weibo)),
            patch.object(WeiboProvider, "fetch_media_list", new=AsyncMock(return_value=weibo_media)),
            socket.socket() as listener,
        ):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning"))
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(.05)
                assert server.started
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    context = await browser.new_context(viewport={"width": 1280, "height": 900})
                    await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
                    page = await context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.route("https://covers.example.invalid/**", cover)
                    await page.goto(base_url)
                    douyin = page.get_by_role("tabpanel", name="抖音")
                    status = douyin.locator(".download-status")
                    await expect(douyin.get_by_role("heading", level=1)).to_have_text("我的收藏")
                    await douyin.get_by_role("button", name="登录抖音", exact=True).click()
                    avatar = douyin.get_by_role("button", name="当前抖音账号")
                    await expect(avatar).to_be_visible()
                    await expect(avatar.locator("img")).to_have_js_property("complete", True)
                    assert await avatar.locator("img").evaluate("image => image.naturalWidth > 0")
                    await expect(douyin.get_by_role("button", name="登录抖音", exact=True)).to_have_count(0)
                    douyin_login.assert_awaited_once()
                    await expect(douyin.locator("textarea")).to_have_count(0)
                    await expect(douyin.locator(".save-card")).to_contain_text("请选择下载文件夹")
                    assert (await douyin.locator(".save-card").bounding_box())["height"] <= 64
                    await choose_path(douyin, None)
                    await choose_path(douyin, douyin_path)
                    await douyin.get_by_role("button", name="读取收藏夹", exact=True).click()
                    await douyin.get_by_role("button", name="周末灵感").click()
                    await expect(douyin.get_by_role("checkbox")).to_have_count(4)
                    rejected = await page.request.post(
                        base_url + "/api/providers/douyin/downloads",
                        data={"source": "collects_50|周末灵感", "work_ids": ["999"]},
                    )
                    assert rejected.status == 400
                    await expect(douyin.get_by_role("heading", level=1)).to_have_text("周末灵感")
                    await expect(douyin.locator(".save-card code")).to_have_text(str(douyin_path))
                    await expect(douyin.get_by_role("button", name="下载选中作品")).to_be_disabled()
                    await douyin.get_by_role("button", name="视频", exact=True).click()
                    await expect(douyin.get_by_role("checkbox")).to_have_count(3)
                    search = douyin.get_by_role("searchbox")
                    await search.fill("城市")
                    await expect(douyin.get_by_role("checkbox")).to_have_count(1)
                    await douyin.get_by_role("checkbox", name="选择 城市漫步").check()
                    await search.clear()
                    await douyin.get_by_role("checkbox", name="选择 山野短片").check()
                    await douyin.get_by_role("button", name="图集", exact=True).click()
                    await douyin.get_by_role("checkbox", name="选择 收藏图集").check()
                    await douyin.get_by_role("button", name="全部", exact=True).click()
                    await expect(douyin.locator(".selection-info")).to_contain_text("共 4 个文件")
                    await choose_path(douyin, output)
                    await expect(douyin.locator(".selection-info")).to_contain_text("共 4 个文件")
                    await douyin.get_by_role("button", name="复制保存路径").click()
                    await expect(douyin.get_by_text("已复制", exact=True)).to_be_visible()
                    assert await page.evaluate("navigator.clipboard.readText()") == str(output)
                    if screenshots:
                        await page.screenshot(path=str(Path(screenshots) / "mediacollector-works.png"))
                    await page.set_viewport_size({"width": 390, "height": 844})
                    assert await page.evaluate("document.documentElement.scrollWidth === innerWidth")
                    await choose_path(douyin, output)
                    if screenshots:
                        await page.screenshot(path=str(Path(screenshots) / "mediacollector-mobile.png"), full_page=True)
                    await page.set_viewport_size({"width": 1280, "height": 900})

                    await douyin.get_by_role("button", name="下载选中作品").click()
                    await expect(status).to_contain_text("已保存 2 个文件，2 个失败")
                    assert sorted(requests) == ["101", "102", "201", "202"], requests
                    assert source_calls.call_count == 1, "Initial download should reuse the browse snapshot"
                    assert sorted(file.read_bytes() for file in output.iterdir()) == [b"fixture-101", b"fixture-202"]
                    assert not list(douyin_path.iterdir())

                    await status.get_by_role("button", name="失败详情").click()
                    await expect(status).to_contain_text("城市漫步")
                    await expect(status).to_contain_text("HTTP 403")
                    failures.clear()
                    await status.get_by_role("button", name="重试失败项").click()
                    await expect(status).to_contain_text("已保存 4 个文件")
                    assert sorted(requests) == ["101", "102", "102", "201", "201", "202"], requests
                    assert source_calls.call_count == 2
                    assert len(list(output.iterdir())) == 4

                    await douyin.get_by_role("button", name="返回收藏").click()
                    await expect(douyin.get_by_role("button", name="周末灵感")).to_be_visible()
                    tab = page.get_by_role("tab", name="抖音")
                    await tab.focus()
                    await tab.press("ArrowRight")
                    weibo_panel = page.get_by_role("tabpanel", name="微博")
                    await expect(weibo_panel.get_by_role("heading", level=1)).to_have_text("微博主页")
                    await expect(weibo_panel.locator(".save-card")).to_contain_text("请选择下载文件夹")
                    weibo_button = weibo_panel.get_by_role("button", name="登录微博", exact=True)
                    await weibo_button.click()
                    await expect(weibo_panel.get_by_text("微博登录窗口已关闭", exact=True)).to_be_visible()
                    await weibo_button.click()
                    await expect(weibo_panel.get_by_text("登录成功", exact=True)).to_be_visible()
                    assert weibo_login.await_count == 2
                    await expect(weibo_panel.locator("textarea")).to_have_count(0)
                    await weibo_panel.get_by_label("主页链接或用户 ID").fill("42")
                    await expect(weibo_panel.get_by_role("button", name="下载微博原图")).to_be_disabled()
                    blocked = await page.request.post(base_url + "/api/tasks", data={"target_url": "42"})
                    assert blocked.status == 400 and (await blocked.json())["detail"] == "请先选择下载文件夹"
                    await choose_path(weibo_panel, weibo_output)
                    await weibo_panel.get_by_label("主页链接或用户 ID").fill("42")
                    await weibo_panel.get_by_role("button", name="下载微博原图").click()
                    await expect(weibo_panel.locator(".download-status")).to_contain_text("已保存 1 个文件")
                    assert (weibo_output / "微博原图.jpg").read_bytes() == b"fixture-weibo"

                    await page.reload()
                    await expect(avatar).to_be_visible()
                    await expect(douyin.get_by_role("button", name="登录抖音", exact=True)).to_have_count(0)
                    await expect(douyin.locator(".download-status")).to_contain_text("已保存 4 个文件")
                    await expect(douyin.locator(".save-card code")).to_have_text(str(douyin_path))
                    await douyin.get_by_role("button", name="周末灵感").click()
                    await expect(douyin.locator(".save-card code")).to_have_text(str(output))
                    assert source_calls.call_count == 2, "Reopening a folder should use the catalog"
                    main.task_manager.get_provider("douyin")._snapshots.clear()
                    await douyin.get_by_role("checkbox", name="选择 晚霞录像").check()
                    await douyin.get_by_role("button", name="下载选中作品").click()
                    await expect(douyin.locator(".download-status")).to_contain_text("已保存 1 个文件")
                    assert requests[-1] == "104" and source_calls.call_count == 3
                    assert any(file.read_bytes() == b"fixture-104" for file in output.iterdir())

                    await douyin.get_by_role("button", name="返回收藏").click()
                    favorites_button = douyin.get_by_role("button", name="全部收藏")
                    slow_favorites.set()
                    await favorites_button.click()
                    assert await asyncio.to_thread(reading.wait, 4)
                    await expect(douyin.locator(".save-card code")).to_have_text(str(douyin_path))
                    await douyin.get_by_role("button", name="停止读取并返回").click()
                    await expect(favorites_button).to_be_visible()
                    assert await asyncio.to_thread(stopped.wait, 4)
                    assert await main.db.get_douyin_catalog("favorite") is None
                    slow_favorites.clear()

                    await favorites_button.click()
                    await expect(douyin.get_by_role("checkbox")).to_have_count(4)
                    reads_after_first = source_calls.call_count
                    await douyin.get_by_role("button", name="返回收藏").click()
                    await favorites_button.click()
                    await expect(douyin.get_by_role("checkbox")).to_have_count(4)
                    assert source_calls.call_count == reads_after_first
                    await douyin.get_by_role("button", name="刷新作品").click()
                    await expect(douyin.get_by_role("button", name="刷新作品")).to_be_enabled()
                    assert source_calls.call_count == reads_after_first + 1
                    await page.get_by_role("tab", name="微博").click()
                    await expect(weibo_panel.locator(".save-card code")).to_have_text(str(weibo_output))
                    assert [call.args[0] for call in chooser.await_args_list] == [
                        "", "", str(douyin_path), str(output), "",
                    ]
                    saved = Database(str(db_path))
                    await saved.init()
                    try:
                        for source in ("favorite", "collects_50"):
                            catalog = await saved.get_douyin_catalog(source)
                            assert len(catalog["works"]) == 4
                    finally:
                        await saved.close()

                    await tab.click()
                    await douyin.get_by_role("button", name="返回收藏").click()
                    await avatar.click()
                    await expect(douyin.get_by_text("测试账号", exact=True)).to_be_visible()
                    await expect(douyin.get_by_text("抖音号：fixture_user", exact=True)).to_be_visible()
                    await avatar.press("Escape")
                    await expect(douyin.get_by_role("button", name="退出登录")).to_have_count(0)
                    await avatar.click()
                    await douyin.get_by_role("button", name="退出登录").click()
                    await expect(douyin.get_by_role("button", name="登录抖音", exact=True)).to_be_visible()
                    douyin_logout.assert_awaited_once()
                    await expect(douyin.get_by_role("button", name="周末灵感")).to_have_count(0)
                    await expect(douyin.locator(".save-card code")).to_have_text(str(douyin_path))
                    assert len(list(output.iterdir())) == 5
                    await page.reload()
                    await expect(douyin.get_by_role("button", name="登录抖音", exact=True)).to_be_visible()
                    await expect(avatar).to_have_count(0)
                    assert not errors, errors
                    await browser.close()
            finally:
                server.should_exit = True
                await serving
                await runner.cleanup()

        reopened = Database(str(db_path))
        await reopened.init()
        try:
            for source in ("favorite", "collects_50"):
                assert await reopened.get_douyin_catalog(source) is None
            assert (await reopened.get_provider_config("douyin"))["download_dir"] == str(douyin_path)
        finally:
            await reopened.close()

    print("PASS: independent path display, account menu, logout, retained files and download workflow")


if __name__ == "__main__":
    verify_browser_reader()
    verify_weibo_login()
    verify_douyin_logout()
    asyncio.run(run())
