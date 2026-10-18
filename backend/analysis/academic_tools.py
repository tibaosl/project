"""學業分析：抓 iNCU「學生成績查詢」跟「畢業資格審查表」，整理成已修學分、
畢業學分缺口、學期平均趨勢、不及格/停修提醒。

抓取（需要登入的 NCUSession）跟解析/分析（純函式，吃 HTML 字串）分開，
解析跟分析的部分不用登入就能測試，見 test_academic_tools.py。
"""

import json
import math
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from backend.logging_config import make_print_logger

print = make_print_logger(__name__)

INCU_TRANSCRIPT_URL = "https://cis.ncu.edu.tw/iNCU/academic/score/transcriptQuery"
INCU_GRADUATE_REPORT_URL = "https://cis.ncu.edu.tw/iNCU/academic/graduate/graduateReport"

# 學士班 60 分及格，研究所 70 分
PASS_SCORE = {"學士班": 60.0}
DEFAULT_GRADUATE_PASS_SCORE = 70.0

TERM_TITLE_RE = re.compile(r"第\s*(\d+)\s*學年度第\s*(\d)\s*學期")

# 109 年 9 月起的百分制轉等第積分（教務處註冊組〈成績表說明〉參、一、（二）），由高往低找第一個符合的下限
GRADE_POINTS = (
    (90, "A+", 4.3), (85, "A", 4.0), (80, "A-", 3.7), (77, "B+", 3.3), (73, "B", 3.0), (70, "B-", 2.7),
    (67, "C+", 2.3), (63, "C", 2.0), (60, "C-", 1.7), (50, "D", 1.0), (1, "E", 0.0), (0, "X", 0.0),
)
# 研究生的學位論文不算 GPA（同一份說明的備註 3）
THESIS_RE = re.compile(r"^(碩士|博士)?(學位)?論文$")
FAILED_SCORE_KEYWORDS = ("停修", "不及格", "未通過", "棄修")


# ============================================================
# 抓取
# ============================================================

async def _load_incu_page(session, url: str, ready_selector: str) -> str:
    page = await session.open_incu_home()
    await page.goto(url, wait_until="networkidle")
    await session._handle_oauth_consent_if_present(page)

    if "login" in page.url:
        raise RuntimeError("iNCU 需要登入，但目前 session 無效（被導回登入頁）。")
    if not page.url.startswith(url):
        await page.goto(url, wait_until="networkidle")

    await page.wait_for_selector(ready_selector, timeout=15000)
    return await page.content()


async def fetch_academic_records(session, include_graduation: bool = True) -> tuple[dict, Optional[dict]]:
    """回傳 (成績, 畢業審查)。畢業審查頁只是參考資料，抓不到時回傳 None，不影響成績分析。"""
    print("[iNCU] 正在取得學生成績...")
    transcript = parse_transcript_html(
        await _load_incu_page(session, INCU_TRANSCRIPT_URL, "text=學生基本資料")
    )
    if not include_graduation:
        return transcript, None

    try:
        graduation = await fetch_graduation_report(session)
    except Exception as exc:
        print(f"[iNCU] 畢業資格審查表取得失敗，只分析成績：{exc}")
        graduation = None

    return transcript, graduation


async def fetch_graduation_report(session) -> dict:
    print("[iNCU] 正在取得畢業資格審查表...")
    return parse_graduation_report_html(
        await _load_incu_page(session, INCU_GRADUATE_REPORT_URL, ".total-credits")
    )


# ============================================================
# 解析：學生成績查詢
# ============================================================

def _to_float(text: Any) -> Optional[float]:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    number = _to_float(value)
    return int(number) if number is not None else 0


def _cell_texts(tr) -> list[str]:
    return [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]


def _parse_basic_info(soup: BeautifulSoup) -> dict[str, str]:
    info = {}
    for label in soup.select(".fmlabel"):
        value = label.find_next_sibling(class_="fminput")
        if value is not None:
            info[label.get_text(strip=True)] = value.get_text(" ", strip=True)
    return info


