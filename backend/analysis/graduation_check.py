"""畢業總檢：把 iNCU 畢業資格審查表跟學習護照時數整理成一張「離畢業還差什麼」的清單。

審查表除了學分，還有操行（每學期一筆）、體育、服務學習課程、學生學習護照、特殊檢核（例如院選修學分、
英文能力）這些條件（2026-10 用資工系大三的帳號看過）。每一項標出完成了沒、還差多少、怎麼補。這學期
修課中（成績還沒出來）的課另外算，標出都通過的話會不會完成。

都是純函式，不呼叫模型、不用登入，測試見 tests/test_graduation_check.py。審查表沒寫細節的條件
（英文能力的門檻、院選修哪些課算、還沒修的必修是哪幾門）只附上可以點的問題，使用者點了才用法規問答查
（一題約 US$0.09），這裡不查。
"""

import re
from typing import Optional

from backend.analysis.academic_tools import _clean_memo, _remaining

DONE = "done"            # 已完成
THIS_TERM = "this_term"  # 這學期修課中的課都通過就會完成
PENDING = "pending"      # 學習護照時數等主辦單位核發就會達標
AUTO = "auto"            # 操行：照常在學，每學期會多一筆
TODO = "todo"            # 還要另外補
STATUS_ORDER = (TODO, THIS_TERM, PENDING, AUTO, DONE)

SUMMARY_TEXT = {
    TODO: "{} 項還要補",
    THIS_TERM: "{} 項這學期的課都通過就會完成",
    PENDING: "{} 項等時數核發",
    AUTO: "{} 項照常在學就會完成",
}

# 例如軍訓：「※本階層規則之實得學分數不納入畢業總學分.」
TOTAL_EXCLUDED_MEMO = "不納入畢業總學分"
# 審查表的系所寫成「113-資訊工程學系」：課程規劃的入學年度-系所
DEPARTMENT_RE = re.compile(r"^(\d{2,3})\s*-\s*(.+)$")
RULE_PREFIX_RE = re.compile(r"^特殊檢核(條件)?-")
LANGUAGE_RE = re.compile(r"英文|外語|外文|語言")
THRESHOLD_RE = re.compile(r"能力|檢定|鑑定|門檻|檢核")
HOURS_TOTAL_RE = re.compile(r"總時數[:：]\s*([\d.]+)")
MAX_COURSE_NAMES = 4


# ============================================================
# 小工具
# ============================================================

def _ask(label: str, question: str) -> dict:
    return {"label": label, "question": question}


def _subject(department: str) -> tuple[str, str]:
    """「113-資訊工程學系」→ ("資訊工程學系", "113")，沒有年度的寫法年度留空。"""
    match = DEPARTMENT_RE.match(department.strip())
    return (match.group(2).strip(), match.group(1)) if match else (department.strip(), "")


def _question(department: str, cohort: str, body: str) -> str:
    """法規問答要知道系所跟入學年度才挑得到對的文件（各入學年度的規定可能不一樣）。"""
    if department and cohort:
        return f"{department} {cohort} 學年度入學，{body}"
    if department:
        return f"{department}的{body}"
    return body


def _term_label(semester: str) -> str:
    return f"{semester[:-1]}-{semester[-1:]}" if len(semester) >= 4 else semester


def _amount(credits: int, courses: int, unit: str = "門") -> str:
    parts = []
    if credits:
        parts.append(f"{credits} 學分")
    if courses:
        parts.append(f"{courses} {unit}")
    return "、".join(parts)


def _hours(value: float) -> str:
    return f"{value:g}"


def _score_text(text: str) -> str:
    """「停修(Withdrawn)」→「停修」，「45.00」→「45 分」。"""
    text = re.sub(r"\(.*?\)", "", text).strip()
    try:
        return f"{float(text):g} 分"
    except ValueError:
        return text


def _course_names(courses: list[dict]) -> str:
    names = [c["name"] for c in courses]
    if len(names) > MAX_COURSE_NAMES:
        return "、".join(names[:MAX_COURSE_NAMES]) + f"等 {len(names)} 門"
    return "、".join(names)


