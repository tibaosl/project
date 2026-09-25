"""學業分析：抓 iNCU「學生成績查詢」跟「畢業資格審查表」，整理成已修學分、
畢業學分缺口、學期平均趨勢、不及格/停修提醒。

抓取（需要登入的 NCUSession）跟解析/分析（純函式，吃 HTML 字串）分開，
解析跟分析的部分不用登入就能測試，見 test_academic_tools.py。
"""

import json
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from logging_config import make_print_logger

print = make_print_logger(__name__)

INCU_TRANSCRIPT_URL = "https://cis.ncu.edu.tw/iNCU/academic/score/transcriptQuery"
INCU_GRADUATE_REPORT_URL = "https://cis.ncu.edu.tw/iNCU/academic/graduate/graduateReport"

# 學士班 60 分及格，研究所 70 分
PASS_SCORE = {"學士班": 60.0}
DEFAULT_GRADUATE_PASS_SCORE = 70.0

TERM_TITLE_RE = re.compile(r"第\s*(\d+)\s*學年度第\s*(\d)\s*學期")
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

    print("[iNCU] 正在取得畢業資格審查表...")
    try:
        graduation = parse_graduation_report_html(
            await _load_incu_page(session, INCU_GRADUATE_REPORT_URL, ".total-credits")
        )
    except Exception as exc:
        print(f"[iNCU] 畢業資格審查表取得失敗，只分析成績：{exc}")
        graduation = None

    return transcript, graduation


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
            courses.append({
                "semester": str(c.get("semester", "")),
                "course_no": c.get("c_id", ""),
                "name": name.get("tw", "") if isinstance(name, dict) else str(name),
                "credits": _to_int(c.get("c_cred")),
                "score_text": str(c.get("score", "")),
                "qualified": str(c.get("qualified")) == "1",
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


def _course_failed(course: dict) -> Optional[str]:
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


def _remaining(rule: dict) -> tuple[int, int]:
    return (
        max(0, rule["required_credits"] - rule["earned_credits"]),
        max(0, rule["required_courses"] - rule["earned_courses"]),
    )


def _unmet_rules(rule: dict) -> list[dict]:
    """往下找到「真正沒過」的最細規則，上層只是因為底下沒過才沒過的不重複列。

    學校的上層要求不一定等於細項加總（例如共同必修要 10 門，細項加起來只有 9 門）：
    細項是各自的下限，細項都達標後，上層還要再多修的部分算在任一細項都可以。
    上層缺的比細項缺的多時，差額另外列一行，不能讓它無聲消失。
    """
    if rule["passed"]:
        return []
    remaining_credits, remaining_courses = _remaining(rule)
    failing_children = [c for c in rule["children"] if not c["passed"]]
    if not failing_children:
        return [{
            "name": rule["name"],
            "remaining_credits": remaining_credits,
            "remaining_courses": remaining_courses,
            "memo": _clean_memo(rule["memo"]),
        }]

    items = [item for child in failing_children for item in _unmet_rules(child)]
    extra_credits = remaining_credits - sum(i["remaining_credits"] for i in items)
    extra_courses = remaining_courses - sum(i["remaining_courses"] for i in items)
    if extra_credits > 0 or extra_courses > 0:
        items.append({
            "name": f"{rule['name']}整體",
            "remaining_credits": max(0, extra_credits),
            "remaining_courses": max(0, extra_courses),
            "memo": "細項都達標後整體還要再多修的部分，修「"
            + "、".join(c["name"] for c in rule["children"])
            + "」任一類的課都算",
        })
    return items


def _clean_memo(memo: str) -> str:
    """拿掉只有類別代碼、對使用者沒意義的片段，例如「※未通過類別:18101,」。"""
    parts = [p.strip(" .,") for p in memo.split("※")]
    keep = [p for p in parts if p and not p.startswith("未通過類別") and not p.startswith("學分數或課程數不足")]
    return "；".join(keep)


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
            reason = _course_failed(course)
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

    # 畢業審查表判定未通過、但成績頁沒抓到的（例如抵免/跨系課程）
    if graduation:
        stack = list(graduation["categories"])
        while stack:
            rule = stack.pop()
            stack.extend(rule["children"])
            for c in rule["courses"]:
                key = (c["semester"], c["course_no"])
                if c["qualified"] or key in seen or passed_later.get(c["course_no"], "") > c["semester"]:
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


ANALYSIS_FOCUSES = ("credits", "grades", "overview")


def analyze_academic_progress(
    transcript: dict, graduation: Optional[dict], focus: str = "overview"
) -> dict:
    """整理成前端 AcademicAnalysis 元件吃的資料包。

    focus 決定前端顯示哪一塊：credits（學分/畢業缺口）、grades（成績/排名）、overview（全部）。
    """
    credits_by_type: dict[str, int] = {}
    for sem in transcript["semesters"]:
        for course in sem["courses"]:
            if _course_passed(course) and course["credits"]:
                credits_by_type[course["course_type"]] = credits_by_type.get(course["course_type"], 0) + course["credits"]

    semester_ranks = transcript["ranks"].get("semester", {})
    semesters = []
    for sem in transcript["semesters"]:
        rank = semester_ranks.get(sem["term"], {})
        semesters.append({
            "term": sem["term"],
            "label": sem["label"],
            "average": sem["summary"].get("average", rank.get("average")),
            "earned_credits": sem["summary"].get("earned_credits"),
            "class_rank": rank.get("class_rank"),
            "dept_rank": rank.get("dept_rank"),
        })

    averages = [s["average"] for s in semesters if s["average"] is not None]
    trend = round(averages[-1] - averages[-2], 2) if len(averages) >= 2 else None

    cumulative_ranks = [
        {"term": term, "label": f"{term[:-1]}-{term[-1:]}", **rank}
        for term, rank in sorted(transcript["ranks"].get("cumulative", {}).items())
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
            "latest_change": trend,
            "semesters": semesters,
            "cumulative_ranks": cumulative_ranks,
        },
        "graduation_available": graduation is not None,
        "graduation_categories": categories,
        "alerts": _build_alerts(transcript, graduation),
    }