def _parse_semester_table(table, term: str, label: str) -> dict:
    rows = table.find_all("tr")
    header = next((_cell_texts(tr) for tr in rows if "課號" in _cell_texts(tr)), None)
    if header is None:
        return {"term": term, "label": label, "courses": [], "summary": {}}
    col = {name: i for i, name in enumerate(header)}

    courses = []
    summary = {}
    for tr in rows:
        cells = tr.find_all("td")
        if len(cells) == 1:
            summary = _parse_semester_summary(cells[0].get_text(" ", strip=True))
            continue
        if len(cells) < len(header):
            continue

        names = cells[col["課名"]].get_text("\n", strip=True).split("\n")
        score_text = cells[col["成績"]].get_text(" ", strip=True)
        courses.append({
            "course_no": cells[col["課號"]].get_text(strip=True),
            "name": names[0],
            "name_en": " ".join(names[1:]),
            "course_type": cells[col["課程屬性"]].get_text(strip=True),
            "program": cells[col["學制屬性"]].get_text(strip=True),
            "credits": _to_int(cells[col["學分數"]].get_text(strip=True)),
            "score": _to_float(score_text),
            "score_text": score_text,
            "remark": cells[col["備註"]].get_text(" ", strip=True).strip("-").strip(),
        })

    # 還沒有成績的這學期，iNCU 的總結寫「學期平均：0」（成績欄是「未到」），不是真的考 0 分。
    # 當成 0 的話學業分析會寫「比上學期退步 80 幾分」，獎學金的「前一學期平均」也會拿 0 分去比（2026-10 實測）
    if summary.get("average") == 0 and not any(c["score"] is not None for c in courses):
        summary["average"] = None

    return {"term": term, "label": label, "courses": courses, "summary": summary}


def _parse_semester_summary(text: str) -> dict:
    def grab(pattern):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    return {
        "average": _to_float(grab(r"學期平均\s*：\s*([\d.]+)")),
        "attempted_credits": _to_int(grab(r"修習學分數\s*：\s*(\d+)")),
        "earned_credits": _to_int(grab(r"學期實得學分[^：]*：\s*(\d+)")),
    }


def graded_cumulative_ranks(transcript: dict) -> list[tuple[str, dict]]:
    """累計排名，照學期排好：[(term, {"average", "class_rank", "dept_rank"}), ...]。

    iNCU 的累計排名表會多一列還沒有成績的這學期，數字跟上學期一模一樣，看起來像是
    這學期也算進去了，所以只留到最近一個有成績的學期為止。
    """
    cumulative = transcript.get("ranks", {}).get("cumulative", {})
    semester_ranks = transcript.get("ranks", {}).get("semester", {})
    graded = [
        sem["term"] for sem in transcript.get("semesters", [])
        if sem["summary"].get("average") is not None
        or semester_ranks.get(sem["term"], {}).get("average") is not None
    ]
    return sorted(
        ((term, rank) for term, rank in cumulative.items() if not graded or term <= max(graded)),
        key=lambda pair: pair[0],
    )


def _parse_rank_tables(soup: BeautifulSoup) -> dict[str, dict[str, dict]]:
    ranks: dict[str, dict[str, dict]] = {}
    for table in soup.find_all("table"):
        rows = [_cell_texts(tr) for tr in table.find_all("tr")]
        if not rows or rows[0][:2] != ["學年度", "學期平均"]:
            continue
        heading = table.find_previous(string=re.compile(r"(學期|累計|畢業)排名"))
        kind = "cumulative" if heading and "累計" in heading else "semester"
        for row in rows[1:]:
            if len(row) >= 4:
                ranks.setdefault(kind, {})[row[0]] = {
                    "average": _to_float(row[1]),
                    "class_rank": row[2],
                    "dept_rank": row[3],
                }
    return ranks


def parse_transcript_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = _parse_basic_info(soup)

    semesters = []
    for table in soup.find_all("table"):
        title = table.find("th")
        match = TERM_TITLE_RE.search(title.get_text(" ", strip=True)) if title else None
        if match:
            term = f"{match.group(1)}{match.group(2)}"
            label = f"{match.group(1)}-{match.group(2)}"
            semesters.append(_parse_semester_table(table, term, label))
    semesters.sort(key=lambda s: s["term"])

    return {
        "department": info.get("系所", ""),
        "grade": info.get("年級", ""),
        "program": info.get("學制", ""),
        "cumulative_credits": _to_int(info.get("累計學分")),
        "cumulative_average": _to_float(info.get("學業平均成績")),
        "semesters": semesters,
        "ranks": _parse_rank_tables(soup),
    }


# ============================================================
# 解析：畢業資格審查表
# ============================================================

