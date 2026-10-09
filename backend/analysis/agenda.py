"""我的行程：把課表、已報名的活動跟校曆整理成每天的行程（企劃書的「個人行程」）。

- 課表（action_tools.get_schedule）是每週固定的時段，套到日期上：只在學期的上課期間（開始上課到
  寒假、暑假開始前一天）出現，放假、補假、停課的日子不列課，並標出原因。
- 活動報名紀錄（NCUSession.get_my_activity_registrations）有場次的開始跟結束時間，列在那幾天。
- 校曆的事件（academic_calendar）在開始那天列出，好幾天的（加退選、停修受理）在最後一天再提醒一次。

這裡只整理資料、不呼叫模型，也不連網路（課表跟報名紀錄由 agent_tools 先抓好傳進來）。
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from backend.analysis.academic_calendar import (
    CalendarEvent,
    format_day,
    no_class_reason,
    semester_class_period,
)

WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
# 課表的時間欄偶爾是空的，用節次補（跟前端 ScheduleTable.jsx 的對照一樣）
PERIOD_TIMES = {
    "第一節": "08:00-08:50", "第二節": "09:00-09:50", "第三節": "10:00-10:50", "第四節": "11:00-11:50",
    "中午": "12:00-12:50", "第Ｚ節": "12:00-12:50", "第五節": "13:00-13:50", "第六節": "14:00-14:50",
    "第七節": "15:00-15:50", "第八節": "16:00-16:50", "第九節": "17:00-17:50", "第Ａ節": "18:00-18:50",
    "第Ｂ節": "19:00-19:50", "第Ｃ節": "20:00-20:50",
}


def _full_width_letters(text: str) -> str:
    return re.sub(r"[A-Za-z]", lambda m: chr(ord(m.group(0).upper()) + 0xFEE0), text)


def _period_times(item: dict) -> tuple[str, str]:
    times = _TIME_RE.findall(item.get("time") or "")
    if len(times) < 2:
        times = (PERIOD_TIMES.get(_full_width_letters((item.get("period") or "").strip()), "")).split("-")
    if len(times) < 2 or not times[0]:
        return "", ""
    return times[0].zfill(5), times[-1].zfill(5)


def classes_on(schedule: list[dict], weekday: int) -> list[dict]:
    """某個星期幾的課，連續好幾節的同一門課合併成一筆（例如 09:00-11:50）。"""
    day_name = WEEKDAY_NAMES[weekday]
    blocks = []
    for item in schedule or []:
        if (item.get("day") or "").strip() not in (day_name, day_name[-1], f"週{day_name[-1]}"):
            continue
        start, end = _period_times(item)
        lines = [line for line in item.get("details") or [] if line]
        if not lines:
            continue
        blocks.append({"start": start, "end": end, "lines": lines, "period": (item.get("period") or "").strip()})
    blocks.sort(key=lambda b: b["start"] or "99:99")

    merged: list[dict] = []
    for block in blocks:
        last = merged[-1] if merged else None
        if last and last["lines"] == block["lines"] and _consecutive(last["end"], block["start"]):
            last["end"] = block["end"]
            last["periods"].append(block["period"])
        else:
            merged.append({**block, "periods": [block["period"]]})
    return [{"time": f"{b['start']}-{b['end']}" if b["start"] else "", **_course_title(b["lines"]), "periods": b["periods"]}
            for b in merged]


# 課號：CE3007-A、PE2151-*、LN0027-C
_COURSE_CODE_RE = re.compile(r"^[A-Z]{2,3}\d{3,4}(-\S+)?$")


def _course_title(lines: list[str]) -> dict:
    """課表格子裡的幾行字是「課號、課名、教室」，標題用課名，課號跟教室放在後面。"""
    if len(lines) >= 2 and _COURSE_CODE_RE.match(lines[0]):
        return {"title": lines[1], "detail": "｜".join(lines[2:] + [lines[0]])}
    return {"title": lines[0], "detail": "｜".join(lines[1:])}


def _consecutive(previous_end: str, next_start: str) -> bool:
    """上一節結束跟下一節開始只差下課時間（20 分鐘以內）。"""
    if not previous_end or not next_start:
        return False
    to_minutes = lambda t: int(t[:2]) * 60 + int(t[3:5])  # noqa: E731
    return 0 <= to_minutes(next_start) - to_minutes(previous_end) <= 20


def _registration_times(item: dict) -> list[datetime]:
    return [datetime.strptime(t, "%Y-%m-%d %H:%M") for t in _DATETIME_RE.findall(item.get("活動場次時間", ""))]


def activities_on(registrations: list[dict], day: date) -> list[dict]:
    """這天有的已報名活動場次。"""
    result = []
    for item in registrations or []:
        times = _registration_times(item)
        if not times or not (times[0].date() <= day <= times[-1].date()):
            continue
        start, end = times[0], times[-1]
        same_day = start.date() == end.date()
        result.append({
            "time": f"{start:%H:%M}-{end:%H:%M}" if same_day else f"{start:%m/%d %H:%M} 到 {end:%m/%d %H:%M}",
            "title": item.get("活動名稱") or item.get("場次名稱") or "（沒有名稱的活動）",
            "session": item.get("場次名稱", "") if item.get("場次名稱") != item.get("活動名稱") else "",
            "place": item.get("活動地點", ""),
            "status": item.get("報名狀態", ""),
            "sort": start.strftime("%H:%M") if start.date() == day else "00:00",
        })
    return sorted(result, key=lambda a: a["sort"])


def calendar_items_on(events: list[CalendarEvent], day: date) -> list[dict]:
    """校曆上這天開始的事件，好幾天的事件在最後一天再列一次（例如「加退選最後一天」）。
    校務會議、教師繳交成績這類不是學生要做的事不列。"""
    items = []
    for event in events:
        if not event.for_students:
            continue
        if event.start == day:
            note = f"到 {format_day(event.end)}" if event.end != event.start else ""
            items.append({"title": event.title, "kind": event.kind, "note": note})
        elif event.end == day:
            items.append({"title": event.title, "kind": event.kind, "note": "最後一天"})
    return items


def build_agenda(
    first: date, last: date, events: list[CalendarEvent], schedule: Optional[list[dict]],
    registrations: Optional[list[dict]], title: str, today: Optional[date] = None,
) -> dict[str, Any]:
    """給前端「我的行程」卡片的資料（MessageContent 的 kind === "personal_agenda"）。

    schedule、registrations 是 None 代表那份資料沒抓到（例如課務系統一時連不上），
    會在 notes 說明，其他資料照樣列出。
    """
    today = today or date.today()
    days = []
    out_of_term = False
    day = first
    while day <= last:
        holiday = no_class_reason(events, day)
        in_term = semester_class_period(events, day) is not None
        out_of_term = out_of_term or not in_term
        classes = classes_on(schedule, day.weekday()) if schedule and in_term and not holiday else []
        days.append({
            "date": day.isoformat(),
            "label": format_day(day),
            "is_today": day == today,
            "holiday": holiday,
            "classes": classes,
            "activities": activities_on(registrations, day),
            "events": calendar_items_on(events, day),
        })
        day += timedelta(days=1)

    notes = []
    if schedule is None:
        notes.append("這次沒有抓到課表（課務系統可能暫時連不上），所以沒有列出上課時間。")
    elif not schedule:
        notes.append("課表上沒有任何課。")
    elif out_of_term:
        notes.append("有些日子不在學期的上課期間（寒暑假或還沒開始上課），那些天沒有列課。")
    if registrations is None:
        notes.append("這次沒有抓到活動報名紀錄，所以沒有列出活動。")
    if not events:
        notes.append("找不到校曆，沒有列出放假跟學校的重要日期。")
    return {"kind": "personal_agenda", "title": title, "days": days, "notes": notes}
