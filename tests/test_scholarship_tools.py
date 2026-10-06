"""獎學金推薦的合併、比對邏輯測試，用手寫的假資料，不需要登入、API key 跟 data/：
    python -m pytest tests/test_scholarship_tools.py
"""

from datetime import date

import pytest

from backend.analysis import scholarship_tools as s
from backend.rag.rag_documents import CatalogDocument


def _item(**overrides):
    """模型整理出來的一筆獎學金（欄位跟 SCHOLARSHIP_SCHEMA 一樣），預設是全校都能申請、沒有門檻。"""
    item = {
        "name": "測試獎學金", "kind": "獎學金", "amount": "", "quota": "",
        "groups": [], "units": [],
        "grades": {"basis": "未說明", "min_average": 0},
        "rank": {"basis": "未說明", "scope": "未說明", "percent": 0, "top": 0},
        "no_failing": False, "conduct_min": 0, "nationality": "不限", "conditions": [],
        "eligibility": "", "period": "", "deadline": "", "how_to_apply": "", "notes": "",
    }
    item.update(overrides)
    return item


def _indexed(**overrides):
    """合併過、可以拿去比對的一筆（多了 deadline_term 跟 sources）。"""
    return {**_item(**overrides), "deadline_term": overrides.get("deadline_term", ""), "sources": []}


def _profile(**overrides):
    profile = {
        "department": "資訊工程學系", "college": "資訊電機學院", "degree": "學士班", "grade": 3,
        "semesters": [
            s.Semester("1141", "114-1", 84.0, 20, "5/52", "12/110", []),
            s.Semester("1142", "114-2", 86.5, 22, "7/52", "15/110", []),
        ],
        "cumulative_average": 83.2, "cumulative_class_rank": "6/52", "cumulative_dept_rank": "14/110",
        "previous_year": 114,
    }
    profile.update(overrides)
    return s.StudentProfile(**profile)


# ============================================================
# 名稱、系所
# ============================================================
@pytest.mark.parametrize("a, b", [
    ("國立中央大學羅家倫校長紀念獎學金辦法", "羅家倫校長紀念獎學金"),
    ("宏惠光電股份有限公司獎助學金實施辦法", "宏惠光電股份有限公司獎助金"),
    ("國立中央大學學業成績優良書卷獎獎勵辦法", "國立中央大學學業成績優良書卷獎"),
    ("國立中央大學學生傑出領導獎學金實施辦法", "國立中央大學學生傑出領導獎"),
    ("國立中央大學朱順一合勤獎學金（學業優良）", "朱順一合勤獎學金(學業優良)"),
    ("中大學術基金會-林燕友誼奬學金", "中大學術基金會林燕友誼獎學金"),
])
def test_scholarship_key_matches_names_from_different_documents(a, b):
    assert s.scholarship_key(a) == s.scholarship_key(b)


def test_scholarship_key_keeps_sub_awards_apart_unless_base():
    academic = "朱順一合勤獎學金（學業優良）"
    sports = "朱順一合勤獎學金（運動績優）"
    assert s.scholarship_key(academic) != s.scholarship_key(sports)
    assert s.scholarship_key(academic, base=True) == s.scholarship_key(sports, base=True) == s.scholarship_key("朱順一合勤獎學金")


@pytest.mark.parametrize("text, expected", [
    ("資訊電機學院資訊工程學系", ("資訊工程學系", "資訊電機學院")),  # iNCU 成績單的系所是學院加系所
    ("資訊電機學院資訊工程學系 / 不分組", ("資訊工程學系", "資訊電機學院")),  # 2026-10 真實成績單的寫法
    ("物理系", ("物理學系", "理學院")),
    ("電機資訊學院", ("資訊電機學院", "資訊電機學院")),  # 一覽表寫的是「電機資訊學院」
    ("管理學院", ("管理學院", "管理學院")),
    ("本校女子籃球隊", ("", "")),
])
def test_resolve_unit(text, expected):
    assert s.resolve_unit(text) == expected


