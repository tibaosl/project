"""把校曆、課表、已報名的活動匯出成 iCalendar（.ics）檔，可以匯入 Google 行事曆、Outlook、手機的行事曆。

- 校曆：整個學年給學生看的事件（agenda.calendar_items_on 一樣不列校務會議這類），做成整天的事件。
- 課表：每週固定的時段套到這學期上課期間的每一天（還沒開學、寒暑假就用下一個學期），
  放假、補假、停課的日子跳過，跟「我的行程」的規則一樣（agenda.classes_on）。
- 活動：報名紀錄上的場次時間。

時間一律換成 UTC 寫（台灣沒有日光節約時間，直接減 8 小時），不用另外附 VTIMEZONE，各家行事曆都讀得懂。
每筆事件的 UID 由內容算出來，同一份行事曆重新匯入時會更新原本的事件，不會整批重複。
這裡只整理資料，不呼叫模型也不連網路（課表、報名紀錄由呼叫的人先抓好）。
"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from backend.analysis.academic_calendar import CalendarEvent, no_class_reason, semester_class_period
from backend.analysis.agenda import _registration_times, classes_on

TAIPEI = timezone(timedelta(hours=8))
PRODUCT_ID = "-//NCUXplore//Campus Calendar//ZH-TW"


def term_for_export(events: list[CalendarEvent], today: date) -> Optional[tuple[date, date]]:
    """要匯出課表的學期上課期間：今天在上課期間就是這學期，寒暑假或還沒開學就用下一個學期。"""
    current = semester_class_period(events, today)
    if current:
        return current
    for start in sorted(e.start for e in events if "開始上課" in e.title and e.start > today):
        period = semester_class_period(events, start)
        if period:
            return period
    return None


def _escape(text: str) -> str:
    """RFC 5545 的 TEXT：反斜線、逗號、分號要跳脫，換行寫成 \\n。"""
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """一行超過 75 個位元組要折行（接續行開頭空一格），中文字是 3 個位元組，不能從字的中間切開。"""
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(moment: datetime) -> str:
    return moment.replace(tzinfo=TAIPEI).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _uid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24] + "@ncuxplore"


def _event(uid: str, summary: str, stamp: str, start: str, end: str, location: str = "", description: str = "",
           categories: str = "") -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", start, end, f"SUMMARY:{_escape(summary)}"]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if categories:
        lines.append(f"CATEGORIES:{_escape(categories)}")
    lines.append("END:VEVENT")
    return lines


def _calendar_events(events: Iterable[CalendarEvent], stamp: str) -> list[str]:
    lines = []
    for event in events:
        if not event.for_students:
            continue
        lines += _event(
            _uid("calendar", event.start.isoformat(), event.end.isoformat(), event.title),
            event.title, stamp,
            f"DTSTART;VALUE=DATE:{event.start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{event.end + timedelta(days=1):%Y%m%d}",  # 整天事件的結束日是「不含」的那天
            description="中央大學校曆", categories="校曆",
        )
    return lines


def _class_location_and_note(detail: str) -> tuple[str, str]:
    """agenda 把教室跟課號用「｜」串在 detail，第一段通常是教室。"""
    parts = [p for p in detail.split("｜") if p]
    return (parts[0] if parts else ""), "｜".join(parts[1:])


def _class_events(schedule: list[dict], events: list[CalendarEvent], term: tuple[date, date], stamp: str) -> list[str]:
    lines = []
    day, last = term
    while day <= last:
        if not no_class_reason(events, day):
            for block in classes_on(schedule, day.weekday()):
                if not block["time"]:
                    continue  # 沒有時間的格子放不進行事曆
                start, end = block["time"].split("-")
                location, note = _class_location_and_note(block["detail"])
                begin = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
                finish = datetime.combine(day, datetime.strptime(end, "%H:%M").time())
                lines += _event(
                    _uid("class", day.isoformat(), block["time"], block["title"]),
                    block["title"], stamp, f"DTSTART:{_utc(begin)}", f"DTEND:{_utc(finish)}",
                    location=location, description=note, categories="課程",
                )
        day += timedelta(days=1)
    return lines


def _activity_events(registrations: list[dict], stamp: str) -> list[str]:
    lines = []
    for item in registrations:
        times = _registration_times(item)
        if not times:
            continue
        title = item.get("活動名稱") or item.get("場次名稱") or "校園活動"
        session = item.get("場次名稱", "")
        description = "｜".join(x for x in (session if session != title else "", item.get("報名狀態", "")) if x)
        lines += _event(
            _uid("activity", title, session, times[0].isoformat()),
            title, stamp, f"DTSTART:{_utc(times[0])}", f"DTEND:{_utc(max(times[-1], times[0] + timedelta(minutes=30)))}",
            location=item.get("活動地點", ""), description=description, categories="活動",
        )
    return lines


def build_ics(
    events: list[CalendarEvent],
    schedule: Optional[list[dict]] = None,
    registrations: Optional[list[dict]] = None,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
) -> str:
    """組出整份 .ics 文字。schedule、registrations 沒給（訪客、沒抓到）就只有校曆。"""
    today = today or date.today()
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "中央大學校曆" if not (schedule or registrations) else "我的中央大學行事曆"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODUCT_ID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}", "X-WR-TIMEZONE:Asia/Taipei",
    ]
    lines += _calendar_events(events, stamp)
    term = term_for_export(events, today)
    if schedule and term:
        lines += _class_events(schedule, events, term, stamp)
    if registrations:
        lines += _activity_events(registrations, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def export_summary(events: list[CalendarEvent], schedule: Optional[list[dict]], registrations: Optional[list[dict]],
                   today: Optional[date] = None) -> dict:
    """匯出內容的摘要（給卡片顯示「會匯出哪些東西」）。"""
    today = today or date.today()
    term = term_for_export(events, today) if schedule else None
    return {
        "calendar_events": sum(1 for e in events if e.for_students),
        "term": [term[0].isoformat(), term[1].isoformat()] if term else None,
        "courses": len({b["title"] for weekday in range(7) for b in classes_on(schedule or [], weekday)}),
        "activities": sum(1 for item in registrations or [] if _registration_times(item)),
    }
