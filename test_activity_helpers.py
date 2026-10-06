"""activity_tools.py 裡不用連網路的小工具：場次額滿、報名/取消報名時找出是哪一個場次、
使用者是不是真的同意送出、報名紀錄的排序，還有打學校網站用的 TLS 驗證。資料是照 2026-10
用真實帳號看到的格式手寫的：
    python -m pytest test_activity_helpers.py
"""

import ssl
from datetime import datetime

import pytest
import requests

import activity_tools as a
import secure_requests


@pytest.mark.parametrize("text, full", [
    ("報名人數：145 / 145 備取人數：0 / 0", True),
    ("報名人數：1 / 1 備取人數：1 / 1", True),
    ("報名人數：10 / 10", True),  # 沒寫備取就是沒有備取名額
    ("報名人數：20 / 20 備取人數：2 / 5", False),  # 還能備取
    ("報名人數：3 / 15 備取人數：0 / 5", False),
    ("報名人數：0 / 0", False),  # 沒設上限
    ("", False),
])
def test_session_full(text, full):
    assert a.session_full(text) is full


@pytest.mark.parametrize("text, confirmed", [
    ("確定", True),
    ("確定報名", True),
    ("確定取消報名", True),
    ("好，確定！", True),
    ("不錯，確定報名", True),
    ("我還不確定", False),
    ("不太確定耶", False),
    ("確定嗎？", False),
    ("確定要報名嗎", False),
    ("確定不要報名了", False),
    ("先不要，我確定一下時間", False),
    ("算了", False),
    ("好", False),
])
def test_is_confirmation(text, confirmed):
    assert a.is_confirmation(text) is confirmed


def test_names_match_ignores_spaces_punctuation_width_and_filler_words():
    assert a.names_match("客家學院那場", "客家學院學生出國說明會")
    assert a.names_match("ＡＩ 論壇", "115-1人本AI論壇")
    assert a.names_match("115-1人本AI論壇的場次", "人本AI論壇")
    assert not a.names_match("", "人本AI論壇")
    assert not a.names_match("印度文化日", "人本AI論壇")


# 報名時間用很久以後的日期，測試不會因為哪天跑而變成「已截止」
SESSIONS = [
    {"session_id": "e1", "session_name": "人本AI論壇：AI與人類心智：從幽默、情緒到認知神經科學",
     "event_period": "2026-10-27 10:00 ~ 2026-10-27 12:00", "signup_period": "2026-09-22 09:00 ~ 2099-10-23 12:00"},
    {"session_id": "e2", "session_name": "人本AI論壇：AI時代的關鍵能力與藍海策略",
     "event_period": "2026-11-17 15:00 ~ 2026-11-17 17:00", "signup_period": "2026-09-22 09:00 ~ 2099-11-13 12:00"},
]


@pytest.mark.parametrize("hint, session_id", [
    ("藍海策略", "e2"),
    ("AI時代的關鍵能力那場", "e2"),
    ("11/17", "e2"),
    ("10月27日那場", "e1"),
    ("2026-10-27", "e1"),
])
def test_choose_session_by_name_or_date(hint, session_id):
    chosen, _ = a.choose_session(SESSIONS, hint)
    assert chosen["session_id"] == session_id


def test_choose_session_asks_when_it_cannot_tell():
    for hint in ("", "人本AI論壇", "12/25"):  # 沒說、兩場名稱都有、對不上
        chosen, candidates = a.choose_session(SESSIONS, hint)
        assert chosen is None
        assert [s["session_id"] for s in candidates] == ["e1", "e2"]


def test_choose_session_without_hint_skips_closed_sessions():
    closed = {**SESSIONS[0], "signup_period": "2026-09-01 12:00 ~ 2026-09-02 12:00"}
    chosen, _ = a.choose_session([closed, SESSIONS[1]])
    assert chosen["session_id"] == "e2"


def test_single_session_is_chosen_even_if_hint_does_not_match():
    chosen, _ = a.choose_session(SESSIONS[:1], "下午那場")
    assert chosen["session_id"] == "e1"


REGISTRATIONS = [
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "客家學院學生出國說明會",
     "活動場次時間": "開始：2026-10-13 12:00 結束：2026-10-13 13:00", "功能": "取消報名 檢視活動資訊"},
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "資電學院學生出國說明會",
     "活動場次時間": "開始：2026-09-30 12:00 結束：2026-09-30 13:00", "功能": "檢視活動資訊"},
    {"活動名稱": "大一CPR實體技術考試", "場次名稱": "113.11.1(五)第五場次(10:00~11:00)",
     "活動場次時間": "開始：2024-11-01 10:00 結束：2024-11-01 11:00", "功能": "檢視活動資訊"},
]


def _sessions(records):
    return [r.get("場次名稱") for r in records]


def test_match_registrations_by_session_or_activity_name():
    # 使用者常講的是場次名稱，不是活動名稱
    assert _sessions(a.match_registrations(REGISTRATIONS, "客家學院學生出國說明會")) == ["客家學院學生出國說明會"]
    assert len(a.match_registrations(REGISTRATIONS, "出國講座")) == 2
    assert _sessions(a.match_registrations(REGISTRATIONS, "出國講座", "10/13")) == ["客家學院學生出國說明會"]
    assert _sessions(a.match_registrations(REGISTRATIONS, "出國講座", "資電")) == ["資電學院學生出國說明會"]
    assert a.match_registrations(REGISTRATIONS, "印度文化日") == []


def test_sort_registrations_puts_upcoming_first():
    unknown = {"活動名稱": "看不懂時間", "活動場次時間": ""}
    upcoming, past = a.sort_registrations(REGISTRATIONS + [unknown], now=datetime(2026, 10, 6, 4, 0))
    assert _sessions(upcoming) == ["客家學院學生出國說明會"]
    assert _sessions(past) == ["資電學院學生出國說明會", "113.11.1(五)第五場次(10:00~11:00)", None]


# ============================================================
# 活動查詢打 cis.ncu.edu.tw 用的 TLS 驗證（secure_requests.py）
# ============================================================
def test_non_strict_adapter_still_verifies_chain_and_hostname():
    adapter = secure_requests.NonStrictX509Adapter()
    context = adapter.poolmanager.connection_pool_kw["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_strict_failure_switches_host_to_non_strict_verification(monkeypatch):
    calls = []

    def strict_get(url, verify=True, **kwargs):
        calls.append(("strict" if verify else "unverified", url))
        raise requests.exceptions.SSLError("Missing Subject Key Identifier")

    def non_strict_get(url, **kwargs):
        calls.append(("non-strict", url))
        return "ok"

    monkeypatch.setattr(secure_requests.requests, "get", strict_get)
    monkeypatch.setattr(secure_requests._non_strict_session, "get", non_strict_get)
    monkeypatch.setattr(secure_requests, "_non_strict_hosts", set())

    assert secure_requests.get_with_fallback("https://cis.example.test/a") == "ok"
    assert secure_requests.get_with_fallback("https://cis.example.test/b") == "ok"
    # 第二次直接用放寬嚴格檢查的連線，不再先失敗一次，也不會退到不驗證
    assert calls == [
        ("strict", "https://cis.example.test/a"),
        ("non-strict", "https://cis.example.test/a"),
        ("non-strict", "https://cis.example.test/b"),
    ]