def _parse_rule(code: str, node: dict) -> dict:
    raw_courses = node.get("course")
    courses = []
    if isinstance(raw_courses, list):
        for c in raw_courses:
            name = c.get("crs_name") or {}
            memo = c.get("memo") or {}
            score_text = str(c.get("score", ""))
            courses.append({
                "semester": str(c.get("semester", "")),
                "course_no": c.get("c_id", ""),
                "name": name.get("tw", "") if isinstance(name, dict) else str(name),
                "credits": _to_int(c.get("c_cred")),
                "score_text": score_text,
                "qualified": str(c.get("qualified")) == "1",
                # 這學期還在修、成績還沒出來的課：qualified 是 "2"、成績寫「未到(Not Entered)」（2026-10 實測）
                "in_progress": str(c.get("qualified")) == "2" or score_text.startswith("未到"),
                "memo": memo.get("tw", "") if isinstance(memo, dict) else str(memo),
            })

    return {
        "code": code,
        "name": node.get("ruleName", ""),
        "passed": node.get("isPass") == "T",
        "required_credits": _to_int(node.get("lowCredits")),
        "earned_credits": _to_int(node.get("creditCred")),
        "required_courses": _to_int(node.get("lowCourses")),
        "earned_courses": _to_int(node.get("creditCourse")),
        "memo": node.get("memo", ""),
        "courses": courses,
        "children": [
            _parse_rule(key, value)
            for key, value in node.items()
            if key.isdigit() and isinstance(value, dict)
        ],
    }


def parse_graduation_report_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    categories = []
    for block in soup.select("[data-credits]"):
        data = json.loads(block["data-credits"])
        rules = data.get("course") or {}
        if isinstance(rules, str):
            rules = json.loads(rules)
        category = _parse_rule("", {k: v for k, v in data.items() if k != "course"})
        category["children"] = [_parse_rule(k, v) for k, v in rules.items() if isinstance(v, dict)]
        category["percentage"] = _to_float(data.get("percentage"))
        categories.append(category)

    credit_spans = soup.select(".credit span")
    threshold = soup.select_one(".graduation-threshold")
    total_required = soup.select_one(".total-credits")
    department = soup.select_one(".department")

    return {
        "department": department.get_text(strip=True) if department else "",
        "total_required": _to_int(re.sub(r"\D", "", total_required.get_text())) if total_required else 0,
        "total_earned": _to_int(credit_spans[0].get_text()) if credit_spans else 0,
        "total_courses": _to_int(credit_spans[1].get_text()) if len(credit_spans) > 1 else 0,
        "passed_threshold": bool(threshold) and "未" not in threshold.get_text(),
        "categories": categories,
    }


# ============================================================
# 分析
# ============================================================

def _pass_score(program: str) -> float:
    return PASS_SCORE.get(program, DEFAULT_GRADUATE_PASS_SCORE)


def course_failed(course: dict) -> Optional[str]:
    """回傳未通過原因；通過、或還沒有成績（修課中）回傳 None。"""
    if course["score"] is not None:
        return "不及格" if course["score"] < _pass_score(course["program"]) else None
    for keyword in FAILED_SCORE_KEYWORDS:
        if keyword in course["score_text"]:
            return keyword
    return None


def _course_passed(course: dict) -> bool:
    if course["score"] is not None:
        return course["score"] >= _pass_score(course["program"])
    return "通過" in course["score_text"] and "未通過" not in course["score_text"]


def grade_point(score: float) -> tuple[str, float]:
    """百分制成績 → (等第, 等第積分)。成績以整數表示，先四捨五入。"""
    rounded = int(score + 0.5)
    return next((letter, point) for low, letter, point in GRADE_POINTS if rounded >= low)


def _counts_toward_average(course: dict, student_program: str) -> bool:
    """算累計學業平均跟 GPA 的課：有百分制成績的課，含不及格、重修、暑修，
    不含通過/不通過、停修（沒有分數）、境外修課、自主學習微課程，研究生不含學士班課程跟學位論文。"""
    if course["score"] is None or not course["credits"]:
        return False
    if "境外" in course.get("remark", "") or "自主學習" in course["name"]:
        return False
    if student_program and student_program != "學士班":
        return course["program"] != "學士班" and not THESIS_RE.match(course["name"])
    return True


AWAITING_SCORE_TEXTS = ("未到", "Not Entered")