def _short_name(name: str, parent: str) -> str:
    """細項名稱拿掉跟上層重複的部分：「一般系訂必修學分-A類」在「一般系訂必修學分」底下寫「A類」，
    「核心通識-人文與思想」寫「人文與思想」。"""
    if name.startswith(parent) and name != parent:
        return name[len(parent):].lstrip("-－ ") or name
    return name.split("-", 1)[1] if "-" in name else name


def _has_requirement(rule: dict) -> bool:
    return bool(rule["required_credits"] or rule["required_courses"])


def _note(rule: dict) -> str:
    """完成的項目旁邊的說明，例如英文能力「通過培力英檢考試」。"""
    return re.sub(r"[\[\]]", "", _clean_memo(rule["memo"]))


# ============================================================
# 修課中、沒通過的課
# ============================================================

def _walk_courses(rule: dict):
    yield from rule["courses"]
    for child in rule["children"]:
        yield from _walk_courses(child)


def _course_key(course: dict) -> tuple[str, str]:
    return course["semester"], course["course_no"]


def _taking(rule: dict, exclude: frozenset = frozenset()) -> list[dict]:
    """這個規則（含底下細項）這學期修課中的課，同一門只算一次。"""
    found: dict[tuple, dict] = {}
    for course in _walk_courses(rule):
        if course.get("in_progress") and _course_key(course) not in exclude:
            found.setdefault(_course_key(course), course)
    return list(found.values())


def _failed_courses(rule: dict) -> list[dict]:
    """沒通過（停修、不及格）、之後也還沒重修通過的課。同一門課重修是同一個課號，
    不用課名比對（上下學期的微積分課名一樣、課號不同）。"""
    courses = list(_walk_courses(rule))
    passed = {c["course_no"] for c in courses if c["qualified"]}
    retaking = {c["course_no"] for c in courses if c.get("in_progress")}
    failed, seen = [], set()
    for course in courses:
        if course["qualified"] or course.get("in_progress") or course["course_no"] in passed | seen:
            continue
        seen.add(course["course_no"])
        failed.append({**course, "retaking": course["course_no"] in retaking})
    return failed


# ============================================================
# 還差多少
# ============================================================

def _gaps(rule: dict) -> list[dict]:
    """沒過的最細規則各還差多少，跟 academic_tools._unmet_rules 同一套拆法，另外記這學期修課中的課。"""
    if rule["passed"]:
        return []
    failing = [child for child in rule["children"] if not child["passed"]]
    if not failing:
        credits, courses = _remaining(rule)
        return [{"rule": rule, "credits": credits, "courses": courses, "taking": _taking(rule)}]
    return _with_whole_gap(rule, [gap for child in failing for gap in _gaps(child)])


def _with_whole_gap(rule: dict, gaps: list[dict]) -> list[dict]:
    """學校的上層要求不一定等於細項加總（共同必修要 10 門，細項加起來只有 9 門），
    上層缺的比細項缺的多時，另外列一筆「整體」。

    差額（credits、courses）只拿來判斷要不要列、給更上層加總，顯示的是整體還差多少（full）：
    細項缺的門數是下限，核心通識差 5 學分、1 門，實際修 2 門就把整體的 10 門也補滿了，
    寫成「細項達到之後還要再多修 1 門」會讓人以為要多修。"""
    credits, courses = _remaining(rule)
    extra_credits = credits - sum(g["credits"] for g in gaps)
    extra_courses = courses - sum(g["courses"] for g in gaps)
    if extra_credits <= 0 and extra_courses <= 0:
        return gaps
    return gaps + [{
        "rule": rule, "whole": True,
        "credits": max(0, extra_credits), "courses": max(0, extra_courses),
        "full": (credits, courses), "taking": _taking(rule),
    }]


def _needed(gap: dict) -> tuple[int, int]:
    return gap.get("full", (gap["credits"], gap["courses"]))


