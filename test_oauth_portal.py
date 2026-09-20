"""oauth_portal.py 的純邏輯檢查：state 管理、授權網址組裝。

不會真的打 portal.ncu.edu.tw（token 交換/使用者資訊那段需要真的 client_id/
client_secret 和一次真實的授權流程，沒辦法在這裡測，需要真帳號在瀏覽器裡
跑一次 /api/oauth/login 才能驗證，見 main.py 的 oauth_login/oauth_callback）。

用 pytest 執行：pytest test_oauth_portal.py
"""

import pytest

import oauth_portal


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(oauth_portal, "CLIENT_ID", "test-client-id")
    monkeypatch.setattr(oauth_portal, "CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(oauth_portal, "_pending_states", {})


def test_not_configured_raises_clear_error(monkeypatch):
    monkeypatch.setattr(oauth_portal, "CLIENT_ID", "")
    monkeypatch.setattr(oauth_portal, "CLIENT_SECRET", "")
    assert oauth_portal.is_configured() is False
    with pytest.raises(RuntimeError):
        oauth_portal.build_authorization_url()


def test_authorization_url_contains_client_id_and_a_fresh_state():
    url = oauth_portal.build_authorization_url()
    assert url.startswith(oauth_portal.AUTHORIZATION_URL)
    assert "client_id=test-client-id" in url
    assert "state=" in url
    assert len(oauth_portal._pending_states) == 1


def test_state_can_only_be_consumed_once():
    url = oauth_portal.build_authorization_url()
    state = url.split("state=")[-1]

    assert oauth_portal.consume_state(state) is True
    assert oauth_portal.consume_state(state) is False, "同一個 state 不該能重複使用"


def test_unknown_state_is_rejected():
    assert oauth_portal.consume_state("this-was-never-issued") is False
    assert oauth_portal.consume_state("") is False


def test_expired_state_is_rejected(monkeypatch):
    import time

    url = oauth_portal.build_authorization_url()
    state = url.split("state=")[-1]

    # 把這個 state 的建立時間往回撥到超過 TTL，模擬「太久沒用」。
    oauth_portal._pending_states[state] -= oauth_portal._STATE_TTL_SECONDS + 1
    assert oauth_portal.consume_state(state) is False


def test_build_return_url_with_and_without_query():
    assert oauth_portal.build_return_url("/login") == f"{oauth_portal.FRONTEND_ORIGIN}/login"

    url = oauth_portal.build_return_url("/oauth-complete", token="abc123", username="alice")
    assert url.startswith(f"{oauth_portal.FRONTEND_ORIGIN}/oauth-complete?")
    assert "token=abc123" in url
    assert "username=alice" in url


def test_build_return_url_drops_empty_values():
    url = oauth_portal.build_return_url("/login", oauth_error="", username="alice")
    assert "oauth_error" not in url
    assert "username=alice" in url
