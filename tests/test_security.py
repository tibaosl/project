"""本機 API 的防護（backend/security.py）跟 main.py 跟登入狀態有關的修正，不會連到學校系統或 OpenAI：
    python -m pytest tests/test_security.py
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 main 時建立模型物件要有值

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import security  # noqa: E402
from backend.agent import agent_tools  # noqa: E402

LOCAL = "http://127.0.0.1:8000"


@pytest.mark.parametrize("host, ok", [
    ("127.0.0.1:8000", True), ("localhost:5173", True), ("LOCALHOST", True), ("[::1]:8000", True),
    ("evil.example", False), ("127.0.0.1.evil.example", False), ("localhost.evil.example:8000", False), ("", False),
])
def test_host_is_local(host, ok):
    assert security.host_is_local(host) is ok


@pytest.mark.parametrize("origin, ok", [
    ("http://localhost:5173", True), ("http://127.0.0.1:8000", True), ("https://localhost", True),
    ("https://evil.example", False), ("null", False), ("file://", False), ("http://localhost.evil.example", False),
])
def test_origin_is_local(origin, ok):
    assert security.origin_is_local(origin) is ok


def _small_app():
    app = FastAPI()
    app.add_middleware(security.LocalOnlyMiddleware)

    @app.post("/action")
    async def action():
        return {"done": True}

    @app.get("/stream")
    async def stream():
        async def events():
            yield "data: 1\n\n"
            yield "data: 2\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def test_dns_rebinding_host_is_rejected():
    client = TestClient(_small_app(), base_url="http://evil.example")
    assert client.post("/action").status_code == 400


def test_cross_site_posts_are_rejected_but_local_pages_and_scripts_are_not():
    client = TestClient(_small_app(), base_url=LOCAL)
    assert client.post("/action", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/action", headers={"Origin": "null"}).status_code == 403
    assert client.post("/action", headers={"Origin": "http://localhost:5173"}).json() == {"done": True}
    assert client.post("/action").json() == {"done": True}  # curl、測試沒有 Origin


def test_security_headers_and_streaming_still_work():
    response = TestClient(_small_app(), base_url=LOCAL).get("/stream")
    assert response.text == "data: 1\n\ndata: 2\n\n"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


# ============================================================
# main.py
# ============================================================
@pytest.fixture
def main_client(monkeypatch):
    from backend import main
    return main, TestClient(main.app, base_url=LOCAL)


def test_cross_site_chrome_login_never_reaches_the_handler(main_client, monkeypatch):
    # 別的網站偷偷 POST /api/login/chrome 的話，在這台電腦開 Chrome 之前就要被擋下來
    main, client = main_client
    opened = []
    monkeypatch.setattr(main, "NCUSession", lambda **kwargs: opened.append(kwargs))
    response = client.post("/api/login/chrome", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert opened == []


def test_logout_uses_the_account_behind_the_token_not_the_request(main_client, monkeypatch):
    main, client = main_client
    resets = []

    async def fake_reset(username):
        resets.append(username)

    monkeypatch.setattr(main, "reset_session", fake_reset)
    # 帶無效 token 跟別人的帳號，不能把別人的登入狀態關掉
    client.post("/api/logout", json={"token": "not-a-token", "username": "113000001"})
    assert resets == []

    token = agent_tools.issue_session_token("113000002")
    client.post("/api/logout", json={"token": token, "username": "someone-else"})
    assert resets == ["113000002"]


def test_too_long_messages_are_rejected_before_the_agent_runs(main_client, monkeypatch):
    main, client = main_client

    async def agent_should_not_run(*args, **kwargs):
        raise AssertionError("不應該執行 agent")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "run_ncuxplore_agent_stream", agent_should_not_run)
    response = client.post("/api/chat/stream", json={"user_message": "字" * (main.MAX_MESSAGE_CHARS + 1)})
    assert "訊息太長" in response.text


def test_conversation_threads_are_bound_to_the_account():
    from backend import main
    assert main._thread_key("113000001", "tab-1") != main._thread_key("113000002", "tab-1")
    assert main._thread_key("", "tab-1") == "guest:tab-1"
    # 沒帶 thread_id 的請求不再共用同一個預設對話
    assert main._thread_key("", "") != main._thread_key("", "")


# ============================================================
# session token
# ============================================================
def test_idle_tokens_expire(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(agent_tools.time, "monotonic", lambda: clock[0])
    token = agent_tools.issue_session_token("113000003")

    clock[0] += agent_tools.TOKEN_IDLE_SECONDS - 60
    assert agent_tools.resolve_session_token(token) == "113000003"  # 用了就重新計時
    clock[0] += agent_tools.TOKEN_IDLE_SECONDS - 60
    assert agent_tools.resolve_session_token(token) == "113000003"

    clock[0] += agent_tools.TOKEN_IDLE_SECONDS + 1
    assert agent_tools.resolve_session_token(token) is None
    assert not agent_tools.has_active_tokens("113000003")