def _awaiting_grade(course: dict, student_program: str) -> bool:
    """還沒有成績、出成績之後會算進累計平均跟 GPA 的課（這學期修課中的課）。
    成績欄是「未到」（iNCU 成績查詢的寫法）、「-」或空白才算，停修、通過、抵免這類不算。"""
    text = course["score_text"]
    if course["score"] is not None:
        return False
    if text.strip(" -—") and not any(mark in text for mark in AWAITING_SCORE_TEXTS):
        return False
    return _counts_toward_average({**course, "score": 0.0}, student_program)


def courses_awaiting_grades(transcript: dict) -> list[dict]:
    """成績單上還沒有成績、之後會算進 GPA 的課。通過制的課（例如服務學習）出成績前看不出來，也會算在裡面。"""
    program = transcript.get("program", "")
    return [
        {"term": sem["term"], "label": sem["label"], "name": course["name"], "credits": course["credits"]}
        for sem in transcript.get("semesters", [])
        for course in sem["courses"]
        if _awaiting_grade(course, program)
    ]


def compute_gpa(transcript: dict) -> Optional[dict]:
    """照學校公式算的 GPA（4.3 制）：(每科學分 × 等第積分) 的總和 ÷ 學分總和。

    iNCU 只給百分制的平均，GPA 是自己算的。同一批課也算一次累計平均，跟 iNCU 的累計平均比對，
    對得上才代表挑的課跟學校的算法一樣（matches_school_average），對不上前端會標「僅供參考」。
    points、scores 是 (學分 × 積分)、(學分 × 分數) 的總和，試算用。
    """
    program = transcript.get("program", "")
    total_credits = total_points = total_scores = 0.0
    semesters = []
    for sem in transcript.get("semesters", []):
        credits = points = 0.0
        for course in sem["courses"]:
            if not _counts_toward_average(course, program):
                continue
            credits += course["credits"]
            points += course["credits"] * grade_point(course["score"])[1]
            total_scores += course["credits"] * course["score"]
        if credits:
            semesters.append({"term": sem["term"], "gpa": round(points / credits + 1e-9, 2)})
            total_credits += credits
            total_points += points
    if not total_credits:
        return None
    average = round(total_scores / total_credits + 1e-9, 2)
    reported = transcript.get("cumulative_average")
    return {
        "gpa": round(total_points / total_credits + 1e-9, 2),
        "credits": int(total_credits),
        "semesters": semesters,
        "matches_school_average": reported is None or abs(average - reported) <= 0.01,
        "points": total_points,
        "scores": total_scores,
    }


# ============================================================
# GPA 試算：剩下的課都拿某個成績會變多少、要考幾分才到目標
# ============================================================
MAX_POINT = GRADE_POINTS[0][2]
# 試算表列的等第（再低的很少人會想試算，使用者問了會另外算）
SCENARIO_GRADES = ("A+", "A", "A-", "B+", "B", "B-")
_LETTER_RANGES = {
    letter: (low, GRADE_POINTS[i - 1][0] - 1 if i else 100, point)
    for i, (low, letter, point) in enumerate(GRADE_POINTS)
}
_ASSUMED_LETTER_RE = re.compile(r"^([A-Ea-eXx])\s*([+＋\-－]?)$")
_ASSUMED_SCORE_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*分?$")


def parse_assumed_grade(text: str) -> Optional[dict]:
    """「A」「a+」「B-」「85」「85 分」→ {"grade", "point", "score"}。等第對應的是一段分數，score 是 None。"""
    text = re.sub(r"^(?:(?:都|全部|全|每科|拿|考)\s*)+", "", (text or "").strip())
    letter = _ASSUMED_LETTER_RE.match(text)
    if letter:
        sign = {"＋": "+", "－": "-"}.get(letter.group(2), letter.group(2))
        grade = letter.group(1).upper() + sign
        if grade in _LETTER_RANGES:
            return {"grade": grade, "point": _LETTER_RANGES[grade][2], "score": None}
        return None
    score = _ASSUMED_SCORE_RE.match(text)
    if score and float(score.group(1)) <= 100:
        value = float(score.group(1))
        grade, point = grade_point(value)
        return {"grade": grade, "point": point, "score": value}
    return None


def required_score(current: float, credits: float, remaining: float, target: float) -> Optional[float]:
    """累計 current（credits 學分）要變成 target，剩下 remaining 學分要平均多少。GPA、百分制都適用。"""
    if remaining <= 0:
        return None
    return (target * (credits + remaining) - current * credits) / remaining


