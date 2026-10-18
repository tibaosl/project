"""學業分析的解析/分析邏輯測試，用手寫的假 HTML，不需要登入：
    python -m pytest tests/test_academic_tools.py
"""

import json
from html import escape

import pytest

from backend.analysis.academic_tools import (
    analyze_academic_progress,
    compute_gpa,
    courses_awaiting_grades,
    estimate_gpa,
    grade_point,
    parse_assumed_grade,
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


def test_cumulative_rank_of_a_term_without_grades_is_dropped():
    # iNCU 的累計排名表會多一列還沒有成績的這學期，數字跟上學期一樣（2026-10 用真實帳號看到的）
    transcript = parse_transcript_html(TRANSCRIPT_HTML)
    transcript["ranks"]["cumulative"]["1142"] = {"average": 78.5, "class_rank": "12/50", "dept_rank": "25/100"}
    a = analyze_academic_progress(transcript, None)
    assert [r["label"] for r in a["gpa"]["cumulative_ranks"]] == ["114-1"]


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


def test_grade_points_follow_the_official_table():
    # 教務處註冊組〈成績表說明〉109 年 9 月起的對照表，每一級的上下限都測
    table = {
        100: ("A+", 4.3), 90: ("A+", 4.3), 89: ("A", 4.0), 85: ("A", 4.0), 84: ("A-", 3.7), 80: ("A-", 3.7),
        79: ("B+", 3.3), 77: ("B+", 3.3), 76: ("B", 3.0), 73: ("B", 3.0), 72: ("B-", 2.7), 70: ("B-", 2.7),
        69: ("C+", 2.3), 67: ("C+", 2.3), 66: ("C", 2.0), 63: ("C", 2.0), 62: ("C-", 1.7), 60: ("C-", 1.7),
        59: ("D", 1.0), 50: ("D", 1.0), 49: ("E", 0.0), 1: ("E", 0.0), 0: ("X", 0.0),
    }
    assert {score: grade_point(score) for score in table} == table
    assert grade_point(89.5) == ("A+", 4.3)  # 成績以整數表示，先四捨五入


def test_gpa_uses_the_same_courses_as_the_cumulative_average():
    transcript = parse_transcript_html(TRANSCRIPT_HTML)
    gpa = compute_gpa(transcript)
    # 72（B-）、45（E）、88（A）、50（D）：停修、修課中、通過制、0 學分的不算
    assert gpa["credits"] == 11
    assert gpa["gpa"] == 1.74  # (3×2.7 + 3×0 + 2×4.0 + 3×1.0) / 11
    assert gpa["semesters"] == [{"term": "1132", "gpa": 1.38}, {"term": "1141", "gpa": 2.7}]
    # 假資料的累計平均是隨便寫的，跟算出來的 61.55 對不上
    assert gpa["matches_school_average"] is False
    transcript["cumulative_average"] = 61.55
    assert compute_gpa(transcript)["matches_school_average"] is True


def test_graduate_gpa_leaves_out_undergraduate_courses_and_the_thesis():
    transcript = {
        "program": "碩士班",
        "cumulative_average": 90.0,
        "semesters": [{"term": "1141", "courses": [
            {"name": "機器學習", "program": "碩士班", "credits": 3, "score": 90.0, "remark": ""},
            {"name": "微積分", "program": "學士班", "credits": 3, "score": 60.0, "remark": ""},
            {"name": "碩士論文", "program": "碩士班", "credits": 6, "score": 70.0, "remark": ""},
            {"name": "海外研究", "program": "碩士班", "credits": 3, "score": 75.0, "remark": "境外"},
        ]}],
    }
    gpa = compute_gpa(transcript)
    assert (gpa["gpa"], gpa["credits"], gpa["matches_school_average"]) == (4.3, 3, True)


def test_analysis_includes_gpa_for_each_semester():
    a = analyze_academic_progress(parse_transcript_html(TRANSCRIPT_HTML), None)
    assert (a["gpa"]["gpa"], a["gpa"]["gpa_credits"], a["gpa"]["gpa_matches_school_average"]) == (1.74, 11, False)
    assert [s["gpa"] for s in a["gpa"]["semesters"]] == [1.38, 2.7]
    assert compute_gpa({"program": "學士班", "cumulative_average": None, "semesters": []}) is None


# ============================================================
# GPA 試算
# ============================================================
def _graded(name, credits, score):
    return {"course_no": name, "name": name, "credits": credits, "score": score, "score_text": f"{score:.2f}",
            "program": "學士班", "course_type": "必修", "remark": ""}


def _in_progress(name, credits, score_text="-"):
    return {**_graded(name, credits, 0), "score": None, "score_text": score_text}


def _estimate_transcript():
    """114-1 兩門課：90 分（A+ 4.3）、80 分（A- 3.7），累計 GPA 4.0、平均 85。115-1 還有 6 學分沒有成績。"""
    return {
        "program": "學士班",
        "cumulative_average": 85.0,
        "semesters": [
            {"term": "1141", "label": "114-1", "courses": [_graded("微積分", 3, 90.0), _graded("物理", 3, 80.0)],
             "summary": {"average": 85.0}},
            {"term": "1151", "label": "115-1", "summary": {}, "courses": [
                _in_progress("作業系統", 3), _in_progress("演算法", 3),
                _in_progress("停修的課", 3, "停修"), _in_progress("服務學習", 0),
            ]},
        ],
    }


def _with_current_semester(html):
    """多一個還沒有成績的這學期：成績欄是「未到」，總結寫「學期平均：0」（2026-10 真實成績單的寫法）。"""
    current = _semester_table(115, 1, [
        _course_row("CE9010", "作業系統", "必修", 3, "未到"),
        _course_row("PE9010", "體育", "體育", 0, "未到"),
    ], 0, 0)
    return html.replace("<h5>學期排名</h5>", current + "<h5>學期排名</h5>")


def test_semester_without_grades_has_no_average_not_zero():
    transcript = parse_transcript_html(_with_current_semester(TRANSCRIPT_HTML))
    assert transcript["semesters"][-1]["term"] == "1151"
    assert transcript["semesters"][-1]["summary"]["average"] is None
    a = analyze_academic_progress(transcript, None, "grades")
    # 不會寫成「比上學期退步 75 分」
    assert a["gpa"]["latest_change"] == -5.0
    assert a["gpa"]["semesters"][-1]["average"] is None
    # 「未到」的課是這學期還在修的，0 學分的體育不算 GPA
    assert [c["name"] for c in courses_awaiting_grades(transcript)] == ["修課中", "作業系統"]


def test_courses_awaiting_grades_are_in_progress_courses_that_count_toward_gpa():
    assert [c["name"] for c in courses_awaiting_grades(_estimate_transcript())] == ["作業系統", "演算法"]
    # 測試用的 HTML：成績欄是「-」的修課中
    assert courses_awaiting_grades(parse_transcript_html(TRANSCRIPT_HTML)) == [
        {"term": "1141", "label": "114-1", "name": "修課中", "credits": 2}
    ]


@pytest.mark.parametrize("text, expected", [
    ("A", {"grade": "A", "point": 4.0, "score": None}),
    ("a+", {"grade": "A+", "point": 4.3, "score": None}),
    ("全部 B-", {"grade": "B-", "point": 2.7, "score": None}),
    ("85", {"grade": "A", "point": 4.0, "score": 85.0}),
    ("76.5 分", {"grade": "B+", "point": 3.3, "score": 76.5}),
    ("F", None), ("120", None), ("很好", None),
])
def test_parse_assumed_grade(text, expected):
    assert parse_assumed_grade(text) == expected


def test_estimate_lists_cumulative_gpa_for_each_grade_this_semester():
    e = estimate_gpa(_estimate_transcript())
    assert (e["current_gpa"], e["current_average"], e["credits"]) == (4.0, 85.0, 6)
    assert (e["remaining_source"], e["remaining_credits"], e["scope_text"]) == ("this_semester", 6, "這學期 6 學分")
    assert [c["name"] for c in e["remaining_courses"]] == ["作業系統", "演算法"]
    # (24 + 6 × 積分) / 12
    assert [(s["grade"], s["gpa"]) for s in e["scenarios"]] == [
        ("A+", 4.15), ("A", 4.0), ("A-", 3.85), ("B+", 3.65), ("B", 3.5), ("B-", 3.35),
    ]
    assert e["scenarios"][1]["label"] == "A（85～89 分）"
    assert e["answers"] == [] and e["notes"] == []


def test_estimate_with_an_assumed_grade():
    [answer] = estimate_gpa(_estimate_transcript(), assumed_grade="B")["answers"]
    assert answer == "這學期 6 學分都拿 B（73～76 分）的話，累計 GPA 會從 4.00 變成 3.50。"
    # 給分數的話累計平均也算得出來：(85 × 6 + 75 × 6) / 12
    [answer] = estimate_gpa(_estimate_transcript(), assumed_grade="75")["answers"]
    assert answer == "這學期 6 學分都考 75 分（B，3.0）的話，累計 GPA 會從 4.00 變成 3.50，累計平均從 85.00 變成 80.00。"
    assert "看不懂「很好」" in estimate_gpa(_estimate_transcript(), assumed_grade="很好")["answers"][0]


@pytest.mark.parametrize("target, expected", [
    # (4.1 × 12 − 24) / 6 = 4.2，只有 A+ 夠
    (4.1, "累計 GPA 要到 4.10，這學期 6 學分的 GPA 要 4.20 以上，要每科都拿 A+（90 分以上）才到得了。"),
    # 已經超過目標：(3.5 × 12 − 24) / 6 = 3.0
    (3.5, "目前累計 GPA 4.00，已經在 3.50 以上，要維持的話這學期 6 學分的 GPA 要 3.00 以上，大約每科都拿 B（73～76 分）以上就夠。"),
    (1.5, "目前累計 GPA 4.00，這學期 6 學分不管拿幾分，累計 GPA 都會在 1.50 以上。"),
    # 全拿 A+ 只到 4.15。(24 + 4.3 × 30) / 36 = 4.25：再修 24 學分
    (4.25, "這學期 6 學分全拿 A+（4.3）累計 GPA 也只到 4.15，到不了 4.25。之後的課也全拿 A+ 的話，還要再修 24 學分才到得了。"),
    (4.5, "GPA 最高是 4.3（每科都 A+），沒辦法到 4.50。"),
])
def test_estimate_target_gpa(target, expected):
    assert estimate_gpa(_estimate_transcript(), target_gpa=target)["answers"] == [expected]


def test_estimate_target_average():
    # (88 × 12 − 85 × 6) / 6 = 91
    assert estimate_gpa(_estimate_transcript(), target_average=88)["answers"] == [
        "累計平均要到 88，這學期 6 學分平均要考 91.00 分以上。"
    ]
    assert estimate_gpa(_estimate_transcript(), target_average=95)["answers"] == [
        "這學期 6 學分全考 100 分累計平均也只到 92.50，到不了 95。之後的課也全考 100 分的話，還要再修 6 學分才到得了。"
    ]


def test_estimate_remaining_credits_from_user_or_graduation_report():
    e = estimate_gpa(_estimate_transcript(), credits=30, target_gpa=3.9)
    assert (e["remaining_source"], e["scope_text"], e["remaining_courses"]) == ("user", "接下來 30 學分", [])

    graduation = {"total_required": 128, "total_earned": 100}
    e = estimate_gpa(_estimate_transcript(), graduation, remaining="until_graduation")
    assert (e["remaining_credits"], e["scope_text"]) == (28, "畢業前剩下 28 學分")
    assert "通過制" in e["notes"][0]

    # 畢業資格審查表取不到時改用這學期的課，並說明
    e = estimate_gpa(_estimate_transcript(), None, remaining="until_graduation")
    assert e["remaining_credits"] == 6 and "暫時取不到" in e["notes"][0]


def test_estimate_without_courses_in_progress_asks_for_credits():
    transcript = _estimate_transcript()
    transcript["semesters"] = transcript["semesters"][:1]
    e = estimate_gpa(transcript, target_gpa=3.8)
    assert e["scenarios"] == []
    assert e["answers"][0].startswith("成績單上看不到還沒有成績的課")
    assert estimate_gpa({"program": "學士班", "semesters": []}) is None


def test_analysis_includes_estimate_and_passes_its_options():
    transcript = {**_estimate_transcript(), "department": "", "grade": "", "cumulative_credits": 6, "ranks": {}}
    a = analyze_academic_progress(transcript, None)
    assert a["estimate"]["remaining_credits"] == 6 and a["estimate"]["answers"] == []

    a = analyze_academic_progress(transcript, None, "estimate", {"target_gpa": 3.5})
    assert a["focus"] == "estimate"
    assert a["estimate"]["answers"][0].startswith("目前累計 GPA 4.00，已經在 3.50 以上")
