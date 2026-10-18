"""開場畫面的「最近要注意」：整理校曆、已報名的活動、獎學金截止日、還沒核發的時數（reminders.build_reminders）。
不連學校系統，都是假資料：
    python -m pytest tests/test_reminders.py
"""

import os
from datetime import date, datetime

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 scholarship_tools 時建立模型物件要有值

from backend.analysis.academic_calendar import CalendarEvent  # noqa: E402
from backend.analysis.reminders import build_reminders  # noqa: E402
from backend.ncu.hour_activities import parse_activity_rows  # noqa: E402

NOW = datetime(2026, 10, 18, 17, 0)  # 星期日

EVENTS = [
    CalendarEvent(date(2026, 9, 7), date(2026, 10, 16), "受理弱勢助學補助申請", "other"),  # 已經過了
    CalendarEvent(date(2026, 10, 19), date(2026, 11, 27), "受理課程停修申請", "course"),
    CalendarEvent(date(2026, 9, 30), date(2026, 10, 21), "受理研究生獎助學金申請", "other"),  # 這週最後一天
    CalendarEvent(date(2026, 10, 20), date(2026, 10, 20), "115學年度第1次校務發展委員會", "other"),  # 不是學生的事
    CalendarEvent(date(2026, 10, 24), date(2026, 10, 24), "期中評量週；請留意各課程公告", "exam"),
    CalendarEvent(date(2026, 10, 25), date(2026, 10, 25), "光復節放假1日", "holiday"),  # 超過 7 天
]

REGISTRATIONS = [
    {"活動名稱": "職涯講座", "場次名稱": "職涯講座", "活動地點": "國鼎圖書資料館 3F",
     "活動場次時間": "開始：2026-10-19 12:00 結束：2026-10-19 13:30", "報名狀態": "正取", "簽到/簽退": "未簽到 / 未簽退"},
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "理學院學生出國說明會",
     "活動場次時間": "開始：2026-10-18 16:00 結束：2026-10-18 18:00", "報名狀態": "正取", "簽到/簽退": "已簽到 / 未簽退"},
    {"活動名稱": "取消的活動", "活動場次時間": "開始：2026-10-20 10:00 結束：2026-10-20 11:00", "報名狀態": "已取消"},
    {"活動名稱": "下下週的活動", "活動場次時間": "開始：2026-10-30 10:00 結束：2026-10-30 11:00", "報名狀態": "正取"},
    {"活動名稱": "候補的活動", "活動場次時間": "開始：2026-10-22 18:00 結束：2026-10-22 20:00", "報名狀態": "候補"},
    {"活動名稱": "107影享會115-1 Movie Time", "場次名稱": "新生電影節0923天才狂歡派對：最終章 Freshman Film Festival",
     "活動場次時間": "開始：2026-09-23 18:00 結束：2026-09-23 20:00", "報名狀態": "正取", "簽到/簽退": "已簽到 / 已簽退"},
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "文學院學生出國說明會",
     "活動場次時間": "開始：2026-09-29 12:00 結束：2026-09-29 13:00", "簽到/簽退": "已簽到 / 未簽退"},
    {"活動名稱": "大一週會", "場次名稱": "沒去的週會", "活動場次時間": "開始：2026-09-16 13:00 結束：2026-09-16 15:00",
     "簽到/簽退": "未簽到 / 未簽退"},
    {"活動名稱": "校園導覽", "場次名稱": "校園導覽", "活動場次時間": "開始：2026-09-10 10:00 結束：2026-09-10 11:00",
     "時數標籤": "不提供時數", "簽到/簽退": "已簽到 / 已簽退"},
]