def round_up(value: float) -> float:
    """要考幾分這種門檻無條件進位到小數第二位，四捨五入可能差一點點沒達到。"""
    return math.ceil(value * 100 - 1e-6) / 100


def _letter_label(grade: str) -> str:
    low, high, _ = _LETTER_RANGES[grade]
    return f"{grade}（{low} 分以上）" if grade == "A+" else f"{grade}（{low}～{high} 分）"


def _lowest_letter(point: float) -> Optional[str]:
    """平均積分要 point 以上時，每科都拿同一個等第的話最低要哪一個（不及格的 D、E 不算）。"""
    passing = [(low, letter, p) for low, letter, p in GRADE_POINTS if low >= 60]
    return next((letter for _, letter, p in reversed(passing) if p >= point - 1e-9), None)


def _extra_credits(current: float, credits: float, remaining: float, target: float, best: float) -> Optional[int]:
    """剩下的課全拿最高分也到不了 target 時，之後再修幾學分（也全拿最高分）才到得了。"""
    if target >= best:
        return None
    total = credits + remaining
    return math.ceil((target * total - current * credits - best * remaining) / (best - target) - 1e-9)


def _scope_text(source: str, credits: int) -> str:
    if source == "until_graduation":
        return f"畢業前剩下 {credits} 學分"
    if source == "user":
        return f"接下來 {credits} 學分"
    return f"這學期 {credits} 學分"


def _assumed_answer(assumed: dict, scope: str, gpa_before: float, gpa_after: float,
                    average_before: Optional[float], average_after: Optional[float]) -> str:
    if assumed["score"] is not None:
        text = f"{scope}都考 {assumed['score']:g} 分（{assumed['grade']}，{assumed['point']:.1f}）的話，累計 GPA 會從 {gpa_before:.2f} 變成 {gpa_after:.2f}"
        if average_before is not None and average_after is not None:
            text += f"，累計平均從 {average_before:.2f} 變成 {average_after:.2f}"
        return text + "。"
    return f"{scope}都拿 {_letter_label(assumed['grade'])}的話，累計 GPA 會從 {gpa_before:.2f} 變成 {gpa_after:.2f}。"


def _target_gpa_answer(target: float, scope: str, current: float, credits: float, remaining: float) -> str:
    if target > MAX_POINT:
        return f"GPA 最高是 {MAX_POINT}（每科都 A+），沒辦法到 {target:.2f}。"
    need = required_score(current, credits, remaining, target)
    if need is None:
        return f"成績單上看不到還沒有成績的課。跟我說還有幾學分要修（例如「還有 30 學分，GPA 要拿多少才到 {target:.2f}」），我再幫你算。"
    if need <= 0:
        return f"目前累計 GPA {current:.2f}，{scope}不管拿幾分，累計 GPA 都會在 {target:.2f} 以上。"
    if need > MAX_POINT + 1e-9:
        best = (current * credits + MAX_POINT * remaining) / (credits + remaining)
        text = f"{scope}全拿 A+（{MAX_POINT}）累計 GPA 也只到 {best:.2f}，到不了 {target:.2f}。"
        extra = _extra_credits(current, credits, remaining, target, MAX_POINT)
        if extra is None:
            return text + f"累計 GPA 要到 {target:.2f}，之前的每一科都要是 A+。"
        return text + f"之後的課也全拿 A+ 的話，還要再修 {extra} 學分才到得了。"
    letter = _lowest_letter(need)
    lead = (
        f"目前累計 GPA {current:.2f}，已經在 {target:.2f} 以上，要維持的話{scope}的 GPA 要 {round_up(need):.2f} 以上"
        if current >= target else f"累計 GPA 要到 {target:.2f}，{scope}的 GPA 要 {round_up(need):.2f} 以上"
    )
    if letter is None:
        return lead + "。"
    if letter == GRADE_POINTS[0][1]:
        return lead + f"，要每科都拿 {_letter_label(letter)}才到得了。"
    return lead + f"，大約每科都拿 {_letter_label(letter)}以上就夠。"


