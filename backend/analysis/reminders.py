"""開場畫面的「最近要注意」卡片：助理不等使用者開口，先提醒接下來幾天要注意的事。

- 校曆（academic_calendar）：接下來 7 天開始或結束的事件（停修、加退選、繳費截止、期中考、放假）。
  好幾天的受理期間只在開始那天跟最後一天出現，跟「我的行程」一樣。
- 已報名的活動（iNCU「我的報名」）：接下來 7 天的場次，提醒簽到、簽退。
- 符合資格的獎學金（scholarship_tools.match_scholarships 的 eligible）：兩週內截止的，寫還剩幾天。
- 活動時數（時數頁的活動總表，hour_activities）：活動結束兩週以上，時數還在待核發或主辦單位還沒登錄的。
沒登入只有校曆。

這裡只整理資料，不呼叫模型也不連網路。iNCU 的資料由 backend/agent/reminder_prep.py 在登入後的背景
先抓好，「明天」「還剩幾天」每次都照現在的時間重新算。
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from backend.analysis.academic_calendar import CalendarEvent, format_day
from backend.analysis.hour_records import matching_registration
from backend.analysis.scholarship_tools import deadline_date

WINDOW_DAYS = 7  # 校曆跟活動：今天起 7 天
SCHOLARSHIP_DAYS = 14  # 獎學金截止前兩週就提醒，準備申請資料要時間
HOURS_WAIT_DAYS = 14  # 活動結束超過兩週，時數還沒核發才算等很久
HOURS_LOOKBACK_DAYS = 120  # 更早的活動不提醒（多半本來就不會有時數，提醒了也做不了什麼）
MAX_HOURS_ITEMS = 3

PARTS = ("activities", "hours", "scholarships")
PART_LABELS = {"activities": "活動報名紀錄", "hours": "時數紀錄", "scholarships": "獎學金"}

# 校曆事件的標籤跟顏色（跟前端 CampusCalendar.jsx 的 EVENT_KINDS 一樣）
_CALENDAR_BADGES = {
    "holiday": ("放假", "ok"),
    "exam": ("考試", "warn"),
    "course": ("選課", "info"),
    "deadline": ("截止", "warn"),
}
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

ASK_AGENDA = "我接下來 7 天有什麼行程？"
ASK_SCHOLARSHIPS = "我可以申請哪些獎學金？"
ASK_PENDING_HOURS = "我有哪些活動的時數待核發？"


def day_label(day: date, today: date) -> str:
    if day == today:
        return "今天"
    if day == today + timedelta(days=1):
        return "明天"
    return format_day(day)


def _clean(text: str) -> str:
    """學校網站上的名稱偶爾有分號，使用者不喜歡分號，換成逗號。"""
    return re.sub(r"\s+", " ", (text or "").replace("；", "，")).strip()


def _item(kind: str, sort: str, when: str, title: str, badge: str, tone: str = "",
          detail: str = "", hint: str = "", ask: str = "") -> dict[str, str]:
    return {
        "kind": kind, "sort": sort, "when": when, "title": _clean(title), "badge": badge, "tone": tone,
        "detail": _clean(detail), "hint": _clean(hint), "ask": _clean(ask),
    }


def calendar_reminders(events: list[CalendarEvent], today: date) -> list[dict]:
    """校曆上接下來 7 天開始或結束的事件。校務會議這類不是學生要做的事不列。"""
    last = today + timedelta(days=WINDOW_DAYS - 1)
    items = []
    for event in events:
        if not event.for_students:
            continue
        if today <= event.start <= last:
            day, detail = event.start, f"到 {format_day(event.end)}" if event.end != event.start else ""
        elif today <= event.end <= last:
            day, detail = event.end, "最後一天"
        else:
            continue
        badge, tone = _CALENDAR_BADGES.get(event.kind, ("校曆", ""))
        items.append(_item("calendar", f"{day.isoformat()} 00:00", day_label(day, today), event.title,
                           badge, tone, detail=detail))
    return items


def _session_times(registration: dict) -> list[datetime]:
    return [datetime.strptime(t, "%Y-%m-%d %H:%M") for t in _DATETIME_RE.findall(registration.get("活動場次時間", ""))]


def _checkin_hint(checkin: str, ongoing: bool) -> str:
    if "已簽退" in checkin:
        return ""
    if ongoing and "已簽到" in checkin:
        return "離開前記得簽退"
    return "記得簽到、簽退，不然可能拿不到時數"


def activity_reminders(registrations: Optional[list[dict]], now: datetime) -> list[dict]:
    """接下來 7 天（或正在進行）的已報名場次。取消的報名不列。"""
    today = now.date()
    last = today + timedelta(days=WINDOW_DAYS - 1)
    items = []
    for registration in registrations or []:
        status = registration.get("報名狀態", "")
        times = _session_times(registration)
        if "取消" in status or not times:
            continue
        start, end = times[0], times[-1]
        if end < now or start.date() > last:
            continue
        ongoing = start <= now
        title = registration.get("活動名稱") or registration.get("場次名稱") or "（沒有名稱的活動）"
        session = registration.get("場次名稱", "")
        details = [session if session and session != title else "", registration.get("活動地點", ""),
                   "目前是候補" if "候補" in status else ""]
        items.append(_item(
            "activity", f"{max(start, now):%Y-%m-%d %H:%M}",
            "進行中" if ongoing else f"{day_label(start.date(), today)} {start:%H:%M}",
            title, "活動", "info", detail="｜".join(d for d in details if d),
            hint=_checkin_hint(registration.get("簽到/簽退", ""), ongoing), ask=ASK_AGENDA,
        ))
    return items


def scholarship_reminders(scholarships: Optional[list[dict]], today: date) -> list[dict]:
    """符合資格、兩週內截止的獎學金。截止日來自每學期的〈各項獎學金一覽表〉，沒寫截止日的不列。"""
    items = []
    for scholarship in scholarships or []:
        due = deadline_date(scholarship.get("deadline", ""), scholarship.get("deadline_term", ""))
        if due is None or not 0 <= (due - today).days < SCHOLARSHIP_DAYS:
            continue
        left = (due - today).days
        when = {0: "今天截止", 1: "明天截止"}.get(left, f"還剩 {left} 天")
        amount = scholarship.get("amount", "")
        items.append(_item(
            "scholarship", f"{due.isoformat()} 23:59", when, scholarship.get("name", ""), "獎學金", "warn",
            detail="｜".join(x for x in (f"{format_day(due)}截止", amount) if x),
            hint="你的成績符合申請資格", ask=ASK_SCHOLARSHIPS,
        ))
    return items


def _activity_date(activity: dict) -> Optional[date]:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", activity.get("end") or activity.get("start") or "")
    return date(*map(int, match.groups())) if match else None


def _hours_text(entries: list[dict]) -> str:
    return "、".join(f"{e['category']} {e['hours']:g} 小時" if e.get("hours") is not None else e["category"]
                     for e in entries)


def hours_reminders(hour_activities: Optional[list[dict]], registrations: Optional[list[dict]],
                    now: datetime) -> list[dict]:
    """活動結束兩週以上，時數還在待核發或主辦單位還沒登錄的活動，等最久的在前面。

    報名紀錄寫「未簽到」的不列（多半是沒去，不是在等時數），報名時就標「不提供時數」的也不列。"""
    today = now.date()
    waiting = []
    for activity in hour_activities or []:
        ended = _activity_date(activity)
        if ended is None or not HOURS_WAIT_DAYS <= (today - ended).days <= HOURS_LOOKBACK_DAYS:
            continue
        # 時數頁會把一個活動的每個類別都列出來，沒有給時數的類別是 0 小時，不算在等的時數
        pending = [entry for entry in activity["hours"]
                   if entry["status"] == "待核發" and entry.get("hours") != 0]
        if activity["hours"] and not pending:
            continue  # 都核發了
        registration = matching_registration(activity, registrations) if registrations else None
        checkin = (registration or {}).get("簽到/簽退", "")
        if "未簽到" in checkin or "不提供時數" in (registration or {}).get("時數標籤", ""):
            continue
        days = (today - ended).days
        unit = activity.get("unit") or "主辦單位"
        hint = ""
        if pending:
            status = f"{_hours_text(pending)}待核發"
        else:
            status = "主辦單位還沒登錄時數"
            if "未簽退" in checkin:
                hint = "報名紀錄顯示你沒有簽退，可能不會有時數，可以向主辦單位確認"
            elif "已簽退" in checkin:
                hint = f"你有簽到簽退，可以向{unit}確認"
        # 點了直接問這個活動（時數頁的名稱是「活動/場次」，問場次名稱就好）
        ask = f"「{activity['name'].rsplit('/', 1)[-1].strip()}」的時數核發了沒？"
        # 左邊寫等了幾天，不寫活動日期（卡片其他項目的日期都是接下來的，過去的日期放在這裡容易看錯）
        waiting.append((days, _item("hours", "", f"等了 {days} 天", activity["name"], "時數", "",
                                    detail=f"{format_day(ended)}的活動，{status}", hint=hint, ask=ask)))
    waiting.sort(key=lambda pair: -pair[0])
    items = [item for _, item in waiting[:MAX_HOURS_ITEMS]]
    if len(waiting) > MAX_HOURS_ITEMS:
        items.append(_item("hours", "", "", f"還有 {len(waiting) - MAX_HOURS_ITEMS} 個活動的時數也還沒核發",
                           "時數", "", ask=ASK_PENDING_HOURS))
    return items


def build_reminders(
    now: datetime,
    events: list[CalendarEvent],
    logged_in: bool,
    registrations: Optional[list[dict]] = None,
    hour_activities: Optional[list[dict]] = None,
    scholarships: Optional[list[dict]] = None,
    loading: Iterable[str] = (),
    failed: Iterable[str] = (),
) -> dict[str, Any]:
    """給前端 RemindersCard 的資料。loading 是還在背景準備的部分、failed 是這次沒抓到的部分
    （PARTS 裡的名稱），沒登入時這兩個跟 iNCU 的資料都不用給。

    有日期的（校曆、活動、獎學金截止日）照時間排，時數放最後面。status 是 loading 時前端會再問一次。"""
    today = now.date()
    items = calendar_reminders(events, today)
    loading = [part for part in PARTS if logged_in and part in set(loading)]
    failed = [part for part in PARTS if logged_in and part in set(failed) and part not in loading]
    if logged_in:
        items += activity_reminders(registrations, now) + scholarship_reminders(scholarships, today)
    items.sort(key=lambda item: item["sort"])
    if logged_in:
        items += hours_reminders(hour_activities, registrations, now)

    notes = []
    if not events:
        notes.append("目前沒有收錄校曆，沒有列出學校的重要日期。")
    if failed:
        notes.append(f"這次沒有抓到你的{'、'.join(PART_LABELS[p] for p in failed)}，直接問我的時候會再查一次。")
    return {
        "title": "最近要注意",
        "range": f"{format_day(today)}～{format_day(today + timedelta(days=WINDOW_DAYS - 1))}",
        "logged_in": logged_in,
        "status": "loading" if loading else "ready",
        "loading": [PART_LABELS[p] for p in loading],
        "items": [{key: value for key, value in item.items() if key != "sort"} for item in items],
        "notes": notes,
    }