def _after(gap: dict) -> tuple[int, int]:
    """這學期修課中的課都通過的話，還差 (學分, 門數)。"""
    credits, courses = _needed(gap)
    return (
        max(0, credits - sum(c["credits"] for c in gap["taking"])),
        max(0, courses - len(gap["taking"])),
    )


def _taking_line(gap: dict, unit: str) -> str:
    taking = gap["taking"]
    credits = sum(c["credits"] for c in taking)
    after = _amount(*_after(gap), unit)
    return (
        f"這學期修課中：{_course_names(taking)}" + (f"（{credits} 學分）" if credits else "")
        + ("，都通過就夠了" if not after else f"，都通過的話還差 {after}")
    )


def _whole_line(rule: dict, gap: dict, unit: str) -> str:
    areas = "、".join(child["name"] for child in rule["children"])
    line = f"整體還差 {_amount(*_needed(gap), unit)}"
    return line + (f"，補細項修的課也算在內，{areas}任一類的課都算" if areas else "")


def _progress(rule: dict, unit: str) -> tuple[str, Optional[float]]:
    parts, ratios = [], []
    for earned, required, label in (
        (rule["earned_credits"], rule["required_credits"], "學分"),
        (rule["earned_courses"], rule["required_courses"], unit),
    ):
        if required:
            parts.append(f"{earned} / {required} {label}")
            ratios.append(min(1.0, earned / required))
    return "・".join(parts), (min(ratios) if ratios else None)


# ============================================================
# 每一項
# ============================================================

def _item(group: str, title: str, status: str, **fields) -> dict:
    return {
        "group": group, "title": title, "status": status,
        "progress": "", "ratio": None, "lines": [], "how": "", "asks": [], "note": "",
        **fields,
    }


def _advice(rule: dict, title: str, department: str, cohort: str) -> tuple[str, list[dict]]:
    """怎麼補。審查表沒寫細節的條件附上可以點的問題（點了才查法規）。"""
    name = rule["name"]
    if "操行" in name:
        return "操行成績每學期評一次，照常在學，之後每學期會多一筆。", []
    if "體育" in name:
        return "之後的學期再選修體育課補齊門數。", []
    if "服務學習" in name:
        return "選修服務學習課程補齊門數。", []
    if LANGUAGE_RE.search(name) and THRESHOLD_RE.search(name):
        return (
            "審查表只寫有沒有通過，門檻跟認可的檢定要看系上的規定。",
            [_ask("問英文畢業門檻", _question(department, cohort, "英文畢業門檻是什麼？"))],
        )
    if "必修" in name:
        return (
            "審查表只列出修過的課，還沒修的必修要對照系上的必修科目表。",
            [_ask("問必修科目有哪些", _question(department, cohort, "必修科目有哪些？"))],
        )
    if "通識" in name:
        areas = [_short_name(child["name"], name) for child in rule["children"]]
        if areas and not any(_has_requirement(child) for child in rule["children"]):
            return f"選修通識課程補齊，{'、'.join(areas)}任一類的課都算。", []
        return "選修通識課程，補齊上面還缺的部分。", []
    return (
        "審查表沒寫哪些課算，可以問我系上的規定。",
        [_ask(f"問{title}的規定", _question(department, cohort, f"「{title}」有什麼規定？"))],
    )


