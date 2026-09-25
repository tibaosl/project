"""學業分析的解析/分析邏輯測試，用手寫的假 HTML，不需要登入：
    python -m pytest test_academic_tools.py
"""

import json
from html import escape

from academic_tools import (
    analyze_academic_progress,
    parse_graduation_report_html,
    parse_transcript_html,
)


def _course_row(no, name, course_type, credits, score, remark="-"):
    return f"""
    <tr class=""><td>{no}</td><td>A</td><td>{name}<br/>English Name</td><td>{course_type}</td>
    <td>學士班</td><td>{credits}</td><td><span>{score}<span></span></span></td>
    <td><span>{remark}</span></td></tr>"""


def _semester_table(year, term, rows, average, earned):
    return f"""
    <table class="table"><tbody>
    <tr><th colspan="9">第{year}學年度第{term}學期 (xxx)</th></tr>
    <tr><th>課號</th><th>班別</th><th>課名</th><th>課程屬性</th><th>學制屬性</th>
        <th>學分數</th><th>成績</th><th>備註</th></tr>
    {''.join(rows)}
    <tr><td colspan="9"><label>操行成績：85.00、學期平均：{average}、修習學分數：{earned + 3}、學期實得學分(不含暑修、抵免)：{earned}、EMI實得學分：0</label></td></tr>
    </tbody></table>"""


TRANSCRIPT_HTML = f"""
<html><body>
<div class="panel-body">
  <div class="fmlabel">系所</div><div class="fminput"><span>測試學院測試學系</span></div>
  <div class="fmlabel">年級</div><div class="fminput"><span>二年A班</span></div>
  <div class="fmlabel">學制</div><div class="fminput"><span>學士班</span></div>
  <div class="fmlabel">累計學分</div><div class="fminput"><span>20</span></div>
  <div class="fmlabel">學業平均成績</div><div class="fminput"><span>78.50</span></div>
</div>
<h5>學期成績</h5>
{_semester_table(114, 1, [
    _course_row("CE9001", "進階測試", "必修", 3, "停修"),
    _course_row("CE9002", "重修測試", "必修", 3, "72.00"),
    _course_row("CE9004", "修課中", "選修", 2, "-"),
], 75.00, 3)}
{_semester_table(113, 2, [
    _course_row("CE9002", "重修測試", "必修", 3, "45.00"),
    _course_row("GS9001", "通識測試", "通識", 2, "88.00"),
    _course_row("SC9001", "服務學習", "勞動服務", 0, "勞動服務通過"),
    _course_row("LN9001", "被當的選修", "選修", 3, "50.00"),
], 80.00, 2)}
<h5>學期排名</h5>
<table><tr><th>學年度</th><th>學期平均</th><th>班排名</th><th>系(組)排名</th><th>排名鎖定</th></tr>
<tr><td>1132</td><td>80.00</td><td>10/50</td><td>20/100</td><td>鎖定</td></tr>
<tr><td>1141</td><td>75.00</td><td>15/50</td><td>30/100</td><td>鎖定</td></tr></table>
<h5>累計排名</h5>
<table><tr><th>學年度</th><th>學期平均</th><th>班排名</th><th>系(組)排名</th><th>排名鎖定</th></tr>
<tr><td>1141</td><td>78.50</td><td>12/50</td><td>25/100</td><td>鎖定</td></tr></table>
</body></html>
"""


def _grad_course(semester, no, name, score, qualified):
    return {
        "semester": semester, "c_id": no, "crs_name": {"tw": name, "en": ""},
        "c_cred": "3", "l_cred": "3", "score": score, "qualified": qualified,
        "replace": None, "memo": {"tw": "", "en": ""},
    }


def _block(data):
    return f'<div class="text-block" data-credits="{escape(json.dumps(data), quote=True)}"></div>'