def _target_average_answer(target: float, scope: str, current: Optional[float], credits: float, remaining: float) -> str:
    if current is None:
        return "查不到你的累計平均，沒辦法試算。"
    if target > 100:
        return f"平均最高是 100 分，沒辦法到 {target:g} 分。"
    need = required_score(current, credits, remaining, target)
    if need is None:
        return f"成績單上看不到還沒有成績的課。跟我說還有幾學分要修（例如「還有 30 學分，平均要考幾分累計才到 {target:g}」），我再幫你算。"
    if need <= 0:
        return f"目前累計平均 {current:.2f}，{scope}不管考幾分，累計平均都會在 {target:g} 以上。"
    if need > 100 + 1e-9:
        best = (current * credits + 100 * remaining) / (credits + remaining)
        text = f"{scope}全考 100 分累計平均也只到 {best:.2f}，到不了 {target:g}。"
        extra = _extra_credits(current, credits, remaining, target, 100)
        if extra is None:
            return text
        return text + f"之後的課也全考 100 分的話，還要再修 {extra} 學分才到得了。"
    if current >= target:
        return f"目前累計平均 {current:.2f}，已經在 {target:g} 以上，要維持的話{scope}平均要考 {round_up(need):.2f} 分以上。"
    return f"累計平均要到 {target:g}，{scope}平均要考 {round_up(need):.2f} 分以上。"


ESTIMATE_SCOPES = ("this_semester", "until_graduation")


def estimate_gpa(
    transcript: dict,
    graduation: Optional[dict] = None,
    target_gpa: float = 0,
    target_average: float = 0,
    assumed_grade: str = "",
    remaining: str = "this_semester",
    credits: int = 0,
) -> Optional[dict]:
    """GPA 試算（純計算，不呼叫模型）。剩下的學分預設是這學期還沒有成績的課，remaining="until_graduation"
    改用畢業資格審查表上還差的學分，credits 是使用者自己說的學分數（優先）。

    answers 是直接回答使用者問題的句子（假設拿某個成績、要到某個目標），scenarios 是剩下的課都拿
    A+、A、A-…的累計 GPA。查不到任何有成績的課時回傳 None。
    """
    gpa = compute_gpa(transcript)
    if gpa is None:
        return None
    awaiting = courses_awaiting_grades(transcript)
    notes = []
    if credits and credits > 0:
        source, remaining_credits = "user", int(credits)
    elif remaining == "until_graduation" and graduation and graduation.get("total_required"):
        source = "until_graduation"
        remaining_credits = max(0, graduation["total_required"] - graduation["total_earned"])
        notes.append("到畢業還差的學分裡，通過制、不算 GPA 的課也算在內，實際算進 GPA 的學分可能少一點。")
    else:
        source, remaining_credits = "this_semester", sum(c["credits"] for c in awaiting)
        if remaining == "until_graduation":
            notes.append("畢業資格審查表暫時取不到，先用這學期還沒有成績的課試算。")

    total_credits, points = gpa["credits"], gpa["points"]
    current_gpa = points / total_credits
    reported = transcript.get("cumulative_average")
    current_average = reported if reported is not None else gpa["scores"] / total_credits
    scope = _scope_text(source, remaining_credits)

    def after(point: float) -> float:
        return round((points + point * remaining_credits) / (total_credits + remaining_credits) + 1e-9, 2)

    scenarios = [
        {"grade": grade, "label": _letter_label(grade), "point": _LETTER_RANGES[grade][2],
         "gpa": after(_LETTER_RANGES[grade][2])}
        for grade in SCENARIO_GRADES
    ] if remaining_credits else []

    answers = []
    assumed = parse_assumed_grade(assumed_grade) if assumed_grade else None
    if assumed_grade and assumed is None:
        answers.append(f"看不懂「{assumed_grade}」是哪個成績，可以寫等第（例如 A、B+）或分數（例如 85 分）。")
    elif assumed and remaining_credits:
        average_after = None
        if assumed["score"] is not None:
            average_after = (current_average * total_credits + assumed["score"] * remaining_credits) / (total_credits + remaining_credits)
        answers.append(_assumed_answer(assumed, scope, gpa["gpa"], after(assumed["point"]), current_average, average_after))
    elif assumed:
        answers.append("成績單上看不到還沒有成績的課。跟我說還有幾學分要修（例如「還有 30 學分都拿 A 的話 GPA 會是多少」），我再幫你算。")
    if target_gpa:
        answers.append(_target_gpa_answer(target_gpa, scope, current_gpa, total_credits, remaining_credits))
    if target_average:
        answers.append(_target_average_answer(target_average, scope, current_average, total_credits, remaining_credits))
    if not gpa["matches_school_average"]:
        notes.append("用來算 GPA 的課跟學校的累計平均對不上，試算結果僅供參考。")

    return {
        "current_gpa": gpa["gpa"],
        "current_average": current_average,
        "credits": total_credits,
        "remaining_source": source,
        "remaining_credits": remaining_credits,
        "remaining_courses": awaiting if source == "this_semester" else [],
        "scope_text": scope,
        "scenarios": scenarios,
        "answers": answers,
        "notes": notes,
    }