@pytest.mark.parametrize("text, expected", [("三年A班", 3), ("一年級", 1), ("碩一", 1), ("博二", 2), ("", 0)])
def test_parse_grade(text, expected):
    assert s.parse_grade(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("學士班", "學士班"), ("碩士班", "碩士班"), ("碩士在職專班", "在職專班"), ("博士班", "博士班"), ("", ""),
])
def test_parse_degree(text, expected):
    assert s.parse_degree(text) == expected


# ============================================================
# 學生資料
# ============================================================
def _course(name, score, credits=3):
    return {"course_no": name, "name": name, "credits": credits, "score": score,
            "score_text": str(score), "program": "學士班", "course_type": "必修"}


def test_build_profile_reads_department_grade_and_completed_semesters():
    transcript = {
        "department": "資訊電機學院資訊工程學系", "grade": "三年A班", "program": "學士班",
        "cumulative_average": 83.2,
        "semesters": [
            {"term": "1141", "label": "114-1", "courses": [_course("微積分", 90)],
             "summary": {"average": 84.0, "attempted_credits": 20, "earned_credits": 20}},
            {"term": "1142", "label": "114-2", "courses": [_course("物理", 55)],
             "summary": {"average": 86.5, "attempted_credits": 22, "earned_credits": 19}},
            {"term": "1143", "label": "114-3", "courses": [_course("暑修", 70)],
             "summary": {"average": 70.0, "attempted_credits": 2, "earned_credits": 2}},
            {"term": "1151", "label": "115-1", "courses": [_course("作業系統", None)], "summary": {}},
        ],
        "ranks": {
            "semester": {"1141": {"average": 84.0, "class_rank": "5/52", "dept_rank": "12/110"},
                         "1142": {"average": 86.5, "class_rank": "7/52", "dept_rank": "15/110"}},
            # 還沒有成績的 115-1 也有一列累計排名，不能拿來用
            "cumulative": {"1141": {"class_rank": "8/52"}, "1142": {"class_rank": "6/52", "dept_rank": "14/110"},
                           "1151": {"class_rank": "1/52", "dept_rank": "1/110"}},
        },
    }
    profile = s.build_profile(transcript, today=date(2026, 10, 6))

    assert (profile.department, profile.college, profile.degree, profile.grade) == ("資訊工程學系", "資訊電機學院", "學士班", 3)
    assert profile.previous_year == 114
    # 暑修跟還沒有成績的這學期不算
    assert [sem.label for sem in profile.semesters] == ["114-1", "114-2"]
    assert profile.semesters[1].failed == ["物理"]
    assert profile.semesters[1].class_rank == "7/52"
    assert (profile.cumulative_class_rank, profile.cumulative_dept_rank) == ("6/52", "14/110")


def test_withdrawn_course_is_not_a_failing_grade():
    # 「無不及格科目」只看有成績的課，停修沒有成績（2026-10 實測時停修被當成不及格，誤判不符合）
    withdrawn = {**_course("演算法", None), "score_text": "停修"}
    transcript = {
        "semesters": [{"term": "1142", "label": "114-2", "courses": [withdrawn, _course("物理", 55)],
                       "summary": {"average": 81.69, "attempted_credits": 16}}],
        "ranks": {},
    }
    profile = s.build_profile(transcript, today=date(2026, 10, 6))
    assert profile.semesters[0].failed == ["物理"]

    only_withdrawn = {**transcript, "semesters": [{**transcript["semesters"][0], "courses": [withdrawn]}]}
    item = _indexed(no_failing=True, grades={"basis": "前一學期", "min_average": 75})
    assert s.evaluate(item, s.build_profile(only_withdrawn, today=date(2026, 10, 6)))[0] == "eligible"


def test_previous_year_follows_the_academic_calendar():
    assert s.build_profile({}, today=date(2026, 10, 6)).previous_year == 114  # 115-1
    assert s.build_profile({}, today=date(2027, 1, 20)).previous_year == 114  # 115-1 的 1 月
    assert s.build_profile({}, today=date(2027, 3, 1)).previous_year == 114  # 115-2


