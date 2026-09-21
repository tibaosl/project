"""action_tools.py 裡跟課表衝堂判斷相關的純函式測試。

這些函式不需要登入、不打任何網路，可以直接單元測試（跟需要真實 NCU 帳密
才能跑的 test.py / test_hours_activity.py 不同）。匯入 action_tools 仍然
需要裝 playwright（它在檔案頂端被 import），但測試本身不會真的啟動瀏覽器。

用 pytest 執行：pytest test_schedule_helpers.py
"""

from action_tools import (
    build_schedule_occupied_slots,
    parse_course_time_slots,
    filter_available_courses,
)


def test_parse_course_time_slots_expands_each_period():
    assert parse_course_time_slots("一234") == {"一2", "一3", "一4"}


def test_parse_course_time_slots_handles_letter_periods():
    assert parse_course_time_slots("四ABC") == {"四A", "四B", "四C"}


def test_parse_course_time_slots_ignores_credit_hint_strings():
    # 選課系統偶爾會把「學分」字樣混進時間欄位，這種要直接當成沒有時段，
    # 不然會被誤判成跟某個時段衝堂。
    assert parse_course_time_slots("2學分") == set()


def test_parse_course_time_slots_empty_input():
    assert parse_course_time_slots("") == set()


def test_build_schedule_occupied_slots_converts_chinese_day_and_period():
    schedule_data = [
        {"day": "星期一", "period": "第2節", "time": "09:00-09:50", "details": []},
        {"day": "週三", "period": "第A節", "time": "18:00-18:50", "details": []},
    ]
    assert build_schedule_occupied_slots(schedule_data) == {"一2", "三A"}


def test_filter_available_courses_splits_on_conflict():
    occupied = {"一2", "一3"}
    courses = [
        {"title": "衝堂課", "time": "一23"},
        {"title": "沒衝堂", "time": "四5"},
    ]
    available, conflicted = filter_available_courses(courses, occupied)

    assert [c["title"] for c in available] == ["沒衝堂"]
    assert [c["title"] for c in conflicted] == ["衝堂課"]
    assert "conflict_reason" in conflicted[0]