def _remaining(rule: dict) -> tuple[int, int]:
    return (
        max(0, rule["required_credits"] - rule["earned_credits"]),
        max(0, rule["required_courses"] - rule["earned_courses"]),
    )


def _unmet_rules(rule: dict) -> list[dict]:
    """往下找到「真正沒過」的最細規則，上層只是因為底下沒過才沒過的不重複列。

    學校的上層要求不一定等於細項加總（例如共同必修要 10 門，細項加起來只有 9 門）：
    上層缺的比細項缺的多時，另外列一行「整體」，不能讓它無聲消失。
    """
    return [item for item, _ in _unmet_with_gaps(rule)]


def _unmet_with_gaps(rule: dict) -> list[tuple[dict, tuple[int, int]]]:
    """每一行附上它算進上層缺口的 (學分, 門數)。

    整體那行的差額只拿來判斷要不要列、給更上層加總（更上層才不會重複計算），
    顯示的是整體還差多少：細項缺的門數是下限，核心通識差 5 學分、1 門，
    實際修 2 門就把整體的 10 門也補滿了，寫成「細項達標後還要再多修 1 門」會讓人以為要多修。
    同一套拆法 graduation_check._with_whole_gap 也在用。
    """
    if rule["passed"]:
        return []
    remaining = _remaining(rule)
    failing_children = [c for c in rule["children"] if not c["passed"]]
    if not failing_children:
        return [(_unmet_item(rule["name"], remaining, _clean_memo(rule["memo"])), remaining)]

    rows = [row for child in failing_children for row in _unmet_with_gaps(child)]
    extra_credits = remaining[0] - sum(gap[0] for _, gap in rows)
    extra_courses = remaining[1] - sum(gap[1] for _, gap in rows)
    if extra_credits > 0 or extra_courses > 0:
        memo = (
            "補細項修的課也算在內，修「"
            + "、".join(c["name"] for c in rule["children"])
            + "」任一類的課都算"
        )
        rows.append((
            _unmet_item(f"{rule['name']}整體", remaining, memo),
            (max(0, extra_credits), max(0, extra_courses)),
        ))
    return rows


def _unmet_item(name: str, remaining: tuple[int, int], memo: str) -> dict:
    return {
        "name": name,
        "remaining_credits": remaining[0],
        "remaining_courses": remaining[1],
        "memo": memo,
    }


def _clean_memo(memo: str) -> str:
    """拿掉只有類別代碼、對使用者沒意義的片段，例如「※未通過類別:18101,」。"""
    parts = [p.strip(" .,") for p in memo.split("※")]
    keep = [p for p in parts if p and not p.startswith("未通過類別") and not p.startswith("學分數或課程數不足")]
    return "。".join(keep)  # 不用分號（使用者不喜歡）


def _build_alerts(transcript: dict, graduation: Optional[dict]) -> list[dict]:
    passed_later: dict[str, str] = {}
    for sem in transcript["semesters"]:
        for course in sem["courses"]:
            if _course_passed(course):
                passed_later[course["course_no"]] = max(passed_later.get(course["course_no"], ""), sem["term"])

    alerts = []
    seen = set()
    for sem in transcript["semesters"]:
        for course in sem["courses"]:
            reason = course_failed(course)
            if reason is None:
                continue
            seen.add((sem["term"], course["course_no"]))
            if passed_later.get(course["course_no"], "") > sem["term"]:
                continue
            alerts.append({
                "term": sem["term"],
                "label": sem["label"],
                "course_no": course["course_no"],
                "name": course["name"],
                "credits": course["credits"],
                "course_type": course["course_type"],
                "score_text": course["score_text"],
                "reason": reason,
                "required": course["course_type"] == "必修",
            })

    # 畢業審查表判定未通過、但成績頁沒抓到的（例如抵免/跨系課程）。這學期還在修的課審查表也判未通過，
    # 不算（以前會被列成「必修課需要重新修習才能畢業」）
    if graduation:
        stack = list(graduation["categories"])
        while stack:
            rule = stack.pop()
            stack.extend(rule["children"])
            for c in rule["courses"]:
                key = (c["semester"], c["course_no"])
                if (c["qualified"] or c.get("in_progress") or key in seen
                        or passed_later.get(c["course_no"], "") > c["semester"]):
                    continue
                seen.add(key)
                alerts.append({
                    "term": c["semester"],
                    "label": f"{c['semester'][:-1]}-{c['semester'][-1:]}",
                    "course_no": c["course_no"],
                    "name": c["name"],
                    "credits": c["credits"],
                    "course_type": rule["name"],
                    "score_text": c["score_text"],
                    "reason": "畢業審查未通過",
                    "required": "必修" in rule["name"],
                })

    alerts.sort(key=lambda a: (not a["required"], a["term"]))
    return alerts


