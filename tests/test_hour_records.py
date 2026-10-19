"""學習護照時數的活動明細：讀活動總表（hour_activities）、回答某個活動核發了沒（hour_records）。
不連學校系統，表格的樣子照 2026-10-13 真實頁面的結構。
    python -m pytest tests/test_hour_records.py
"""

from backend.analysis import hour_records
from backend.ncu.hour_activities import parse_activity_rows

ROWS = [
    {"cells": ["中央為翼，飛向國際｜115-1 出國講座系列/客家學院學生出國說明會", "國際事務處海外教育推廣組", "團體",
               "2026-10-13 12:00  2026-10-13 13:00"], "hours": []},
    {"cells": ["中央為翼，飛向國際｜115-1 出國講座系列/文學院學生出國說明會", "國際事務處海外教育推廣組", "團體",
               "2026-10-06 12:00  2026-10-06 13:00"], "hours": [["學習護照", "國際視野", "1", "時數待核發"]]},
    {"cells": ["中央為翼，飛向國際｜115-1 出國講座系列/資電學院學生出國說明會", "國際事務處海外教育推廣組", "團體",
               "2026-09-30 12:00  2026-09-30 13:00"], "hours": [["學習護照", "國際視野", "1", "已核發"]]},
    {"cells": ["107影享會115-1/新生電影節0923天才狂歡派對：最終章", "人文藝術中心", "團體",
               "2026-09-23 18:00  2026-09-23 20:00"], "hours": []},
    {"cells": ["大一週會：學習與生活", "學務處", "團體", "2026-09-16 13:00  2026-09-16 15:00"],
     "hours": [["學習護照", "大一週會", "2", "已核發"], ["軟實力", "自主學習", "1", "已核發"]]},
    {"cells": ["格式不對的列"], "hours": []},
]
ACTIVITIES = parse_activity_rows(ROWS)


def test_activity_rows_keep_names_times_and_hour_status():
    assert len(ACTIVITIES) == 5  # 格式不對的列略過
    pending = ACTIVITIES[1]
    assert pending["name"].endswith("文學院學生出國說明會")
    assert (pending["start"], pending["end"]) == ("2026-10-06 12:00", "2026-10-06 13:00")
    assert pending["hours"] == [{"type": "學習護照", "category": "國際視野", "hours": 1.0, "status": "待核發"}]
    assert ACTIVITIES[3]["hours"] == []  # 時數欄是空的：主辦單位還沒登錄


def test_activity_with_no_hours_yet_is_not_called_pending():
    # 使用者問「9/23 的新生電影節是不是還沒核發」：時數欄是空的，要講清楚還不是待核發
    text = hour_records.answer(ACTIVITIES, keyword="新生電影節", date="9/23")
    assert text.startswith("「107影享會115-1/新生電影節0923天才狂歡派對：最終章」（2026/9/23，人文藝術中心）")
    assert "還沒有任何時數紀錄" in text and "還不是「待核發」" in text
    assert "人文藝術中心" in text


def test_pending_hours_of_a_category_list_the_activities():
    text = hour_records.answer(ACTIVITIES, category="國際視野", status="待核發")
    assert text.startswith("國際視野、待核發的活動有 1 個，共 1 小時")
    assert "文學院學生出國說明會 | 2026/10/6 | 國際視野 1 小時 | 待核發 |" in text
    assert "資電學院" not in text  # 已核發的不列


def test_a_single_pending_activity_found_by_name():
    text = hour_records.answer(ACTIVITIES, keyword="文學院 出國說明會")
    assert "國際視野 1 小時**待核發**" in text


def test_category_group_includes_its_subcategories():
    # 問「生活知能」要把底下的大一週會也算進來，軟實力的時數不算
    matches = hour_records.find_activities(ACTIVITIES, category="生活知能")
    assert [a["name"] for a in matches] == ["大一週會：學習與生活"]
    assert [e["category"] for e in matches[0]["matched_hours"]] == ["大一週會"]


def test_activities_without_hours_can_be_listed():
    matches = hour_records.find_activities(ACTIVITIES, status="還沒有時數")
    assert [a["start"][:10] for a in matches] == ["2026-10-13", "2026-09-23"]  # 新的在前面


def test_unknown_activity_says_it_is_not_in_the_records():
    text = hour_records.answer(ACTIVITIES, keyword="校慶路跑")
    assert text.startswith("你的時數紀錄裡找不到「校慶路跑」的活動")


def test_month_day_formats():
    for text in ("9/23", "09-23", "2026-09-23", "9月23日", "2026-09-23 18:00"):
        assert hour_records.parse_month_day(text) == (9, 23)
    assert hour_records.parse_month_day("新生電影節") is None


