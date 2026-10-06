"""報名/取消報名：「確定」才送出，預覽時查不到活動資料也不會被登出。用假的 session 跟假的模型
跑 supervisor 的一輪對話，不會連到學校系統，也不會呼叫 OpenAI：
    python -m pytest test_supervisor_confirmation.py
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 supervisor_agent 時建立模型物件要有值

from langchain_core.messages import AIMessage  # noqa: E402

import supervisor_agent  # noqa: E402

PENDING_REGISTER = {"type": "ACTIVITY_REGISTER", "activity_id": "2294", "session_id": "event115A01727"}


class FakeSession:
    def __init__(self):
        self.calls = []

    async def register_for_activity_session(self, activity_id, session_id=None, confirm=False):
        self.calls.append(("register", activity_id, session_id, confirm))
        return {"message": "報名成功（正取），序號：1"}

    async def cancel_activity_registration(self, activity_id, session_id=None, confirm=False):
        self.calls.append(("cancel", activity_id, session_id, confirm))
        return {"message": "已取消報名。"}


class FakeLLM:
    """模型直接回一句話、不呼叫工具。"""

    def __init__(self):
        self.called = False

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.called = True
        return AIMessage(content="好，那先不要送出。")


def _run_turn(monkeypatch, user_input, pending_action):
    session, llm = FakeSession(), FakeLLM()

    async def fake_get_session(username, password):
        return session

    monkeypatch.setattr(supervisor_agent, "get_or_create_session", fake_get_session)
    monkeypatch.setattr(supervisor_agent, "llm_smart", llm)

    async def collect():
        return [e async for e in supervisor_agent._agent_turn_events(user_input, "113000000", "", pending_action, "無")]

    events = asyncio.run(collect())
    return session, llm, events[-1]


def test_confirmation_submits_the_previewed_session(monkeypatch):
    session, llm, final = _run_turn(monkeypatch, "確定報名", PENDING_REGISTER)
    assert session.calls == [("register", "2294", "event115A01727", True)]
    assert not llm.called
    assert final["pending_action"] == {}


def test_hesitation_containing_the_word_does_not_submit(monkeypatch):
    for text in ("我還不確定", "確定要報名嗎", "先不要，我確定一下時間"):
        session, llm, final = _run_turn(monkeypatch, text, PENDING_REGISTER)
        assert session.calls == [], text
        assert llm.called, text


def test_public_lookup_failure_during_preview_keeps_the_login_session(monkeypatch):
    # 查公開活動資料失敗（學校網站一時連不上）跟登入無關，清掉 session 的話使用者要重新登入
    import agent_tools

    resets = []

    async def fake_get_session(username, password):
        return FakeSession()

    async def fake_reset(username):
        resets.append(username)

    def unreachable(keyword):
        raise ConnectionError("cis.ncu.edu.tw 連不上")

    monkeypatch.setattr(agent_tools, "get_or_create_session", fake_get_session)
    monkeypatch.setattr(agent_tools, "reset_session", fake_reset)
    monkeypatch.setattr(agent_tools, "find_activity_id", unreachable)
    tools = {t.name: t for t in agent_tools.build_tools("113000000", "", "無")}

    result = asyncio.run(tools["preview_activity_registration"].ainvoke({"keyword": "印度文化日"}))
    assert "查詢活動資料時發生錯誤" in result["content"]
    assert "pending_action" not in result
    assert resets == []


def test_confirm_word_without_a_pending_registration_goes_to_the_model(monkeypatch):
    # 上一輪是活動推薦（等使用者說「繼續」），這時說「確定」不是在確認報名
    pending = {"type": "ACTIVITY_RECOMMEND", "deficiencies": [], "next_indices": {}}
    session, llm, _ = _run_turn(monkeypatch, "確定", pending)
    assert session.calls == []
    assert llm.called