ANALYSIS_FOCUSES = ("credits", "grades", "overview", "estimate")


def analyze_academic_progress(
    transcript: dict, graduation: Optional[dict], focus: str = "overview", estimate: Optional[dict] = None,
) -> dict:
    """整理成前端 AcademicAnalysis 元件吃的資料包。

    focus 決定前端顯示哪一塊：credits（學分/畢業缺口）、grades（成績/排名）、overview（全部）、
    estimate（GPA 試算）。estimate 是 estimate_gpa 的參數（目標 GPA、假設的成績…），沒給就用預設的
    「這學期還沒有成績的課」試算，成績的區塊會附一行全拿 A、全拿 B 的累計 GPA。
    """
    credits_by_type: dict[str, int] = {}
    for sem in transcript["semesters"]:
        for course in sem["courses"]:
            if _course_passed(course) and course["credits"]:
                credits_by_type[course["course_type"]] = credits_by_type.get(course["course_type"], 0) + course["credits"]

    semester_ranks = transcript["ranks"].get("semester", {})
    gpa = compute_gpa(transcript)
    semester_gpa = {s["term"]: s["gpa"] for s in gpa["semesters"]} if gpa else {}
    semesters = []
    for sem in transcript["semesters"]:
        rank = semester_ranks.get(sem["term"], {})
        semesters.append({
            "term": sem["term"],
            "label": sem["label"],
            "gpa": semester_gpa.get(sem["term"]),
            "average": sem["summary"].get("average", rank.get("average")),
            "earned_credits": sem["summary"].get("earned_credits"),
            "class_rank": rank.get("class_rank"),
            "dept_rank": rank.get("dept_rank"),
        })

    averages = [s["average"] for s in semesters if s["average"] is not None]
    trend = round(averages[-1] - averages[-2], 2) if len(averages) >= 2 else None

    cumulative_ranks = [
        {"term": term, "label": f"{term[:-1]}-{term[-1:]}", **rank}
        for term, rank in graded_cumulative_ranks(transcript)
    ]

    categories = []
    if graduation:
        for cat in graduation["categories"]:
            categories.append({
                "name": cat["name"],
                "passed": cat["passed"],
                "required_credits": cat["required_credits"],
                "earned_credits": cat["earned_credits"],
                "required_courses": cat["required_courses"],
                "earned_courses": cat["earned_courses"],
                "percentage": 100.0 if cat["passed"] else (cat["percentage"] or 0.0),
                "unmet_rules": _unmet_rules(cat),
            })

    earned = graduation["total_earned"] if graduation else transcript["cumulative_credits"]
    required = graduation["total_required"] if graduation else None

    return {
        "kind": "academic_analysis",
        "focus": focus if focus in ANALYSIS_FOCUSES else "overview",
        "department": transcript["department"],
        "grade": transcript["grade"],
        "credits": {
            "earned": earned,
            "required": required,
            "remaining": max(0, required - earned) if required else None,
            "passed_threshold": graduation["passed_threshold"] if graduation else None,
            "by_course_type": credits_by_type,
        },
        "gpa": {
            "cumulative_average": transcript["cumulative_average"],
            "gpa": gpa["gpa"] if gpa else None,
            "gpa_credits": gpa["credits"] if gpa else None,
            "gpa_matches_school_average": gpa["matches_school_average"] if gpa else None,
            "latest_change": trend,
            "semesters": semesters,
            "cumulative_ranks": cumulative_ranks,
        },
        "graduation_available": graduation is not None,
        "graduation_categories": categories,
        "alerts": _build_alerts(transcript, graduation),
        "estimate": estimate_gpa(transcript, graduation, **(estimate or {})),
    }
