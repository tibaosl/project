"""下載行事曆檔的 API（main.export_calendar）。不連學校系統：課表、報名紀錄、校曆都換成假資料。
    python -m pytest tests/test_calendar_export_api.py
"""

import os
from datetime import date, timedelta
from urllib.parse import unquote

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 main 時建立模型物件要有值

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.analysis.academic_calendar import CalendarEvent  # noqa: E402
from tests.test_academic_calendar import REGISTRATIONS, SCHEDULE  # noqa: E402

TODAY = date.today()
# 今天剛好在學期中，課表才會排進去
EVENTS = [
    CalendarEvent(TODAY - timedelta(days=30), TODAY - timedelta(days=30), "全校學生註冊、開始上課", "other"),
    CalendarEvent(TODAY + timedelta(days=10), TODAY + timedelta(days=12), "期中成績評量", "exam"),
    CalendarEvent(TODAY + timedelta(days=60), TODAY + timedelta(days=60), "寒假開始", "holiday"),
]


def _client(monkeypatch, schedule=SCHEDULE, registrations=REGISTRATIONS, user="110000000"):
    async def calendar():
        return EVENTS

    async def session(username, password):
        return object() if username == user else None

    async def fetch(_session):
        return schedule, registrations

    resets = []

    async def reset(username):
        resets.append(username)

    monkeypatch.setattr(main, "load_calendar_safely", calendar)
    monkeypatch.setattr(main, "resolve_session_token", lambda token: user if token == "good" else None)
    monkeypatch.setattr(main, "get_or_create_session", session)
    monkeypatch.setattr(main, "fetch_schedule_and_registrations", fetch)
    monkeypatch.setattr(main, "reset_session", reset)
    return TestClient(main.app, base_url="http://127.0.0.1:8000"), resets


def test_guests_get_the_campus_calendar(monkeypatch):
    client, _ = _client(monkeypatch)
    res = client.post("/api/calendar/export", json={})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")
    assert res.headers["x-calendar-contents"] == "calendar"
    assert unquote(res.headers["content-disposition"]).endswith("中央大學校曆.ics")
    assert "SUMMARY:期中成績評量" in res.text and "CATEGORIES:課程" not in res.text
    assert res.headers["cache-control"] == "no-store"


def test_logged_in_users_also_get_classes_and_activities(monkeypatch):
    client, _ = _client(monkeypatch)
    res = client.post("/api/calendar/export", json={"token": "good"})
    assert res.status_code == 200
    assert res.headers["x-calendar-contents"] == "calendar,classes,activities"
    assert unquote(res.headers["content-disposition"]).endswith("我的中央大學行事曆.ics")
    assert "SUMMARY:資料結構" in res.text and "SUMMARY:人本AI論壇" in res.text


def test_expired_tokens_are_not_treated_as_guests(monkeypatch):
    client, _ = _client(monkeypatch)
    res = client.post("/api/calendar/export", json={"token": "expired"})
    assert res.status_code == 401
    assert "重新登入" in res.json()["detail"]


def test_missing_schedule_still_exports_what_was_found(monkeypatch):
    client, _ = _client(monkeypatch, schedule=None)
    res = client.post("/api/calendar/export", json={"token": "good"})
    assert res.status_code == 200
    assert res.headers["x-calendar-contents"] == "calendar,activities"


def test_nothing_could_be_fetched_resets_the_session(monkeypatch):
    client, resets = _client(monkeypatch, schedule=None, registrations=None)
    res = client.post("/api/calendar/export", json={"token": "good"})
    assert res.status_code == 502
    assert resets == ["110000000"]


def test_cross_site_requests_are_rejected(monkeypatch):
    client, _ = _client(monkeypatch)
    res = client.post("/api/calendar/export", json={"token": "good"}, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