# ============================================================
# 比對
# ============================================================
def test_rank_requirement_on_both_semesters_of_last_year():
    # 似鳥國際獎學金：限管理學院、資電學院大二至碩二，前一學年上下學期班排都在前 20%
    item = _indexed(
        groups=[{"degree": "學士班", "min_grade": 2, "max_grade": 0}, {"degree": "碩士班", "min_grade": 1, "max_grade": 2}],
        units=["管理學院", "資訊電機學院"],
        rank={"basis": "前一學年每學期", "scope": "班", "percent": 20, "top": 0},
    )
    status, checks, pending = s.evaluate(item, _profile())
    assert status == "eligible" and pending == []
    assert all(c["ok"] for c in checks)
    assert any("114-2 7/52，前 13.5%" in c["label"] for c in checks)

    second_semester_too_low = _profile(semesters=[
        s.Semester("1141", "114-1", 84.0, 20, "5/52", "", []),
        s.Semester("1142", "114-2", 80.0, 22, "15/52", "", []),
    ])
    assert s.evaluate(item, second_semester_too_low)[0] == "ineligible"


def test_unit_and_grade_mismatch_are_ineligible():
    other_department = _indexed(units=["物理學系"])
    assert s.evaluate(other_department, _profile())[0] == "ineligible"
    sophomores_only = _indexed(groups=[{"degree": "學士班", "min_grade": 2, "max_grade": 2}])
    assert s.evaluate(sophomores_only, _profile())[0] == "ineligible"
    graduate_only = _indexed(groups=[{"degree": "碩士班", "min_grade": 0, "max_grade": 0}])
    assert s.evaluate(graduate_only, _profile())[0] == "ineligible"


def test_unknown_unit_or_department_cannot_be_decided():
    assert s.evaluate(_indexed(units=["本校女子籃球隊"]), _profile())[0] == "maybe"
    unknown_department = _profile(department="測試學院測試學系", college="")
    assert s.evaluate(_indexed(units=["物理學系"]), unknown_department)[0] == "maybe"


def test_year_average_is_weighted_by_credits():
    item = _indexed(grades={"basis": "前一學年", "min_average": 85})
    status, checks, _ = s.evaluate(item, _profile())
    # (84.0 * 20 + 86.5 * 22) / 42 = 85.31
    assert status == "eligible"
    assert "114 學年平均 85 分以上（你：85.31）" in checks[0]["label"]
    assert s.evaluate(_indexed(grades={"basis": "前一學年", "min_average": 86}), _profile())[0] == "ineligible"


def test_last_semester_basis_uses_latest_semester():
    assert s.evaluate(_indexed(grades={"basis": "前一學期", "min_average": 86}), _profile())[0] == "eligible"
    assert s.evaluate(_indexed(grades={"basis": "未說明", "min_average": 87}), _profile())[0] == "ineligible"


def test_missing_grades_make_it_maybe_not_eligible():
    freshman = _profile(grade=1, semesters=[], cumulative_average=None)
    assert s.evaluate(_indexed(grades={"basis": "前一學年", "min_average": 80}), freshman)[0] == "maybe"


def test_book_award_top_five_percent_and_top_n():
    book_award = _indexed(rank={"basis": "前一學期", "scope": "班", "percent": 5, "top": 0})
    top_two = _profile(semesters=[s.Semester("1142", "114-2", 90.0, 20, "2/52", "", [])])
    third = _profile(semesters=[s.Semester("1142", "114-2", 89.0, 20, "3/52", "", [])])
    assert s.evaluate(book_award, top_two)[0] == "eligible"
    assert s.evaluate(book_award, third)[0] == "ineligible"  # 3/52 = 5.8%

    top_three = _indexed(rank={"basis": "未說明", "scope": "班", "percent": 0, "top": 3})
    assert s.evaluate(top_three, third)[0] == "eligible"
    assert s.evaluate(top_three, _profile())[0] == "ineligible"  # 7/52


def test_last_year_rank_with_one_semester_in_and_one_out_cannot_be_decided():
    item = _indexed(rank={"basis": "前一學年", "scope": "班", "percent": 10, "top": 0})
    assert s.evaluate(item, _profile())[0] == "maybe"  # 5/52 在前 10%，7/52 不在