def _rule_item(rule: dict, group: str, department: str, cohort: str) -> dict:
    title = RULE_PREFIX_RE.sub("", rule["name"])
    if rule["passed"]:
        return _item(group, title, DONE, note=_note(rule))

    unit = "學期" if "操行" in rule["name"] else "門"
    progress, ratio = _progress(rule, unit)
    gaps = _gaps(rule)
    single = len(gaps) == 1 and (gaps[0]["credits"], gaps[0]["courses"]) == _remaining(rule)
    lines = []
    for gap in gaps:
        if gap.get("whole"):
            lines.append(_whole_line(gap["rule"], gap, unit))
        else:
            label = "" if single else f"{_short_name(gap['rule']['name'], rule['name'])}："
            amount = _amount(gap["credits"], gap["courses"], unit)
            memo = _clean_memo(gap["rule"]["memo"])
            lines.append(f"{label}還差 {amount}" if amount else f"{label}{memo or '條件還沒達成'}")
        if gap["taking"]:
            lines.append(_taking_line(gap, unit))

    failed = _failed_courses(rule)
    # 選修被當不用重修（修別的課補學分就好），只列必修
    must_retake = failed if "必修" in rule["name"] else []
    for course in must_retake:
        lines.append(
            f"要重修：{course['name']}（{_term_label(course['semester'])} {_score_text(course['score_text'])}"
            + ("，這學期重修中）" if course["retaking"] else "）")
        )

    if "操行" in rule["name"] and not failed:
        status = AUTO
    elif (gaps and all(_after(g) == (0, 0) for g in gaps) and any(g["taking"] for g in gaps)
          and all(c["retaking"] for c in must_retake)):
        status = THIS_TERM
    else:
        status = TODO

    how, asks = ("", []) if status == THIS_TERM else _advice(rule, title, department, cohort)
    return _item(group, title, status, progress=progress, ratio=ratio, lines=lines, how=how, asks=asks)


def _whole_item(category: dict, gap: dict, group: str) -> dict:
    """類別本身的要求比細項加總多、或細項都過了類別還沒過的部分。"""
    lines = [_whole_line(category, gap, "門")]
    if gap["taking"]:
        lines.append(_taking_line(gap, "門"))
    done_this_term = gap["taking"] and _after(gap) == (0, 0)
    progress, ratio = _progress(category, "門")
    return _item(group, f"{category['name']}（整體）", THIS_TERM if done_this_term else TODO,
                 progress=progress, ratio=ratio, lines=lines)


def _passport_item(rule: dict, group: str, hours: Optional[dict]) -> dict:
    """學生學習護照：審查表只有總時數，細項要看時數儀表板（get_hours_dashboard）。
    每一類各自要達到門檻，多的時數不能補別類（見 action_tools.get_hours_dashboard）。"""
    title = "學生學習護照時數"
    if hours is None:
        if rule["passed"]:
            return _item(group, title, DONE)
        match = HOURS_TOTAL_RE.search(rule["memo"])
        total = f"目前總時數 {float(match.group(1)):g} 小時，" if match else ""
        return _item(
            group, title, TODO,
            lines=[f"{total}還沒達到畢業門檻（各類別的時數暫時讀不到）"],
            how="每一類時數都要各自達到門檻，可以問我還差哪幾類。",
            asks=[_ask("看時數還差多少", "我的學習護照時數還差多少？")],
        )

    deficits = [
        (group_name, sub_name, sub)
        for group_name, category in hours.get("categories", {}).items()
        for sub_name, sub in category["subcategories"].items()
        if not sub["passed"]
    ]
    if not deficits:
        return _item(group, title, DONE, note="" if rule["passed"] else "時數都達標了，畢業審查表可能還沒更新")

    lines = []
    for group_name, sub_name, sub in deficits:
        name = sub_name if sub_name == group_name else f"{sub_name}（{group_name}）"
        line = (f"{name}：{_hours(sub['confirmed_hours'])} / {_hours(sub['required'])} 小時，"
                f"還差 {_hours(sub['remaining'])} 小時")
        if sub["pending_hours"]:
            line += f"，另有 {_hours(sub['pending_hours'])} 小時待核發"
            if sub["pending_hours"] >= sub["remaining"]:
                line += "，核發後就達標"
        lines.append(line)

    covered = all(sub["pending_hours"] >= sub["remaining"] for *_, sub in deficits)
    asks = [] if covered else [_ask("推薦補時數的活動", "有什麼活動可以幫我補時數？")]
    if any(sub["pending_hours"] for *_, sub in deficits):
        asks.append(_ask("看哪些活動待核發", "我有哪些活動的時數還在待核發？"))
    how = ("待核發的時數要等主辦單位核發。" if covered
           else "參加有這幾類時數的活動，每一類都要各自達到門檻，多的時數不能補別類。")
    return _item(group, title, PENDING if covered else TODO, lines=lines, how=how, asks=asks)


