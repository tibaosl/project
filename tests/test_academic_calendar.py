"""校曆解析（backend/analysis/academic_calendar.py）跟我的行程（backend/analysis/agenda.py）。
表格照 115 學年度校曆實際的格式手寫，不需要 data/：
    python -m pytest tests/test_academic_calendar.py
"""

from datetime import date

from backend.analysis import academic_calendar as ac
from backend.analysis import agenda

CALENDAR = """
| 國立中央大學115學年度第1學期校曆 第1頁 |  |  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 年 | 月 | 週 | 日 | 一 | 二 | 三 | 四 | 五 | 六 | 記 事 |
| 115 年 | 9 月 |  |  |  | 1 | 2 | 3 | 4 | 5 | 1日起 碩博生新生入住 2日～16日 加退選 |
| 115 年 |  | 1 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 7日 全校學生註冊、開始上課 |
| 115 年 | 10 月 | 5 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 9日 國慶日補假1日 10日 國慶日放假1日 |
| 115 年 |  | 7 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 19日～11月27日 受理課程停修申請 |
| 115 年 |  | 8 | 25 | 26 | 27 | 28 | 29 | 30 | 31 | 27日～30日 期中成績評量(評量時間自行安排) |
| 115 年 | 11 月 | 10 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 11日 全校運動會(全校停課) |
| 115 年 | 12 月 | 15 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 14日～116年1月15日 受理舊生學雜費減免申請 18日 第1學期休學申請截止 |
| 115 年 |  | 16 | 20 | 21 | 22 | 23 | 24 | 25 | 26 | 25日 行憲紀念日放假1日 ； 寒假開始 |
| 116 年 | 1 月 |  |  |  |  |  |  | 1 | 2 | 1日 開國紀念日放假1日 |
"""


def _events():
    return ac.parse_calendar_text(CALENDAR)


def _find(events, text):
    return next(e for e in events if text in e.title)


def test_single_days_ranges_and_since():
    events = _events()
    assert _find(events, "加退選").start == date(2026, 9, 2) and _find(events, "加退選").end == date(2026, 9, 16)
    assert _find(events, "開始上課").start == date(2026, 9, 7)
    assert _find(events, "碩博生新生入住").title == "碩博生新生入住（9/1 起）"


def test_ranges_across_months_and_years():
    events = _events()
    assert _find(events, "停修").end == date(2026, 11, 27)
    fee = _find(events, "學雜費減免")
    assert (fee.start, fee.end) == (date(2026, 12, 14), date(2027, 1, 15))
    assert _find(events, "開國紀念日").start == date(2027, 1, 1)


def test_semicolons_split_events_on_the_same_day_and_holiday_counts_are_not_entries():
    events = _events()
    christmas = [e.title for e in events if e.start == date(2026, 12, 25)]
    assert christmas == ["寒假開始", "行憲紀念日放假1日"]
    # 「放假1日」裡的「1日」不是新的一筆
    assert not any(e.start == date(2026, 10, 1) for e in events)


def test_event_kinds_and_no_class_days():
    events = _events()
    assert _find(events, "期中").kind == "exam"
    assert _find(events, "加退選").kind == "course"
    assert _find(events, "休學申請截止").kind == "deadline"
    assert ac.no_class_reason(events, date(2026, 10, 9)) == "國慶日補假1日"
    assert ac.no_class_reason(events, date(2026, 11, 11)) == "全校運動會(全校停課)"
    assert ac.no_class_reason(events, date(2026, 10, 12)) == ""
    assert not _find(events, "寒假開始").no_class  # 寒假靠上課期間判斷，不是停課事件


def test_semester_class_period_runs_until_the_day_before_the_break():
    events = _events()
    assert ac.semester_class_period(events, date(2026, 10, 12)) == (date(2026, 9, 7), date(2026, 12, 24))
    assert ac.semester_class_period(events, date(2026, 9, 3)) is None  # 還沒開始上課
    assert ac.semester_class_period(events, date(2026, 12, 28)) is None  # 寒假


def test_search_uses_common_phrases():
    events = _events()
    assert [e.title for e in ac.search_events(events, "期中考")] == ["期中成績評量(評量時間自行安排)"]
    assert {e.title for e in ac.search_events(events, "什麼時候放假")} >= {"國慶日放假1日", "國慶日補假1日"}
    assert ac.search_events(events, "") == []


def test_events_between_lists_long_events_only_on_their_first_and_last_day():
    events = _events()
    titles = [e.title for e in ac.events_between(events, date(2026, 10, 20), date(2026, 10, 26))]
    assert "受理課程停修申請" not in titles  # 10/19 開始、11/27 結束，中間這週不列
    assert [e.title for e in ac.events_between(events, date(2026, 11, 23), date(2026, 11, 29))] == ["受理課程停修申請"]


def test_period_range_weeks_start_on_monday():
    saturday = date(2026, 10, 10)
    assert ac.period_range("this_week", saturday) == (saturday, date(2026, 10, 11))
    assert ac.period_range("next_week", saturday) == (date(2026, 10, 12), date(2026, 10, 18))
    assert ac.period_range("tomorrow", saturday) == (date(2026, 10, 11),) * 2
    assert ac.period_range("next_month", saturday) == (date(2026, 11, 1), date(2026, 11, 30))