def test_failed_course_breaks_no_failing_requirement():
    item = _indexed(no_failing=True, grades={"basis": "前一學期", "min_average": 75})
    failed = _profile(semesters=[s.Semester("1142", "114-2", 80.0, 20, "", "", ["物理"])])
    status, checks, _ = s.evaluate(item, failed)
    assert status == "ineligible"
    assert any("物理" in c["label"] and c["ok"] is False for c in checks)


def test_identity_conditions_need_confirmation_unless_declared():
    item = _indexed(
        groups=[{"degree": "學士班", "min_grade": 2, "max_grade": 0}],
        grades={"basis": "前一學年", "min_average": 80},
        conditions=[{"kind": "經濟弱勢", "text": "家境清寒"}],
    )
    status, _, pending = s.evaluate(item, _profile())
    assert status == "maybe" and pending == [{"kind": "經濟弱勢", "text": "家境清寒"}]

    status, checks, pending = s.evaluate(item, _profile(), ("經濟弱勢",))
    assert status == "eligible" and pending == []
    assert "家境清寒（你說明過是經濟弱勢）" in [c["label"] for c in checks]


def test_nationality_restrictions():
    foreign_only = _indexed(nationality="外籍生")
    assert s.evaluate(foreign_only, _profile())[0] == "ineligible"
    assert s.evaluate(foreign_only, _profile(), ("外籍生",))[0] == "eligible"
    domestic_only = _indexed(nationality="本國籍", groups=[{"degree": "學士班", "min_grade": 0, "max_grade": 0}])
    assert s.evaluate(domestic_only, _profile())[0] == "eligible"
    assert s.evaluate(domestic_only, _profile(), ("僑生",))[0] == "ineligible"


def test_scholarship_without_any_requirement_is_not_eligible_for_everyone():
    # 一覽表上只寫「實際資訊以來文公告為主」的基金會獎學金
    status, _, pending = s.evaluate(_indexed(name="某基金會獎學金"), _profile())
    assert status == "maybe"
    assert pending[0]["kind"] == "其他"


# ============================================================
# 合併
# ============================================================
def _source(file):
    return {"title": file, "file": file, "url": ""}


def _rule(file="學務處生活輔導組/辦法.pdf", **overrides):
    return {**_item(**overrides), "_source": _source(file), "_list": False, "_term": ""}


def _listed(term, file="理學院/一覽表.pdf", **overrides):
    return {**_item(**overrides), "_source": _source(file), "_list": True, "_term": term}


def test_list_entry_merges_into_rule_and_brings_current_deadline_and_amount():
    rule = _rule(name="國立中央大學羅家倫校長紀念獎學金", amount="每名十萬元", conditions=[{"kind": "經濟弱勢", "text": "清寒"}])
    old_list = _listed("", file="學務處生活輔導組/校內獎學金一覽.md", name="國立中央大學羅家倫校長紀念獎學金", amount="100,000 元")
    current = _listed("115-1", name="羅家倫校長紀念獎學金", amount="10 萬", deadline="9/21")

    [merged] = s.merge_entries([rule], [old_list, current])

    assert merged["conditions"] == [{"kind": "經濟弱勢", "text": "清寒"}]  # 資格以辦法為準
    assert (merged["amount"], merged["deadline"], merged["deadline_term"]) == ("10 萬", "9/21", "115-1")
    assert [src["file"] for src in merged["sources"]] == [
        "學務處生活輔導組/辦法.pdf", "理學院/一覽表.pdf", "學務處生活輔導組/校內獎學金一覽.md",
    ]


def test_same_rule_on_several_unit_websites_is_merged():
    a = _rule(name="學生傑出領導獎學金", file="學生事務處/a.pdf")
    b = _rule(name="國立中央大學學生傑出領導獎學金", file="學務處職涯發展中心/b.pdf", amount="2 萬")
    [merged] = s.merge_entries([a, b], [])
    assert merged["amount"] == "2 萬"
    assert len(merged["sources"]) == 2


def test_department_scholarships_with_the_same_name_stay_apart():
    physics = _rule(name="研究生獎助學金", units=["物理學系"], file="物理學系/a.pdf")
    chemistry = _rule(name="研究生獎助學金", units=["化學系"], file="化學系/b.pdf")
    assert len(s.merge_entries([physics, chemistry], [])) == 2


