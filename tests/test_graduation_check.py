"""畢業總檢的整理邏輯測試，用照真實畢業資格審查表結構手寫的假資料，不需要登入：
    python -m pytest tests/test_graduation_check.py
"""

import copy
import json
from html import escape

from backend.analysis.academic_tools import parse_graduation_report_html
from backend.analysis.graduation_check import (
    AUTO,
    DONE,
    PENDING,
    THIS_TERM,
    TODO,
    build_graduation_check,
    needs_hours_dashboard,
)


def _course(semester, no, name, credits, score, qualified):
    return {
        "semester": semester, "c_id": no, "crs_name": {"tw": name, "en": ""},
        "c_cred": str(credits), "l_cred": str(credits), "score": score, "qualified": qualified,
        "replace": None, "memo": {"tw": "", "en": ""},
    }


def _rule(name, passed, credits, required_credits, courses, required_courses, memo="", **children):
    node = {
        "ruleName": name, "isPass": "T" if passed else "F",
        "creditCred": credits, "lowCredits": str(required_credits),
        "creditCourse": courses, "lowCourses": str(required_courses), "memo": memo,
    }
    course_list = children.pop("course", None)
    if course_list is not None:
        node["course"] = course_list
    node.update(children)
    return node


def _category(name, passed, credits, required_credits, courses, required_courses, rules, memo=""):
    data = _rule(name, passed, credits, required_credits, courses, required_courses, memo)
    data["percentage"] = 50
    data["course"] = json.dumps(rules, ensure_ascii=False)
    return f'<div class="text-block" data-credits="{escape(json.dumps(data, ensure_ascii=False), quote=True)}"></div>'


# 結構照 2026-10 資工系大三的真實審查表，數字跟課名改過
COMMON = _category("共同必修學分", False, 20, 25, 8, 10, {
    "11200": _rule("共同必修-外文", True, 6, 6, 2, 2, **{"11201": _rule("外文-大一英文", True, 6, 6, 2, 2, course=[
        _course("1131", "LN1001", "大一英文", 3, "90.00", "1"),
        _course("1132", "LN1002", "大一英文", 3, "91.00", "1"),
    ])}),
    "11320": _rule("核心通識課程", False, 9, 14, 4, 5, **{
        "11321": _rule("核心通識-人文與思想", True, 3, 0, 1, 0),
        "11322": _rule("核心通識-自然科學", True, 6, 0, 3, 0),
    }),
    "11400": _rule("基礎課程-大一國文", True, 5, 5, 2, 2),
}, memo="※學分數或課程數不足.※未通過類別:11320,")

def _required(required_credits=58, required_courses=22, retake_algorithms=False):
    last = (_course("1151", "CE3005", "演算法", 3, "未到(Not Entered)", "2") if retake_algorithms
            else _course("1151", "CE3008", "專題實驗 I", 2, "未到(Not Entered)", "2"))
    courses = [
        _course("1131", "MA1003", "微積分", 3, "45.00", "0"),     # 被當、下學期重修過了
        _course("1132", "MA1003", "微積分", 3, "70.00", "1"),
        _course("1132", "MA1004", "微積分", 3, "88.00", "1"),     # 跟上面同名、不同課號
        _course("1142", "CE3005", "演算法", 3, "停修(Withdrawn)", "0"),
        _course("1151", "CE3006", "編譯器", 3, "未到(Not Entered)", "2"),
        _course("1151", "CE3007", "計算機網路", 3, "未到(Not Entered)", "2"),
        last,
    ]
    counts = (45, required_credits, 17, required_courses)
    return _category("系訂必修學分", False, *counts, {
        "12100": _rule("一般系訂必修學分", False, *counts, **{
            "12101": _rule("一般系訂必修學分-A類", False, *counts, course=courses),
        }),
    })


REQUIRED = _required()

ELECTIVE = _category("一般選修", True, 9, 0, 3, 0, {
    "14100": _rule("一般選修", True, 9, 0, 3, 0, **{"14101": _rule("一般選修", True, 9, 0, 3, 0, course=[
        _course("1141", "LN0025", "日文(一)A", 3, "90.00", "1"),
        _course("1142", "LN0099", "被當的選修", 3, "40.00", "0"),
        _course("1151", "CE5037", "類神經網路", 3, "未到(Not Entered)", "2"),
    ])}),
})

OTHER = _category("其它學分", False, 1, 0, 11, 15, {
    "18100": _rule("操行", False, 0, 0, 4, 8, **{"18101": _rule("操行", False, 0, 0, 4, 8, course=[
        _course(term, "CR0001", "操行", 0, "85.00", "1") for term in ("1131", "1132", "1141", "1142")
    ])}),
    "18200": _rule("體育", False, 0, 0, 3, 5, **{
        "18201": _rule("大一體育", True, 0, 0, 2, 2),
        "18202": _rule("其它年級體育", False, 0, 0, 1, 3, course=[
            _course("1142", "PE2071", "排球入門", 0, "80.00", "1"),
            _course("1151", "PE2151", "高爾夫入門", 0, "未到(Not Entered)", "2"),
        ]),
    }),
    "18300": _rule("軍訓", True, 1, 0, 1, 0, memo="※本階層規則之實得學分數不納入畢業總學分.", **{
        "18301": _rule("軍訓", True, 1, 0, 1, 0, course=[_course("1151", "MN3006", "軍訓測試", 1, "未到(Not Entered)", "2")]),
    }),
    "18400": _rule("服務學習", True, 0, 0, 2, 2),
    "18500": _rule("學生學習護照", False, 0, 0, 1, 1, memo="※總時數:96.0(尚未達到畢業基礎門檻,詳細資料請參考:服務櫃台)"),
})