def test_latest_calendar_file_picks_the_newest_year(tmp_path):
    folder = tmp_path / ac.CALENDAR_FOLDER
    folder.mkdir()
    for name in ("114 學年度校曆.pdf", "115 學年度校曆.pdf", "115學年度行事曆說明.pdf"):
        (folder / name).write_bytes(b"")
    assert ac.latest_calendar_file(tmp_path).name == "115 學年度校曆.pdf"
    assert ac.latest_calendar_file(tmp_path / "沒有這個資料夾") is None


# ============================================================
# 我的行程
# ============================================================
SCHEDULE = [
    {"day": "星期一", "period": "第二節", "time": "09:00 09:50", "details": ["資料結構", "工程五館 A204", "王老師"]},
    {"day": "星期一", "period": "第三節", "time": "10:00 10:50", "details": ["資料結構", "工程五館 A204", "王老師"]},
    {"day": "星期一", "period": "第七節", "time": "", "details": ["作業系統", "工程五館 A101"]},
    {"day": "星期五", "period": "第Ａ節", "time": "18:00 18:50", "details": ["夜間課"]},
]
REGISTRATIONS = [
    {"活動名稱": "人本AI論壇", "場次名稱": "AI時代的關鍵能力", "活動地點": "國鼎圖書館",
     "活動場次時間": "開始：2026-10-12 15:00 結束：2026-10-12 17:00", "報名狀態": "正取"},
    {"活動名稱": "舊活動", "活動場次時間": "開始：2024-11-01 10:00 結束：2024-11-01 11:00"},
]


def test_consecutive_periods_of_the_same_course_are_merged():
    classes = agenda.classes_on(SCHEDULE, 0)
    assert [(c["time"], c["title"]) for c in classes] == [("09:00-10:50", "資料結構"), ("15:00-15:50", "作業系統")]
    assert classes[0]["detail"] == "工程五館 A204｜王老師"
    assert agenda.classes_on(SCHEDULE, 4)[0]["time"] == "18:00-18:50"  # 半形 A 也對得上全形節次


def test_agenda_skips_classes_on_holidays_and_lists_activities_and_deadlines():
    events = _events()
    result = agenda.build_agenda(date(2026, 10, 9), date(2026, 10, 12), events, SCHEDULE, REGISTRATIONS,
                                 "我的行程", today=date(2026, 10, 10))
    days = {d["date"]: d for d in result["days"]}
    assert result["kind"] == "personal_agenda" and len(days) == 4
    assert days["2026-10-09"]["holiday"] == "國慶日補假1日" and days["2026-10-09"]["classes"] == []
    assert days["2026-10-10"]["is_today"]
    monday = days["2026-10-12"]
    assert [c["title"] for c in monday["classes"]] == ["資料結構", "作業系統"]
    assert monday["activities"][0]["time"] == "15:00-17:00" and monday["activities"][0]["place"] == "國鼎圖書館"
    assert result["notes"] == []


def test_agenda_outside_the_class_period_and_missing_data_are_explained():
    events = _events()
    result = agenda.build_agenda(date(2026, 12, 28), date(2026, 12, 28), events, SCHEDULE, None, "我的行程")
    assert result["days"][0]["classes"] == []  # 寒假
    assert any("上課期間" in note for note in result["notes"])
    assert any("報名紀錄" in note for note in result["notes"])

    result = agenda.build_agenda(date(2026, 11, 27), date(2026, 11, 27), events, None, [], "我的行程")
    assert result["days"][0]["events"] == [{"title": "受理課程停修申請", "kind": "course", "note": "最後一天"}]
    assert any("課表" in note for note in result["notes"])


def test_course_code_lines_use_the_course_name_as_the_title():
    schedule = [{"day": "星期一", "period": "第七節", "time": "15:00 15:50", "details": ["CE3007-A", "計算機網路", "E6-A204"]}]
    assert agenda.classes_on(schedule, 0) == [
        {"time": "15:00-15:50", "title": "計算機網路", "detail": "E6-A204｜CE3007-A", "periods": ["第七節"]},
    ]


def test_staff_only_events_are_left_out_of_period_listings_and_the_agenda():
    events = ac.parse_calendar_text(CALENDAR.replace(
        "9日 國慶日補假1日 10日 國慶日放假1日", "9日 國慶日補假1日 10日 國慶日放假1日 ； 115學年度第1次校務發展委員會"
    ))
    titles = [e.title for e in ac.events_between(events, date(2026, 10, 5), date(2026, 10, 11))]
    assert titles == ["國慶日補假1日", "國慶日放假1日"]
    assert agenda.calendar_items_on(events, date(2026, 10, 10)) == [{"title": "國慶日放假1日", "kind": "holiday", "note": ""}]
    # 用關鍵字找還是找得到，「教師節」也不會被當成教師的事
    assert ac.search_events(events, "委員會")
    assert ac.CalendarEvent(date(2026, 9, 28), date(2026, 9, 28), "教師節放假1日", "holiday").for_students
