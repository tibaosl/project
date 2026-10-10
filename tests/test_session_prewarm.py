"""登入後在背景預熱 iNCU（agent_tools.start_prewarm）。用假的 session，不連學校系統：
    python -m pytest tests/test_session_prewarm.py
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 agent_tools 時建立模型物件要有值

from backend.agent import agent_tools  # noqa: E402


class FakePage:
    def __init__(self, log):
        self.log = log

    async def goto(self, url, wait_until=None):
        self.log.append(("goto", url))


class FakeSession:
    def __init__(self, log, delay=0.05, error=None):
        self.log, self.delay, self.error = log, delay, error

    async def open_incu_home(self):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.log.append("open_incu_home")
        return FakePage(self.log)

    async def _handle_oauth_consent_if_present(self, page):
        self.log.append("consent")

    async def close(self):
        self.log.append("close")


def _reset_globals(monkeypatch, user, session):
    monkeypatch.setattr(agent_tools, "_sessions", {user: session})
    monkeypatch.setattr(agent_tools, "_session_locks", {})
    monkeypatch.setattr(agent_tools, "_prewarm_tasks", {})


def test_tools_wait_until_the_prewarm_is_done(monkeypatch):
    # 工具取得 session 時預熱還在跑，要等它做完，兩邊才不會同時操作同一個 iNCU 分頁
    log = []
    session = FakeSession(log)
    _reset_globals(monkeypatch, "110000000", session)

    async def scenario():
        agent_tools.start_prewarm("110000000", session)
        got = await agent_tools.get_or_create_session("110000000", "")
        log.append("tool")
        return got

    assert asyncio.run(scenario()) is session
    assert log == ["open_incu_home", ("goto", agent_tools.INCU_MY_ACTIVITIES_URL), "consent", "tool"]
    assert agent_tools._prewarm_tasks == {}


def test_failed_prewarm_does_not_block_or_break_later_queries(monkeypatch):
    log = []
    session = FakeSession(log, error=RuntimeError("iNCU 連不上"))
    _reset_globals(monkeypatch, "110000000", session)

    async def scenario():
        agent_tools.start_prewarm("110000000", session)
        return await agent_tools.get_or_create_session("110000000", "")

    assert asyncio.run(scenario()) is session
    assert "open_incu_home" not in log


def test_slow_prewarm_gives_up_after_the_timeout(monkeypatch):
    log = []
    session = FakeSession(log, delay=5)
    _reset_globals(monkeypatch, "110000000", session)
    monkeypatch.setattr(agent_tools, "PREWARM_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        agent_tools.start_prewarm("110000000", session)
        return await asyncio.wait_for(agent_tools.get_or_create_session("110000000", ""), timeout=2)

    assert asyncio.run(scenario()) is session
    assert log == []


def test_reset_session_cancels_a_running_prewarm(monkeypatch):
    log = []
    session = FakeSession(log, delay=5)
    _reset_globals(monkeypatch, "110000000", session)

    async def scenario():
        agent_tools.start_prewarm("110000000", session)
        task = agent_tools._prewarm_tasks["110000000"]
        await asyncio.sleep(0)
        await agent_tools.reset_session("110000000")
        await asyncio.sleep(0)
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()
    assert log == ["close"]
    assert agent_tools._prewarm_tasks == {}


def test_a_cancelled_waiter_does_not_cancel_the_prewarm(monkeypatch):
    # 前端斷線、等待的請求被取消時，預熱照樣做完，下一個問題就不用再等
    log = []
    session = FakeSession(log, delay=0.1)
    _reset_globals(monkeypatch, "110000000", session)

    async def scenario():
        agent_tools.start_prewarm("110000000", session)
        task = agent_tools._prewarm_tasks["110000000"]
        waiter = asyncio.ensure_future(agent_tools.wait_for_prewarm("110000000"))
        await asyncio.sleep(0.01)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        await task
        return task

    task = asyncio.run(scenario())
    assert not task.cancelled()
    assert "consent" in log