REQUIRED_CATEGORY = {
    "ruleName": "系訂必修學分", "isPass": "F", "creditCred": 3, "creditCourse": 1,
    "lowCredits": "9", "lowCourses": "3", "percentage": 33.3,
    "memo": "※學分數或課程數不足.※未通過類別:12100,",
    "course": json.dumps({
        "12100": {
            "ruleName": "一般系訂必修", "isPass": "F", "creditCred": 3, "creditCourse": 1,
            "lowCredits": "9", "lowCourses": "3", "memo": "※未通過類別:12101,",
            "12101": {
                "ruleName": "一般系訂必修-A類", "isPass": "F", "creditCred": 3, "creditCourse": 1,
                "lowCredits": "9", "lowCourses": "3", "memo": "",
                "course": [
                    _grad_course("1141", "CE9001", "進階測試", "停修(Withdrawn)", "0"),
                    _grad_course("1141", "CE9002", "重修測試", "72.00", "1"),
                    _grad_course("1141", "CE9100", "抵免未過", "55.00", "0"),
                ],
            },
        },
    }),
}

OTHER_CATEGORY = {
    "ruleName": "其它學分", "isPass": "F", "creditCred": 0, "creditCourse": 1,
    "lowCredits": "0", "lowCourses": "1", "percentage": 50,
    "memo": "※未通過類別:18500,",
    "course": json.dumps({
        "18500": {
            "ruleName": "學生學習護照", "isPass": "F", "creditCred": 0, "creditCourse": 1,
            "lowCredits": "0", "lowCourses": "1",
            "memo": "※總時數:50.0(尚未達到畢業基礎門檻)",
        },
    }),
}

ELECTIVE_CATEGORY = {
    "ruleName": "一般選修", "isPass": "T", "creditCred": 2, "creditCourse": 1,
    "lowCredits": "0", "lowCourses": "0", "percentage": 100, "memo": "", "course": "{}",
}

GRADUATION_HTML = f"""
<html><body>
<p class="department">113-測試學系</p>
<p class="credit"><span>20</span>學分, <span>8</span>門課</p>
<p class="total-credits">應修128學分</p>
<p class="graduation-threshold text-danger">尚未通過及格門檻</p>
<div id="text-block-container">
{_block(REQUIRED_CATEGORY)}{_block(ELECTIVE_CATEGORY)}{_block(OTHER_CATEGORY)}
</div>
</body></html>
"""


def test_parse_transcript_semesters_are_chronological_with_summary():
    t = parse_transcript_html(TRANSCRIPT_HTML)

    assert t["department"] == "測試學院測試學系"
    assert t["cumulative_credits"] == 20
    assert t["cumulative_average"] == 78.5
    assert [s["term"] for s in t["semesters"]] == ["1132", "1141"]

    latest = t["semesters"][1]
    assert latest["summary"] == {"average": 75.0, "attempted_credits": 6, "earned_credits": 3}
    withdrawn = latest["courses"][0]
    assert withdrawn["course_no"] == "CE9001"
    assert withdrawn["name"] == "進階測試"
    assert withdrawn["score"] is None and withdrawn["score_text"] == "停修"
    assert withdrawn["remark"] == ""


def test_parse_transcript_separates_semester_and_cumulative_ranks():
    ranks = parse_transcript_html(TRANSCRIPT_HTML)["ranks"]

    assert ranks["semester"]["1132"]["class_rank"] == "10/50"
    assert list(ranks["cumulative"]) == ["1141"]


def test_parse_graduation_report_totals_and_nested_rules():
    g = parse_graduation_report_html(GRADUATION_HTML)

    assert g["total_required"] == 128
    assert g["total_earned"] == 20
    assert g["total_courses"] == 8
    assert g["passed_threshold"] is False

    required = g["categories"][0]
    assert required["name"] == "系訂必修學分"
    leaf = required["children"][0]["children"][0]
    assert leaf["name"] == "一般系訂必修-A類"
    assert [c["qualified"] for c in leaf["courses"]] == [False, True, False]


def test_analysis_credits_and_gpa_trend():
    a = analyze_academic_progress(
        parse_transcript_html(TRANSCRIPT_HTML), parse_graduation_report_html(GRADUATION_HTML)
    )

    assert a["kind"] == "academic_analysis"
    assert a["credits"]["earned"] == 20
    assert a["credits"]["remaining"] == 108
    # 只算通過的課：72 分的重修、88 分的通識；勞動服務 0 學分不列
    assert a["credits"]["by_course_type"] == {"必修": 3, "通識": 2}
    assert a["gpa"]["latest_change"] == -5.0
    assert a["gpa"]["semesters"][0]["dept_rank"] == "20/100"
    assert a["gpa"]["cumulative_ranks"] == [
        {"term": "1141", "label": "114-1", "average": 78.5, "class_rank": "12/50", "dept_rank": "25/100"}
    ]


