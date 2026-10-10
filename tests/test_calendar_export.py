"""匯出行事曆（backend/analysis/calendar_export.py）。校曆、課表沿用 test_academic_calendar 的假資料：
    python -m pytest tests/test_calendar_export.py
"""

from datetime import date, datetime, timedelta, timezone

from backend.analysis import academic_calendar as ac
from backend.analysis import calendar_export as ce
from tests.test_academic_calendar import CALENDAR, REGISTRATIONS, SCHEDULE

NOW = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)


def _events():
    return ac.parse_calendar_text(CALENDAR)


def _vevents(ics: str) -> list[dict]:
    """把折行接回去，拆成一筆一筆事件（欄位名稱 → 值）。"""
    lines = ics.replace("\r\n ", "").split("\r\n")
    result, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            result.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value
    return result


def test_guest_export_has_only_the_calendar_as_all_day_events():
    ics = ce.build_ics(_events(), today=date(2026, 10, 10), now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\n" not in ics.replace("\r\n", "")  # 一律用 CRLF 換行
    assert "X-WR-CALNAME:中央大學校曆" in ics

    events = {e["SUMMARY"]: e for e in _vevents(ics)}
    add_drop = events["加退選"]
    assert add_drop["DTSTART;VALUE=DATE"] == "20260902"
    assert add_drop["DTEND;VALUE=DATE"] == "20260917"  # 整天事件的結束日是不含的那一天
    assert "CATEGORIES" in add_drop and all("DTSTART" in e or "DTSTART;VALUE=DATE" in e for e in events.values())
    assert not any(e.get("CATEGORIES") == "課程" for e in events.values())


def test_classes_fill_the_term_and_skip_holidays():
    ics = ce.build_ics(_events(), SCHEDULE, today=date(2026, 10, 10), now=NOW)
    classes = [e for e in _vevents(ics) if e.get("CATEGORIES") == "課程"]
    starts = {(e["SUMMARY"], e["DTSTART"]) for e in classes}

    mondays = [date(2026, 9, 7) + timedelta(weeks=i) for i in range(16)]  # 9/7 開始上課，寒假 12/25 開始
    assert sum(1 for e in classes if e["SUMMARY"] == "資料結構") == len(mondays)
    assert ("資料結構", "20260907T010000Z") in starts  # 台北 09:00 = UTC 01:00
    assert ("作業系統", "20260907T070000Z") in starts  # 時間欄空的，用節次補
    assert ("夜間課", "20261009T100000Z") not in starts  # 國慶日補假
    assert ("夜間課", "20261016T100000Z") in starts
    assert not any(e["DTSTART"] < "20260907" or e["DTSTART"] > "20261225" for e in classes)

    first = next(e for e in classes if e["DTSTART"] == "20260907T010000Z")
    assert first["DTEND"] == "20260907T025000Z"  # 連續兩節合併成 09:00-10:50
    assert first["LOCATION"] == "工程五館 A204" and first["DESCRIPTION"] == "王老師"
    assert "X-WR-CALNAME:我的中央大學行事曆" in ics


def test_summer_break_exports_the_next_term_and_winter_break_has_no_classes():
    events = _events()
    assert ce.term_for_export(events, date(2026, 8, 20)) == (date(2026, 9, 7), date(2026, 12, 24))
    assert ce.term_for_export(events, date(2026, 12, 28)) is None  # 假資料裡沒有下學期
    ics = ce.build_ics(events, SCHEDULE, today=date(2026, 12, 28), now=NOW)
    assert not any(e.get("CATEGORIES") == "課程" for e in _vevents(ics))


def test_registered_activities_use_their_session_times():
    ics = ce.build_ics(_events(), registrations=REGISTRATIONS, today=date(2026, 10, 10), now=NOW)
    activity = next(e for e in _vevents(ics) if e["SUMMARY"] == "人本AI論壇")
    assert (activity["DTSTART"], activity["DTEND"]) == ("20261012T070000Z", "20261012T090000Z")
    assert activity["LOCATION"] == "國鼎圖書館"
    assert activity["DESCRIPTION"] == "AI時代的關鍵能力｜正取"


def test_long_lines_are_folded_on_character_boundaries_and_text_is_escaped():
    title = "超長的課程名稱，" * 10 + "含分號;跟反斜線\\"
    schedule = [{"day": "星期一", "period": "第二節", "time": "09:00 09:50", "details": [title, "工程五館 A204"]}]
    ics = ce.build_ics(_events(), schedule, today=date(2026, 10, 10), now=NOW)
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n"))
    ics.encode("utf-8").decode("utf-8")  # 沒有把中文字從中間切開
    summary = next(e["SUMMARY"] for e in _vevents(ics) if e.get("CATEGORIES") == "課程")
    assert summary == title.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def test_uids_stay_the_same_when_exported_again():
    first = ce.build_ics(_events(), SCHEDULE, REGISTRATIONS, today=date(2026, 10, 10), now=NOW)
    again = ce.build_ics(_events(), SCHEDULE, REGISTRATIONS, today=date(2026, 10, 11), now=NOW + timedelta(days=1))
    uids = [e["UID"] for e in _vevents(first)]
    assert len(uids) == len(set(uids))
    assert uids == [e["UID"] for e in _vevents(again)]


def test_export_summary_counts_what_will_be_exported():
    summary = ce.export_summary(_events(), SCHEDULE, REGISTRATIONS, today=date(2026, 10, 10))
    assert summary["term"] == ["2026-09-07", "2026-12-24"]
    assert summary["courses"] == 3 and summary["activities"] == 2
    assert summary["calendar_events"] == sum(1 for e in _events() if e.for_students)