def test_list_split_into_sub_awards_only_brings_deadline():
    # 辦法沒分獎項，一覽表分成亞洲、其他地區：併進辦法，但金額以辦法為準
    rule = _rule(name="國際服務學習獎學金", amount="2.5 萬到 3.5 萬")
    asia = _listed("115-1", name="國際服務學習獎學金（亞洲地區）", amount="25,000", deadline="10/2")
    other = _listed("115-1", name="國際服務學習獎學金（其他地區）", amount="35,000", deadline="10/2")
    [merged] = s.merge_entries([rule], [asia, other])
    assert (merged["amount"], merged["deadline"]) == ("2.5 萬到 3.5 萬", "10/2")


def test_list_only_scholarship_takes_conditions_from_every_list():
    # 數學系第一屆校友獎學金：115-1 一覽表只寫「家境清寒者優先」，校內獎學金一覽寫「獎助大二以上清寒學生」
    current = _listed("115-1", name="數學系第一屆校友獎學金", deadline="10/2")
    summary = _listed("", file="學務處生活輔導組/校內獎學金一覽.md", name="數學系第一屆校友獎學金",
                      conditions=[{"kind": "經濟弱勢", "text": "清寒"}])
    [merged] = s.merge_entries([], [summary, current])
    assert merged["deadline"] == "10/2"
    assert merged["conditions"] == [{"kind": "經濟弱勢", "text": "清寒"}]


def test_list_only_scholarship_is_kept():
    [merged] = s.merge_entries([], [_listed("115-1", name="似鳥國際獎學金", deadline="9/21")])
    assert (merged["name"], merged["deadline_term"]) == ("似鳥國際獎學金", "115-1")


def _merged(name, amount="", units=(), file="a.pdf", **overrides):
    return {**_indexed(name=name, amount=amount, units=list(units), **overrides), "sources": [_source(file)]}


def test_dedupe_merges_the_same_scholarship_written_differently():
    index = [
        _merged("學生生活助學金", "每月 6,000 元", file="生輔組/辦法.pdf"),
        _merged("生活助學金", "每月6,000元", file="生輔組/說明.md", deadline="10/2", deadline_term="115-1"),
        _merged("理學院提升學生外文能力獎勵（報名參加英語考試者）", "500元", units=["理學院"], file="理學院/a.pdf"),
        _merged("學生提昇外文能力獎勵（報名參加英語考試者）", "新臺幣500元", units=["理學院"], file="語言中心/b.pdf"),
        _merged("大學部學生修習全英語授課(EMI)課程獎勵", "每一門500元", units=["理學院"], file="語言中心/b.pdf"),
        _merged("理學院提升學生外文能力獎勵（大學部學生修習全英語授課(EMI)課程獎勵）", "每一門500元", units=["理學院"]),
    ]
    result = s.dedupe(index)
    assert [i["name"] for i in result] == [
        "學生生活助學金", "理學院提升學生外文能力獎勵（報名參加英語考試者）", "大學部學生修習全英語授課(EMI)課程獎勵",
    ]
    assert (result[0]["deadline"], result[0]["deadline_term"]) == ("10/2", "115-1")
    assert [src["file"] for src in result[0]["sources"]] == ["生輔組/辦法.pdf", "生輔組/說明.md"]


def test_dedupe_keeps_different_scholarships_apart():
    index = [
        _merged("工學院提升學生英語能力獎勵（多益TOEIC 800分以上）", "1500 元", units=["工學院"]),
        _merged("工學院提升學生英語能力獎勵（托福iBT 90分以上）", "1500 元", units=["工學院"]),
        _merged("勵學獎學金", "至多 1 萬"),
        _merged("教育部清寒優秀學生勵學獎學金（B類）", "每名 10,000 元/月"),
        _merged("研究生獎助學金", units=["物理學系"]),
        _merged("研究生獎助學金", units=["化學系"]),
        _merged("學生傑出領導獎學金", "2 萬"),
        _merged("傑出領導獎學金", "10 萬"),
        _merged("地球科學學院研究生獎學金", units=["地球科學學院"]),
        _merged("地球科學學院研究生助學金", units=["地球科學學院"]),
    ]
    assert len(s.dedupe(index)) == len(index)


