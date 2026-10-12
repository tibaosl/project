"""登入用 Chrome 的背景登入：不需要使用者動手時用看不到的視窗，要自己登入時才開看得到的。

Portal 頁面用 Playwright 的 set_content 假造，不會真的連到 Portal，也不會開真正的 Chrome。
"""

import asyncio
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

from backend.ncu import action_tools
from backend.ncu.action_tools import NCUSession

# Portal「記住我」生效時的登入頁：只剩帳號跟登入按鈕，按下去就進首頁
REMEMBERED_PAGE = """<html><body>
<input aria-label="帳號" value="student">
<button onclick="document.body.innerHTML = '<a>學生服務</a>'">登入 Portal</button>
</body></html>"""

# 沒有記住帳號：要使用者自己輸入密碼
PASSWORD_PAGE = """<html><body>
<input aria-label="帳號"><input aria-label="密碼">
<button>登入 Portal</button>
</body></html>"""

# 按了登入之後跳出人機驗證
CHALLENGE_AFTER_CLICK_PAGE = """<html><body>
<input aria-label="帳號" value="student">
<button onclick="document.body.innerHTML = '<iframe title=&quot;reCAPTCHA&quot;></iframe>'">登入 Portal</button>
</body></html>"""


async def _hidden_login_result(html):
    session = NCUSession("", "")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            return await session._wait_for_chrome_portal_login(page, allow_manual=False), await page.content()
        finally:
            await browser.close()


def test_hidden_window_logs_in_with_the_remembered_account():
    ok, content = asyncio.run(_hidden_login_result(REMEMBERED_PAGE))
    assert ok
    assert "學生服務" in content


def test_hidden_window_gives_up_when_a_password_is_needed():
    ok, _ = asyncio.run(_hidden_login_result(PASSWORD_PAGE))
    assert not ok


def test_hidden_window_gives_up_when_a_challenge_appears_after_clicking():
    ok, _ = asyncio.run(_hidden_login_result(CHALLENGE_AFTER_CLICK_PAGE))
    assert not ok


def _fake_chrome(monkeypatch, needs_manual: bool):
    """記錄每次開的 Chrome 是不是看不到的視窗，看不到的視窗依 needs_manual 決定能不能登入。"""
    opened = []

    @asynccontextmanager
    async def fake_page(chrome, hidden=False):
        opened.append(hidden)
        yield FakeContext(), FakePage()

    async def fake_wait(self, page, allow_manual=True):
        return allow_manual or not needs_manual

    monkeypatch.setattr(action_tools, "_login_chrome_page", fake_page)
    monkeypatch.setattr(NCUSession, "_wait_for_chrome_portal_login", fake_wait)
    return opened


class FakeContext:
    async def storage_state(self):
        return {"cookies": [{"name": "portal"}], "origins": []}


class FakePage:
    async def evaluate(self, expression):
        return "Mozilla/5.0 Chrome"


def test_remembered_login_never_shows_a_window(monkeypatch):
    opened = _fake_chrome(monkeypatch, needs_manual=False)
    state, user_agent, _ = asyncio.run(NCUSession("", "")._portal_login_in_chrome("chrome.exe"))
    assert opened == [True]
    assert state["cookies"] and user_agent


def test_manual_login_reopens_a_visible_window(monkeypatch):
    opened = _fake_chrome(monkeypatch, needs_manual=True)
    asyncio.run(NCUSession("", "")._portal_login_in_chrome("chrome.exe"))
    assert opened == [True, False]


def test_password_fallback_goes_straight_to_a_visible_window(monkeypatch):
    # 有密碼表示背景帳密登入已經被驗證擋下，一定要使用者動手，不用先試看不到的視窗
    opened = _fake_chrome(monkeypatch, needs_manual=True)
    monkeypatch.setattr(action_tools, "_find_chrome", lambda: "chrome.exe")
    asyncio.run(NCUSession("student", "secret")._login_with_real_chrome())
    assert opened == [False]


def test_hidden_chrome_is_placed_off_screen(monkeypatch):
    launched = []

    class FakeProcess:
        def poll(self):
            return 0  # 已經結束，下次開不用等

    def fake_popen(args):
        launched.append(args)
        return FakeProcess()

    class FakeChromium:
        async def connect_over_cdp(self, url):
            return "browser"

    class FakePlaywright:
        chromium = FakeChromium()

    monkeypatch.setattr(action_tools.subprocess, "Popen", fake_popen)
    browser, process = asyncio.run(action_tools._launch_login_chrome(FakePlaywright(), "chrome.exe", hidden=True))
    asyncio.run(action_tools._launch_login_chrome(FakePlaywright(), "chrome.exe", hidden=False))

    assert browser == "browser" and isinstance(process, FakeProcess)
    assert "--window-position=-32000,-32000" in launched[0]
    assert "--disable-backgrounding-occluded-windows" in launched[0]
    # 看得到的視窗要明確放回螢幕上：Chrome 會記住上次關閉時的位置（螢幕外）
    assert "--window-position=80,60" in launched[1]


def _chrome_login_events(monkeypatch, start):
    """用假的 NCUSession 呼叫 /api/login/chrome，回傳串流裡的事件跟用過的 session。"""
    import json
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 main 時建立模型物件要有值
    from fastapi.testclient import TestClient

    from backend import main

    sessions = []

    class FakeSession:
        def __init__(self, username, password):
            self.username, self.closed = username, False
            sessions.append(self)

        start_via_chrome = start

        async def close(self):
            self.closed = True

    async def identity(code):
        return {"identifier": "113000000", "chinese_name": "王小明"}

    async def adopt(username, session):
        pass

    monkeypatch.setattr(main, "NCUSession", FakeSession)
    monkeypatch.setattr(main.oauth_portal, "build_authorization_url", lambda: "https://portal.example/auth")
    monkeypatch.setattr(main.oauth_portal, "consume_state", lambda state: state == "s1")
    monkeypatch.setattr(main.oauth_portal, "exchange_code_for_identity", identity)
    monkeypatch.setattr(main, "adopt_session", adopt)
    monkeypatch.setattr(main, "start_prewarm", lambda username, session: None)

    response = TestClient(main.app, base_url="http://127.0.0.1:8000").post("/api/login/chrome")
    events = [json.loads(chunk[5:]) for chunk in response.text.split("\n\n") if chunk.startswith("data:")]
    return events, sessions


def test_chrome_login_streams_progress_then_the_token(monkeypatch):
    # 登入要等好幾秒，邊做邊回報目前在做什麼，登入頁才不會只顯示一句話空等
    async def start(self, authorization_url, redirect_uri, report=None):
        report("正在背景登入 Portal")
        report("已登入 Portal，正在確認身分")
        return f"{redirect_uri}?code=abc&state=s1"

    events, _ = _chrome_login_events(monkeypatch, start)
    assert [e["text"] for e in events if e["type"] == "status"] == ["正在背景登入 Portal", "已登入 Portal，正在確認身分"]
    assert events[-1]["type"] == "result"
    assert (events[-1]["username"], events[-1]["chinese_name"]) == ("113000000", "王小明")
    assert events[-1]["token"]


def test_chrome_login_failure_ends_the_stream_with_an_error(monkeypatch):
    async def start(self, authorization_url, redirect_uri, report=None):
        raise RuntimeError("3 分鐘內沒有在 Chrome 視窗完成登入。")

    events, sessions = _chrome_login_events(monkeypatch, start)
    assert events == [{"type": "error", "message": "登入失敗：3 分鐘內沒有在 Chrome 視窗完成登入。"}]
    assert sessions[0].closed