SPECIAL = _category("特殊檢核", False, 0, 0, 0, 0, {
    "19100": _rule("資電學院選修總學分", False, 0, 21, 0, 0),
    "19200": _rule("特殊檢核條件-通過進修英文或英文能力鑑定考試合格", True, 0, 4, 0, 2, memo="※通過[培力英檢考試]"),
    "19250": _rule("特殊檢核-系所自訂之等同課程重複修習不列入畢業學分數", True, 0, 0, 0, 0),
})


def _graduation(*blocks, earned=74, required=128):
    return parse_graduation_report_html(f"""
    <html><body>
    <p class="department">113-資訊工程學系</p>
    <p class="credit"><span>{earned}</span>學分, <span>38</span>門課</p>
    <p class="total-credits">應修{required}學分</p>
    <p class="graduation-threshold text-danger">尚未通過及格門檻</p>
    {''.join(blocks)}
    </body></html>""")


GRADUATION = _graduation(COMMON, REQUIRED, ELECTIVE, OTHER, SPECIAL)


def _sub(confirmed, required, pending=0.0):
    return {
        "confirmed_hours": confirmed, "pending_hours": pending, "required": required,
        "remaining": max(0.0, required - confirmed), "passed": confirmed >= required,
    }


HOURS = {
    "categories": {
        "服務學習": {"subcategories": {"服務學習課程": _sub(30, 30), "校外服務": _sub(8, 10)}},
        "人文藝術": {"subcategories": {"人文藝術": _sub(18, 20, pending=2)}},
        "國際視野": {"subcategories": {"國際視野": _sub(5, 5)}},
    },
}


def _items(result):
    return {item["title"]: item for item in result["items"]}