def _total_item(graduation: dict) -> Optional[dict]:
    earned, required = graduation["total_earned"], graduation["total_required"]
    if not required:
        return None
    title, progress = "畢業總學分", f"{earned} / {required} 學分"
    ratio = min(1.0, earned / required)
    remaining = max(0, required - earned)
    if not remaining:
        return _item(title, title, DONE, progress=progress, ratio=ratio, note=progress)

    taking: dict[tuple, dict] = {}

    def collect(rule: dict) -> None:
        if TOTAL_EXCLUDED_MEMO in rule["memo"]:
            return
        for course in rule["courses"]:
            if course.get("in_progress"):
                taking.setdefault(_course_key(course), course)
        for child in rule["children"]:
            collect(child)

    for category in graduation["categories"]:
        collect(category)
    taking_credits = sum(c["credits"] for c in taking.values())
    after = max(0, remaining - taking_credits)

    lines = [f"還差 {remaining} 學分"]
    if taking_credits:
        lines.append(f"這學期修課中 {taking_credits} 學分，" + (f"都通過的話還差 {after} 學分" if after else "都通過就夠了"))
    status = THIS_TERM if taking_credits and not after else TODO
    how = "" if status == THIS_TERM else "各類別的學分補齊之後，總學分還不夠的部分用選修補。"
    return _item(title, title, status, progress=progress, ratio=ratio, lines=lines, how=how)


# ============================================================
# 整張清單
# ============================================================

def needs_hours_dashboard(graduation: dict) -> bool:
    """審查表上有還沒通過的「學生學習護照」才要讀時數儀表板（多 8 秒左右），研究生、已經達標的不用讀。"""
    stack = list(graduation["categories"])
    while stack:
        rule = stack.pop()
        if "學習護照" in rule["name"] and not rule["passed"]:
            return True
        stack.extend(rule["children"])
    return False

def build_graduation_check(graduation: dict, hours: Optional[dict] = None) -> dict:
    """graduation：academic_tools.parse_graduation_report_html 的結果。
    hours：NCUSession.get_hours_dashboard 的結果，讀不到或用不到時是 None。
    回傳前端 GraduationCheck 元件吃的資料包。"""
    department, cohort = _subject(graduation.get("department", ""))
    items = []
    total = _total_item(graduation)
    if total:
        items.append(total)

    for category in graduation["categories"]:
        group = category["name"]
        children = category["children"]
        if not children:
            if not category["passed"] or _has_requirement(category):
                items.append(_rule_item(category, group, department, cohort))
            continue

        for child in children:
            if child["passed"] and not _has_requirement(child):
                continue  # 沒有要求的規則（例如軍訓、系所自訂的提醒），不列
            if "學習護照" in child["name"]:
                items.append(_passport_item(child, group, hours))
            else:
                items.append(_rule_item(child, group, department, cohort))

        if not category["passed"]:
            failing = [child for child in children if not child["passed"]]
            sub_gaps = [gap for child in failing for gap in _gaps(child)]
            extra = _with_whole_gap(category, sub_gaps)[len(sub_gaps):] if failing else _gaps(category)
            items.extend(_whole_item(category, gap, group) for gap in extra)

    items.sort(key=lambda item: STATUS_ORDER.index(item["status"]))
    counts = {status: sum(item["status"] == status for item in items) for status in STATUS_ORDER}
    unfinished = len(items) - counts[DONE]
    if unfinished:
        headline = f"離畢業還有 {unfinished} 項條件沒完成"
        summary = "、".join(SUMMARY_TEXT[s].format(counts[s]) for s in STATUS_ORDER if s != DONE and counts[s]) + "。"
    else:
        headline = "畢業條件都完成了"
        summary = "實際能不能畢業以各學系跟教務處的審查為準。"

    return {
        "kind": "graduation_check",
        "department": department,
        "cohort": cohort,
        "can_graduate": not unfinished,
        "headline": headline,
        "summary": summary,
        "counts": counts,
        "items": items,
        "hours_available": hours is not None,
    }