HOUR_ACTIVITIES = parse_activity_rows([
    {"cells": ["107影享會115-1/新生電影節0923天才狂歡派對：最終章", "人文藝術中心", "團體",
               "2026-09-23 18:00  2026-09-23 20:00"], "hours": []},
    {"cells": ["中央為翼，飛向國際｜115-1 出國講座系列/文學院學生出國說明會", "國際事務處", "團體",
               "2026-09-29 12:00  2026-09-29 13:00"], "hours": []},
    {"cells": ["國際文化日", "國際事務處", "團體", "2026-09-20 10:00  2026-09-20 16:00"],
     "hours": [["學習護照", "國際視野", "2", "時數待核發"], ["學習護照", "人文藝術", "0", "時數待核發"]]},
    {"cells": ["只有 0 小時的講座", "學務處", "團體", "2026-09-21 10:00  2026-09-21 11:00"],
     "hours": [["學習護照", "人文藝術", "0", "時數待核發"]]},
    {"cells": ["大一週會/沒去的週會", "學務處", "團體", "2026-09-16 13:00  2026-09-16 15:00"], "hours": []},
    {"cells": ["校園導覽", "招生組", "團體", "2026-09-10 10:00  2026-09-10 11:00"], "hours": []},
    {"cells": ["已經核發的講座", "學務處", "團體", "2026-09-01 10:00  2026-09-01 11:00"],
     "hours": [["學習護照", "大一週會", "2", "已核發"]]},
    {"cells": ["上週的講座", "學務處", "團體", "2026-10-12 10:00  2026-10-12 11:00"], "hours": []},  # 才一週
    {"cells": ["去年的講座", "學務處", "團體", "2025-10-01 10:00  2025-10-01 11:00"], "hours": []},  # 太久以前
])

SCHOLARSHIPS = [
    {"name": "羅家倫校長紀念獎學金（學士班）", "amount": "每名 2 萬元", "deadline": "10/23", "deadline_term": "115-1"},
    {"name": "明天截止的獎學金", "amount": "", "deadline": "10/19", "deadline_term": "115-1"},
    {"name": "已經截止的獎學金", "amount": "1 萬元", "deadline": "10/2", "deadline_term": "115-1"},
    {"name": "下個月才截止的獎學金", "amount": "1 萬元", "deadline": "11/20", "deadline_term": "115-1"},
    {"name": "沒有截止日的獎學金", "amount": "1 萬元", "deadline": "", "deadline_term": ""},
]


def _reminders(**kwargs):
    defaults = dict(registrations=REGISTRATIONS, hour_activities=HOUR_ACTIVITIES, scholarships=SCHOLARSHIPS)
    return build_reminders(NOW, EVENTS, logged_in=True, **{**defaults, **kwargs})


def _titles(result, kind=None):
    return [item["title"] for item in result["items"] if kind is None or item["kind"] == kind]


def test_guests_only_see_the_campus_calendar():
    result = build_reminders(NOW, EVENTS, logged_in=False, registrations=REGISTRATIONS)
    assert {item["kind"] for item in result["items"]} == {"calendar"}
    assert result["status"] == "ready" and result["loading"] == [] and result["notes"] == []
    assert result["range"] == "10/18（日）～10/24（六）"


def test_calendar_lists_events_starting_or_ending_in_the_next_7_days():
    items = [item for item in _reminders()["items"] if item["kind"] == "calendar"]
    assert [(i["when"], i["title"], i["detail"]) for i in items] == [
        ("明天", "受理課程停修申請", "到 11/27（五）"),
        ("10/21（三）", "受理研究生獎助學金申請", "最後一天"),
        ("10/24（六）", "期中評量週，請留意各課程公告", ""),  # 分號換成逗號
    ]
    assert [i["badge"] for i in items] == ["選課", "校曆", "考試"]


def test_upcoming_activities_remind_to_sign_in_and_out():
    items = {item["title"]: item for item in _reminders()["items"] if item["kind"] == "activity"}
    assert list(items) == ["中央為翼，飛向國際｜115-1 出國講座系列", "職涯講座", "候補的活動"]  # 照時間排
    ongoing = items["中央為翼，飛向國際｜115-1 出國講座系列"]
    assert ongoing["when"] == "進行中" and ongoing["hint"] == "離開前記得簽退"
    assert ongoing["detail"] == "理學院學生出國說明會"
    tomorrow = items["職涯講座"]
    assert tomorrow["when"] == "明天 12:00"
    assert tomorrow["detail"] == "國鼎圖書資料館 3F"  # 場次名稱跟活動名稱一樣就不重複
    assert "記得簽到、簽退" in tomorrow["hint"]
    assert items["候補的活動"]["detail"] == "目前是候補"
    assert tomorrow["ask"]