def test_rule_deadline_without_term_becomes_period():
    [merged] = s.merge_entries([_rule(name="某獎學金", deadline="每年 3 月 31 日")], [])
    assert (merged["deadline"], merged["period"]) == ("", "每年 3 月 31 日")


# ============================================================
# 截止日、金額、整份結果
# ============================================================
@pytest.mark.parametrize("deadline, term, expected", [
    ("9/21", "115-1", date(2026, 9, 21)),
    ("1/5", "115-1", date(2027, 1, 5)),
    ("3/10", "115-2", date(2027, 3, 10)),
    ("依系辦公告", "115-1", None),
    ("10/2", "", None),
])
def test_deadline_date(deadline, term, expected):
    assert s.deadline_date(deadline, term) == expected


@pytest.mark.parametrize("text, expected", [
    ("10 萬", 100000), ("100,000 元", 100000), ("1年至多50萬", 500000), ("5 千", 5000),
    ("學期間以每月3,000元為原則", 3000), ("依經費額度而定", 0),
])
def test_amount_value(text, expected):
    assert s.amount_value(text) == expected


def test_match_scholarships_groups_and_sorts_results():
    index = [
        _indexed(name="小獎學金", amount="1 萬", groups=[{"degree": "學士班", "min_grade": 0, "max_grade": 0}]),
        _indexed(name="大獎學金", amount="10 萬", groups=[{"degree": "學士班", "min_grade": 0, "max_grade": 0}]),
        _indexed(name="已截止的獎學金", amount="20 萬", deadline="9/21", deadline_term="115-1",
                 groups=[{"degree": "學士班", "min_grade": 0, "max_grade": 0}]),
        _indexed(name="清寒獎學金", amount="3 萬", conditions=[{"kind": "經濟弱勢", "text": "清寒"}]),
        _indexed(name="研究生獎學金", groups=[{"degree": "碩士班", "min_grade": 0, "max_grade": 0}]),
        _indexed(name="急難救助金", kind="急難救助"),
    ]
    result = s.match_scholarships(index, _profile(), statuses=["原住民", "不是身分"], today=date(2026, 10, 6))

    assert result["kind"] == "scholarship_recommendations"
    assert [e["name"] for e in result["eligible"]] == ["大獎學金", "小獎學金", "已截止的獎學金"]
    assert result["eligible"][2]["deadline_passed"] is True
    assert [e["name"] for e in result["maybe"]] == ["清寒獎學金"]
    assert result["excluded_count"] == 1  # 研究生獎學金；急難救助不推薦也不算
    assert result["statuses"] == ["原住民"]
    assert result["profile"]["year_average"] == 85.31


def test_card_text_has_no_semicolons():
    # 辦法常用分號隔開一條一條規定，使用者不喜歡分號，每段換成句號
    index = [_indexed(name="測試獎學金", notes="不含延修生；審查時須具在學身分；",
                      conditions=[{"kind": "經濟弱勢", "text": "低收入戶；中低收入戶"}])]
    entry = s.match_scholarships(index, _profile(), today=date(2026, 10, 6))["maybe"][0]
    assert entry["notes"] == "不含延修生。審查時須具在學身分。"
    assert entry["pending"] == [{"kind": "經濟弱勢", "text": "低收入戶。中低收入戶"}]