# 「我的報名」頁的紀錄：名稱寫法跟時數頁不一樣（場次名稱附英文、時間前面有「開始：」）
REGISTRATIONS = [
    {"活動名稱": "107影享會115-1 Movie Time @ 107 CineSpace",
     "場次名稱": "新生電影節0923天才狂歡派對：最終章 Freshman Film Festival 0923 Genius Party Beyond",
     "活動場次時間": "開始：2026-09-23 18:00 結束：2026-09-23 20:00", "簽到/簽退": "已簽到 / 已簽退"},
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "文學院學生出國說明會",
     "活動場次時間": "開始：2026-10-06 12:00 結束：2026-10-06 13:00", "簽到/簽退": "已簽到 / 未簽退"},
    {"活動名稱": "中央為翼，飛向國際｜115-1 出國講座系列", "場次名稱": "客家學院學生出國說明會",
     "活動場次時間": "開始：2026-10-13 12:00 結束：2026-10-13 13:00", "簽到/簽退": "未簽到 / 未簽退"},
]


def test_signed_out_but_no_hours_means_the_organizer_has_not_entered_them():
    from datetime import datetime

    text = hour_records.answer(ACTIVITIES, keyword="新生電影節", date="9/23", registrations=REGISTRATIONS,
                               now=datetime(2026, 10, 13, 9, 0))
    assert "你有完成簽到簽退（報名紀錄顯示「已簽到 / 已簽退」）" in text
    assert "主辦單位**還沒登錄這個活動的時數**" in text


def test_pending_hours_mention_a_missing_sign_out():
    text = hour_records.answer(ACTIVITIES, keyword="文學院", registrations=REGISTRATIONS)
    assert "國際視野 1 小時**待核發**" in text
    assert "「已簽到 / 未簽退」，沒有簽退的話主辦單位可能不會登錄時數" in text


def test_activity_that_has_not_happened_yet():
    from datetime import datetime

    text = hour_records.answer(ACTIVITIES, keyword="客家學院", registrations=REGISTRATIONS,
                               now=datetime(2026, 10, 13, 9, 0))
    assert text.endswith("還沒舉行，時數要等活動結束、主辦單位登錄之後才會出現。")


def test_table_gets_a_checkin_column_when_known():
    text = hour_records.answer(ACTIVITIES, status="還沒有時數", registrations=REGISTRATIONS)
    assert "| 活動 | 日期 | 時數 | 狀態 | 簽到/簽退 |" in text
    assert "| 2026/9/23 | — | 還沒有時數 | 已簽到 / 已簽退 |" in text


def test_checkin_is_only_needed_for_activities_still_waiting():
    issued_only = hour_records.find_activities(ACTIVITIES, status="已核發")
    assert not hour_records.needs_checkin(issued_only)
    # 待核發的已經登錄時數了，不用多花時間讀報名紀錄
    assert not hour_records.needs_checkin(hour_records.find_activities(ACTIVITIES, status="待核發"))
    assert hour_records.needs_checkin(hour_records.find_activities(ACTIVITIES, keyword="新生電影節"))


ZERO_ROWS = [
    {"cells": ["藝文講座/電影與城市", "人文藝術中心", "團體", "2026-09-10 18:00  2026-09-10 20:00"],
     "hours": [["學習護照", "人文藝術", "2", "時數待核發"], ["學習護照", "自我探索與生涯規劃", "0", "時數待核發"]]},
    {"cells": ["社團博覽會/社團介紹", "課外活動組", "團體", "2026-09-05 12:00  2026-09-05 13:00"],
     "hours": [["學習護照", "社團參與", "0", "已核發"], ["學習護照", "自我探索與生涯規劃", "0", "已核發"]]},
]
ZERO_ACTIVITIES = parse_activity_rows(ZERO_ROWS)


def test_zero_hour_categories_are_not_listed_as_pending():
    # 時數頁會把每個類別都列出來，沒有給時數的類別是 0 小時，不能寫成「自我探索與生涯規劃 0 小時待核發」
    text = hour_records.answer(ZERO_ACTIVITIES, keyword="電影與城市")
    assert "人文藝術 2 小時**待核發**" in text
    assert "自我探索" not in text

    assert hour_records.find_activities(ZERO_ACTIVITIES, category="自我探索與生涯規劃") == []
    table = hour_records.answer(ZERO_ACTIVITIES + ACTIVITIES, status="待核發")
    assert "電影與城市 | 2026/9/10 | 人文藝術 2 小時 | 待核發 |" in table
    assert "自我探索" not in table


def test_activities_with_only_zero_hours_say_no_hours_were_given():
    text = hour_records.answer(ZERO_ACTIVITIES, keyword="社團博覽會")
    assert "（社團參與、自我探索與生涯規劃）都是 **0 小時**" in text
    assert "還沒有任何時數紀錄" not in text  # 主辦單位已經登錄了，不是還沒登錄

    table = hour_records.answer(ZERO_ACTIVITIES)
    assert "社團介紹 | 2026/9/5 | 社團參與 0 小時、自我探索與生涯規劃 0 小時 | 已核發 |" in table