def test_analysis_focus_defaults_to_overview_and_rejects_unknown_values():
    transcript = parse_transcript_html(TRANSCRIPT_HTML)

    assert analyze_academic_progress(transcript, None)["focus"] == "overview"
    assert analyze_academic_progress(transcript, None, "credits")["focus"] == "credits"
    assert analyze_academic_progress(transcript, None, "whatever")["focus"] == "overview"


def test_analysis_unmet_rules_use_deepest_rule_and_clean_memo():
    a = analyze_academic_progress(
        parse_transcript_html(TRANSCRIPT_HTML), parse_graduation_report_html(GRADUATION_HTML)
    )
    by_name = {c["name"]: c for c in a["graduation_categories"]}

    assert by_name["系訂必修學分"]["unmet_rules"] == [
        {"name": "一般系訂必修-A類", "remaining_credits": 6, "remaining_courses": 2, "memo": ""}
    ]
    assert by_name["一般選修"]["unmet_rules"] == []
    assert by_name["其它學分"]["unmet_rules"][0]["memo"] == "總時數:50.0(尚未達到畢業基礎門檻)"


def _rule(name, passed, earned_credits, required_credits, earned_courses, required_courses, children=()):
    return {
        "code": "", "name": name, "passed": passed, "memo": "", "courses": [],
        "earned_credits": earned_credits, "required_credits": required_credits,
        "earned_courses": earned_courses, "required_courses": required_courses,
        "children": list(children), "percentage": None,
    }


def test_unmet_rules_report_gap_not_covered_by_sub_rules():
    # 真實案例：共同必修要 10 門，細項（外文 2、核心通識 5、國文 2）加起來只有 9 門
    category = _rule("共同必修學分", False, 20, 25, 8, 10, [
        _rule("外文", True, 6, 6, 2, 2),
        _rule("核心通識課程", False, 9, 14, 4, 5),
        _rule("大一國文", True, 5, 5, 2, 2),
    ])
    graduation = {
        "total_required": 128, "total_earned": 20, "total_courses": 8,
        "passed_threshold": False, "categories": [category],
    }

    a = analyze_academic_progress(parse_transcript_html(TRANSCRIPT_HTML), graduation)
    unmet = a["graduation_categories"][0]["unmet_rules"]

    assert [(u["name"], u["remaining_credits"], u["remaining_courses"]) for u in unmet] == [
        ("核心通識課程", 5, 1),
        ("共同必修學分整體", 0, 1),
    ]
    assert unmet[1]["memo"] == "細項都達標後整體還要再多修的部分，修「外文、核心通識課程、大一國文」任一類的課都算"


def test_alerts_skip_retaken_and_in_progress_courses():
    a = analyze_academic_progress(
        parse_transcript_html(TRANSCRIPT_HTML), parse_graduation_report_html(GRADUATION_HTML)
    )
    alerts = {(x["course_no"], x["reason"]) for x in a["alerts"]}

    assert ("CE9001", "停修") in alerts
    assert ("LN9001", "不及格") in alerts
    # 從畢業審查表補抓：成績頁沒有這門課
    assert ("CE9100", "畢業審查未通過") in alerts
    # 113-2 被當、114-1 重修過了 → 不提醒；修課中的 "-" 也不提醒
    assert not any(no in ("CE9002", "CE9004") for no, _ in alerts)
    # 必修排前面
    assert a["alerts"][0]["required"] is True
    assert a["alerts"][-1]["course_no"] == "LN9001"


def test_analysis_without_graduation_report_falls_back_to_transcript():
    a = analyze_academic_progress(parse_transcript_html(TRANSCRIPT_HTML), None)

    assert a["graduation_available"] is False
    assert a["credits"]["earned"] == 20
    assert a["credits"]["required"] is None
    assert a["graduation_categories"] == []