def test_tiers_of_the_same_scholarship_become_one_card():
    def english(score):
        return dict(units=["工學院"], conditions=[
            {"kind": "語言檢定", "text": "在學期間通過英檢"}, {"kind": "語言檢定", "text": score},
        ])

    index = [
        _indexed(name="工學院提升學生英語能力獎勵（多益TOEIC 800分以上）", amount="NT$ 1500 元", **english("多益 800")),
        _indexed(name="工學院提升學生英語能力獎勵（托福iBT 90分以上）", amount="NT$ 1500 元", **english("托福 90")),
        _indexed(name="工學院提升學生英語能力獎勵（雅思測驗7.0級以上）", amount="NT$ 1000 元", **english("雅思 7")),
        # 沒分獎項的總稱跟分了的同時出現，只留分了的
        _indexed(name="不利處境學生助學金", conditions=[{"kind": "經濟弱勢", "text": "家庭年收入 70 萬以下"}]),
        _indexed(name="不利處境學生助學金（學士班）", amount="2 萬",
                 groups=[{"degree": "學士班", "min_grade": 0, "max_grade": 0}],
                 conditions=[{"kind": "經濟弱勢", "text": "家庭年收入 90 萬以下"}]),
    ]
    mechanical = _profile(department="機械工程學系", college="工學院")
    result = s.match_scholarships(index, mechanical, today=date(2026, 10, 6))

    names = [e["name"] for e in result["maybe"]]
    assert sorted(names) == ["不利處境學生助學金（學士班）", "工學院提升學生英語能力獎勵"]
    card = next(e for e in result["maybe"] if e["name"] == "工學院提升學生英語能力獎勵")
    # 每個獎項自己的條件跟著獎項，大家都要的留在 pending（pending 是全部條件，前端分組用）
    assert card["tiers"] == [
        {"name": "多益TOEIC 800分以上", "amount": "NT$ 1500 元", "conditions": ["多益 800"]},
        {"name": "托福iBT 90分以上", "amount": "NT$ 1500 元", "conditions": ["托福 90"]},
        {"name": "雅思測驗7.0級以上", "amount": "NT$ 1000 元", "conditions": ["雅思 7"]},
    ]
    assert [p["text"] for p in card["pending"]] == ["在學期間通過英檢", "多益 800", "托福 90", "雅思 7"]
    assert all("_family" not in e for e in result["maybe"])


# ============================================================
# 整理資格的快取
# ============================================================
def _doc(doc_id, title, file_name, doc_type="法規辦法", superseded_by=""):
    card = {"title": title, "doc_type": doc_type, "issuer": "", "scope": "", "applies_to": "",
            "version": "", "summary": "", "answers": []}
    return CatalogDocument(doc_id, file_name, f"{title}的內容", card, superseded_by=superseded_by)


def test_candidate_documents_skip_forms_old_versions_and_unrelated_documents():
    docs = [
        _doc("D1", "學業成績優良書卷獎獎勵辦法", "學務處生活輔導組/a.pdf"),
        _doc("D2", "憶冬獎學金申請表", "中國文學系/b.doc", doc_type="申請表單"),
        _doc("D3", "羅家倫校長紀念獎學金辦法", "學生事務處/c.pdf", superseded_by="D1"),
        _doc("D4", "學生請假規則", "教務處/d.pdf"),
        _doc("D5", "校內獎學金一覽", "學務處生活輔導組/校內獎學金一覽.md", doc_type="其他"),
    ]
    assert [d.doc_id for d in s.candidate_documents(docs)] == ["D1", "D5"]


def test_extractions_are_cached_by_content(tmp_path, monkeypatch):
    calls = []

    def fake_extract(doc):
        calls.append(doc.doc_id)
        return {"document_kind": "辦法", "term": "", "scholarships": [_item(name=doc.card["title"])]}

    monkeypatch.setattr(s, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(s, "extract_scholarships", fake_extract)
    docs = [_doc("D1", "甲獎學金", "a/甲.pdf"), _doc("D2", "乙獎學金", "b/乙.pdf")]

    assert [r["scholarships"][0]["name"] for _, r in s.load_extractions(docs, max_workers=2)] == ["甲獎學金", "乙獎學金"]
    assert sorted(calls) == ["D1", "D2"]
    s.load_extractions(docs, max_workers=2)
    assert len(calls) == 2  # 第二次都從快取讀

    monkeypatch.setattr(s, "SCHOLARSHIP_VERSION", "test-next")
    s.load_extractions(docs, max_workers=2)
    assert len(calls) == 4  # 版本號變了就重新整理


def test_failed_extraction_is_skipped_and_retried_next_time(tmp_path, monkeypatch):
    def broken(doc):
        raise RuntimeError("沒有網路")

    monkeypatch.setattr(s, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(s, "extract_scholarships", broken)
    assert s.load_extractions([_doc("D1", "甲獎學金", "a/甲.pdf")]) == []
    assert list(tmp_path.iterdir()) == []