def _all_text(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_text(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_text(v)


def test_parse_marks_courses_in_progress_this_term():
    leaf = GRADUATION["categories"][1]["children"][0]["children"][0]
    in_progress = [c["name"] for c in leaf["courses"] if c["in_progress"]]
    assert in_progress == ["編譯器", "計算機網路", "專題實驗 I"]
    assert not any(c["qualified"] for c in leaf["courses"] if c["in_progress"])


def test_total_credits_count_courses_in_progress_once_and_skip_excluded_rules():
    total = _items(build_graduation_check(GRADUATION, HOURS))["畢業總學分"]

    assert total["status"] == TODO
    assert total["progress"] == "74 / 128 學分"
    # 修課中：編譯器 3、計網 3、專題 2、類神經網路 3、高爾夫 0，軍訓不算進畢業總學分
    assert total["lines"] == ["還差 54 學分", "這學期修課中 11 學分，都通過的話還差 43 學分"]


def test_required_courses_list_in_progress_and_retake_and_ask_with_department_and_cohort():
    item = _items(build_graduation_check(GRADUATION, HOURS))["一般系訂必修學分"]

    assert item["status"] == TODO
    assert item["progress"] == "45 / 58 學分・17 / 22 門"
    assert item["lines"] == [
        "還差 13 學分、5 門",
        "這學期修課中：編譯器、計算機網路、專題實驗 I（8 學分），都通過的話還差 5 學分、2 門",
        # 微積分 MA1003 重修過了、MA1004 同名不同課號都不算
        "要重修：演算法（114-2 停修）",
    ]
    assert item["asks"] == [{"label": "問必修科目有哪些", "question": "資訊工程學系 113 學年度入學，必修科目有哪些？"}]


def test_item_finished_by_courses_in_progress_is_this_term_without_advice():
    item = _items(build_graduation_check(_graduation(_required(53, 20)), None))["一般系訂必修學分"]

    # 還差 8 學分、3 門，修課中剛好 8 學分、3 門。停修的演算法沒有重修，還是要另外補
    assert item["status"] == TODO
    assert "要重修：演算法（114-2 停修）" in item["lines"]

    item = _items(build_graduation_check(_graduation(_required(53, 20, retake_algorithms=True)), None))["一般系訂必修學分"]
    assert item["status"] == THIS_TERM
    assert "要重修：演算法（114-2 停修，這學期重修中）" in item["lines"]
    assert item["how"] == "" and item["asks"] == []


def test_failed_elective_is_not_a_retake():
    result = build_graduation_check(GRADUATION, HOURS)
    assert not any("被當的選修" in line for line in _all_text(result))


def test_non_credit_conditions():
    items = _items(build_graduation_check(GRADUATION, HOURS))

    conduct = items["操行"]
    assert conduct["status"] == AUTO
    assert conduct["progress"] == "4 / 8 學期"
    assert conduct["lines"] == ["還差 4 學期"]

    pe = items["體育"]
    assert pe["status"] == TODO
    assert pe["lines"] == ["還差 2 門", "這學期修課中：高爾夫入門，都通過的話還差 1 門"]
    assert pe["how"] == "之後的學期再選修體育課補齊門數。"

    college = items["資電學院選修總學分"]
    assert college["status"] == TODO
    assert college["lines"] == ["還差 21 學分"]
    assert college["asks"][0]["question"] == "資訊工程學系 113 學年度入學，「資電學院選修總學分」有什麼規定？"


def test_done_items_and_rules_without_requirements():
    items = _items(build_graduation_check(GRADUATION, HOURS))

    english = items["通過進修英文或英文能力鑑定考試合格"]
    assert english["status"] == DONE
    assert english["note"] == "通過培力英檢考試"
    assert items["服務學習"]["status"] == DONE
    assert items["共同必修-外文"]["status"] == DONE
    # 沒有要求的規則（軍訓、系所自訂的提醒、一般選修）不列
    assert not any(title.startswith(("軍訓", "系所自訂", "一般選修")) for title in items)


def test_category_requirement_beyond_sub_rules_shows_whole_gap():
    items = _items(build_graduation_check(GRADUATION, HOURS))

    assert items["核心通識課程"]["lines"] == ["還差 5 學分、1 門"]
    assert items["核心通識課程"]["how"] == "選修通識課程補齊，人文與思想、自然科學任一類的課都算。"
    whole = items["共同必修學分（整體）"]
    assert whole["status"] == TODO
    assert whole["lines"] == [
        "整體還差 5 學分、2 門，補細項修的課也算在內，共同必修-外文、核心通識課程、基礎課程-大一國文任一類的課都算"
    ]


def test_study_passport_uses_hours_dashboard_details():
    passport = _items(build_graduation_check(GRADUATION, HOURS))["學生學習護照時數"]

    assert passport["status"] == TODO
    assert passport["lines"] == [
        "校外服務（服務學習）：8 / 10 小時，還差 2 小時",
        "人文藝術：18 / 20 小時，還差 2 小時，另有 2 小時待核發，核發後就達標",
    ]
    assert [a["question"] for a in passport["asks"]] == ["有什麼活動可以幫我補時數？", "我有哪些活動的時數還在待核發？"]


def test_study_passport_waiting_for_pending_hours():
    hours = copy.deepcopy(HOURS)
    hours["categories"]["服務學習"]["subcategories"]["校外服務"] = _sub(10, 10)
    passport = _items(build_graduation_check(GRADUATION, hours))["學生學習護照時數"]

    assert passport["status"] == PENDING
    assert [a["question"] for a in passport["asks"]] == ["我有哪些活動的時數還在待核發？"]


def test_study_passport_without_hours_dashboard_uses_report_total():
    passport = _items(build_graduation_check(GRADUATION, None))["學生學習護照時數"]

    assert passport["status"] == TODO
    assert passport["lines"] == ["目前總時數 96 小時，還沒達到畢業門檻（各類別的時數暫時讀不到）"]
    assert passport["asks"] == [{"label": "看時數還差多少", "question": "我的學習護照時數還差多少？"}]


def test_needs_hours_dashboard_only_when_passport_not_passed():
    assert needs_hours_dashboard(GRADUATION)
    assert not needs_hours_dashboard(_graduation(COMMON, REQUIRED))


def test_headline_order_and_no_semicolons():
    result = build_graduation_check(GRADUATION, HOURS)

    assert result["kind"] == "graduation_check"
    assert (result["department"], result["cohort"]) == ("資訊工程學系", "113")
    assert result["can_graduate"] is False
    assert result["headline"] == "離畢業還有 8 項條件沒完成"
    assert result["summary"] == "7 項還要補、1 項照常在學就會完成。"
    statuses = [item["status"] for item in result["items"]]
    assert statuses == sorted(statuses, key=[TODO, THIS_TERM, PENDING, AUTO, DONE].index)
    assert result["items"][0]["title"] == "畢業總學分"
    # 使用者不喜歡分號，給使用者看的文字都不能有
    assert not any("；" in text or ";" in text for text in _all_text(result))


def test_everything_done():
    done = parse_graduation_report_html("""
    <p class="department">資訊工程學系</p>
    <p class="credit"><span>130</span>學分, <span>60</span>門課</p>
    <p class="total-credits">應修128學分</p>
    <p class="graduation-threshold">已通過及格門檻</p>
    """ + _category("系訂必修學分", True, 58, 58, 22, 22, {"12100": _rule("一般系訂必修學分", True, 58, 58, 22, 22)}))
    result = build_graduation_check(done, None)

    assert result["can_graduate"] is True
    assert result["headline"] == "畢業條件都完成了"
    assert {item["status"] for item in result["items"]} == {DONE}
    assert result["department"] == "資訊工程學系" and result["cohort"] == ""
