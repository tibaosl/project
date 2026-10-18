"""「最近要注意」的背景準備（agent/reminder_prep.py）跟 /api/reminders。用假的 session，不連學校系統：
    python -m pytest tests/test_reminder_prep.py
"""

import asyncio
import os
from datetime import date, timedelta

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 agent_tools、main 時建立模型物件要有值

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.agent import agent_tools, reminder_prep  # noqa: E402
from backend.analysis.academic_calendar import CalendarEvent  # noqa: E402

USER = "110000000"


class FakePage:
    def __init__(self, log, name):
        self.log, self.name = log, name

    async def close(self):
        self.log.append(("close", self.name))

    def is_closed(self):
        return False


class FakeSession:
    """iNCU 的方法照 NCUSession 的寫法用 self.incu_page，看背景準備是不是用自己的分頁。"""

    def __init__(self, log, fail=(), relogin=False):
        self.log, self.fail, self.relogin = log, set(fail), relogin
        self.incu_page = FakePage(log, "tools")

    async def open_incu_home(self):
        if self.incu_page is None:
            self.incu_page = FakePage(self.log, "side")
        if self.relogin:  # 被導回 Portal 登入頁
            await self._login_on_page(self.incu_page)
        return self.incu_page

    async def _login_on_page(self, page):
        self.log.append("visible chrome login")

    async def _page(self, part):
        page = await self.open_incu_home()
        self.log.append((part, page.name))
        if part in self.fail:
            raise RuntimeError(f"{part} 抓不到")

    async def get_my_activity_registrations(self):
        await self._page("activities")
        return {"registrations": [{"活動名稱": "職涯講座"}]}

    async def get_hours_dashboard(self):
        await self._page("hours")
        return {"activities": [{"name": "新生電影節"}]}


def _setup(monkeypatch, session):
    monkeypatch.setattr(reminder_prep, "_states", {})
    monkeypatch.setattr(agent_tools, "_sessions", {USER: session} if session else {})
    monkeypatch.setattr(agent_tools, "_session_locks", {})
    monkeypatch.setattr(agent_tools, "_prewarm_tasks", {})

    async def transcript(view, include_graduation=True):
        await view._page("scholarships")
        return {"semesters": []}, None

    monkeypatch.setattr(reminder_prep, "fetch_academic_records", transcript)
    monkeypatch.setattr(reminder_prep, "_eligible_scholarships", lambda transcript: [{"name": "羅家倫獎學金"}])


async def _prepare():
    reminder_prep.start(USER)
    first = reminder_prep.snapshot(USER)
    await reminder_prep._states[USER]["task"]
    return first, reminder_prep.snapshot(USER)


def test_prepares_everything_on_its_own_incu_tab(monkeypatch):
    log = []
    session = FakeSession(log)
    _setup(monkeypatch, session)
    first, done = asyncio.run(_prepare())

    assert first["loading"] == ["activities", "hours", "scholarships"]
    assert done["loading"] == [] and done["failed"] == []
    assert done["registrations"] == [{"活動名稱": "職涯講座"}]
    assert done["hour_activities"] == [{"name": "新生電影節"}]
    assert done["scholarships"] == [{"name": "羅家倫獎學金"}]
    # 用另一個分頁，工具用的分頁沒被動到，抓完把自己的分頁關掉
    assert log == [("activities", "side"), ("hours", "side"), ("scholarships", "side"), ("close", "side")]
    assert session.incu_page.name == "tools"


def test_waits_for_the_prewarm_first(monkeypatch):
    log = []
    session = FakeSession(log)
    _setup(monkeypatch, session)

    async def scenario():
        async def prewarm():
            await asyncio.sleep(0.05)
            log.append("prewarm done")

        agent_tools._prewarm_tasks[USER] = asyncio.get_running_loop().create_task(prewarm())
        await _prepare()

    asyncio.run(scenario())
    assert log[0] == "prewarm done"


def test_a_failed_part_does_not_stop_the_others(monkeypatch):
    log = []
    _setup(monkeypatch, FakeSession(log, fail={"hours"}))
    _, done = asyncio.run(_prepare())
    assert done["failed"] == ["hours"]
    assert done["hour_activities"] is None
    assert done["registrations"] and done["scholarships"]


def test_never_pops_up_a_login_window(monkeypatch):
    # 登入狀態失效時工具會開 Chrome 請使用者登入，背景準備不行（使用者沒有要登入）
    log = []
    _setup(monkeypatch, FakeSession(log, relogin=True))
    _, done = asyncio.run(_prepare())
    assert "visible chrome login" not in log
    assert done["failed"] == ["activities", "hours", "scholarships"]


def test_no_session_means_nothing_to_prepare(monkeypatch):
    _setup(monkeypatch, None)
    _, done = asyncio.run(_prepare())
    assert done["failed"] == ["activities", "hours", "scholarships"] and done["loading"] == []


def test_refreshing_keeps_the_old_data_meanwhile(monkeypatch):
    log = []
    _setup(monkeypatch, FakeSession(log))

    async def scenario():
        await _prepare()
        reminder_prep._states[USER]["finished_at"] -= reminder_prep.REFRESH_SECONDS + 1
        during = reminder_prep.snapshot(USER)  # 資料太舊，在背景重抓
        task = reminder_prep._states[USER]["task"]
        assert not task.done()
        await task
        return during

    during = asyncio.run(scenario())
    assert during["loading"] == [] and during["registrations"] == [{"活動名稱": "職涯講座"}]
    assert log.count(("activities", "side")) == 2


def test_logout_clears_the_data(monkeypatch):
    log = []
    _setup(monkeypatch, FakeSession(log))

    async def scenario():
        reminder_prep.start(USER)
        task = reminder_prep._states[USER]["task"]
        reminder_prep.clear(USER)
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()
    assert USER not in reminder_prep._states


# ---- /api/reminders ----
TODAY = date.today()
EVENTS = [CalendarEvent(TODAY + timedelta(days=1), TODAY + timedelta(days=1), "加退選截止", "deadline")]


def _client(monkeypatch, snapshot):
    async def calendar():
        return EVENTS

    calls = []

    def fake_snapshot(username):
        calls.append(username)
        return snapshot

    monkeypatch.setattr(main, "load_calendar_safely", calendar)
    monkeypatch.setattr(main, "resolve_session_token", lambda token: USER if token == "good" else None)
    monkeypatch.setattr(reminder_prep, "snapshot", fake_snapshot)
    return TestClient(main.app, base_url="http://127.0.0.1:8000"), calls


def test_api_guests_get_the_calendar_only(monkeypatch):
    client, calls = _client(monkeypatch, {})
    data = client.post("/api/reminders", json={}).json()
    assert data["logged_in"] is False and data["status"] == "ready"
    assert [(i["when"], i["title"]) for i in data["items"]] == [("明天", "加退選截止")]
    assert calls == []
    # token 失效也只給校曆，不會拿到別人的資料
    assert client.post("/api/reminders", json={"token": "expired"}).json()["logged_in"] is False


def test_api_logged_in_users_get_what_is_ready(monkeypatch):
    snapshot = {"registrations": None, "hour_activities": None, "scholarships": None,
                "loading": ["activities", "hours", "scholarships"], "failed": []}
    client, calls = _client(monkeypatch, snapshot)
    data = client.post("/api/reminders", json={"token": "good"}).json()
    assert data["logged_in"] is True and data["status"] == "loading"
    assert data["loading"] == ["活動報名紀錄", "時數紀錄", "獎學金"]
    assert [i["title"] for i in data["items"]] == ["加退選截止"]  # 校曆先給
    assert calls == [USER]