def test_scholarships_closing_within_two_weeks_count_the_days_left():
    items = [item for item in _reminders()["items"] if item["kind"] == "scholarship"]
    assert [(i["when"], i["title"]) for i in items] == [
        ("明天截止", "明天截止的獎學金"), ("還剩 5 天", "羅家倫校長紀念獎學金（學士班）"),
    ]
    assert items[1]["detail"] == "10/23（五）截止｜每名 2 萬元"


def test_items_with_dates_are_in_time_order():
    kinds = [item["kind"] for item in _reminders()["items"]]
    assert kinds.index("hours") > max(i for i, k in enumerate(kinds) if k != "hours")  # 時數放最後
    assert _titles(_reminders())[:3] == ["中央為翼，飛向國際｜115-1 出國講座系列", "受理課程停修申請", "職涯講座"]


def test_hours_waiting_for_a_long_time():
    items = [item for item in _reminders()["items"] if item["kind"] == "hours"]
    # 等最久的在前面。沒簽到的、不提供時數的、已經核發的、只有 0 小時的、才一週的、太久以前的都不列，
    # 0 小時的類別也不寫進去
    assert [i["title"] for i in items] == [
        "國際文化日", "107影享會115-1/新生電影節0923天才狂歡派對：最終章",
        "中央為翼，飛向國際｜115-1 出國講座系列/文學院學生出國說明會",
    ]
    pending, signed_out, missing_sign_out = items
    assert (pending["when"], pending["detail"]) == ("等了 28 天", "9/20（日）的活動，國際視野 2 小時待核發")
    assert (signed_out["when"], signed_out["detail"]) == ("等了 25 天", "9/23（三）的活動，主辦單位還沒登錄時數")
    assert signed_out["hint"] == "你有簽到簽退，可以向人文藝術中心確認"
    assert "沒有簽退" in missing_sign_out["hint"]
    # 點了直接問這個活動
    assert signed_out["ask"] == "「新生電影節0923天才狂歡派對：最終章」的時數核發了沒？"


def test_many_waiting_activities_are_summarised():
    rows = [{"cells": [f"講座{i}", "學務處", "團體", f"2026-09-{10 + i:02d} 10:00  2026-09-{10 + i:02d} 11:00"], "hours": []}
            for i in range(5)]
    items = [item for item in _reminders(hour_activities=parse_activity_rows(rows))["items"] if item["kind"] == "hours"]
    assert [i["title"] for i in items] == ["講座0", "講座1", "講座2", "還有 2 個活動的時數也還沒核發"]


def test_parts_still_loading_and_parts_that_failed():
    result = _reminders(registrations=None, hour_activities=None, scholarships=None,
                        loading=["hours", "scholarships"], failed=["activities"])
    assert result["status"] == "loading"
    assert result["loading"] == ["時數紀錄", "獎學金"]
    assert result["notes"] == ["這次沒有抓到你的活動報名紀錄，直接問我的時候會再查一次。"]
    assert {item["kind"] for item in result["items"]} == {"calendar"}


def test_no_calendar_is_explained():
    result = build_reminders(NOW, [], logged_in=False)
    assert result["items"] == [] and result["notes"] == ["目前沒有收錄校曆，沒有列出學校的重要日期。"]


def test_no_semicolons_in_any_text():
    result = _reminders(loading=["hours"], failed=["activities"])
    texts = [value for item in result["items"] for value in item.values()] + result["notes"] + [result["range"]]
    assert not any("；" in text for text in texts)
