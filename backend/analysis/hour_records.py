"""回答「某個活動的時數核發了沒」「國際視野待核發的是什麼活動」這類問題：從學習護照時數頁的活動總表
（backend/ncu/hour_activities.py）挑出符合的活動，寫成一段直接回答加一張表。不呼叫模型，回答可預期。

時數狀態有三種：已核發、待核發（主辦單位登錄了，還沒核發），以及時數欄是空的（主辦單位還沒登錄時數）。
第三種在網頁上看起來跟「沒有時數」一樣，使用者最常問的就是它，回答要講清楚它還不是待核發。
還沒有時數或還在待核發的活動，再對照「我的報名」頁（activityManagement/signup）的簽到簽退：
簽到簽退都完成了是主辦單位還沒登錄，沒簽退的話可能就不會給時數，兩種情況使用者要做的事不一樣。
"""

import re
from datetime import datetime
from typing import Any, Optional

from backend.ncu.action_tools import STUDY_PASSPORT_SUBCATEGORY_REQUIREMENTS

STATUS_FILTERS = ("全部", "待核發", "已核發", "還沒有時數")
NO_HOURS = "還沒有時數"
MAX_ROWS = 30

# 細項（大一週會、院週會…）屬於哪個大類別（生活知能…）：問「生活知能」要把底下的細項都算進來
_GROUP_OF = {sub: group for group, subs in STUDY_PASSPORT_SUBCATEGORY_REQUIREMENTS.items() for sub in subs}


def _plain(text: str) -> str:
    """比對用：拿掉空白跟標點，全形英數字轉半形、英文轉小寫。"""
    text = (text or "").translate({code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}).lower()
    return re.sub(r"[\s\W_]+", "", text)


def parse_month_day(text: str) -> Optional[tuple[int, int]]:
    """「9/23」「09-23」「2026-09-23」「9月23日」都讀成 (9, 23)，讀不出來回傳 None。"""
    numbers = [int(n) for n in re.findall(r"\d+", text or "")]
    if len(numbers) >= 3 and numbers[0] > 31:
        numbers = numbers[1:]  # 開頭是年份
    if len(numbers) >= 2 and 1 <= numbers[0] <= 12 and 1 <= numbers[1] <= 31:
        return numbers[0], numbers[1]
    return None


def _category_matches(entry: dict, category: str) -> bool:
    wanted = _plain(category)
    return not wanted or wanted in {_plain(entry["category"]), _plain(_GROUP_OF.get(entry["category"], ""))}


def find_activities(activities: list[dict[str, Any]], keyword: str = "", date: str = "",
                    category: str = "", status: str = "全部") -> list[dict[str, Any]]:
    """挑出符合條件的活動，新的在前面。每個活動多一個 matched_hours：符合類別跟狀態的那幾筆時數
    （條件是「還沒有時數」時是空的）。"""
    words = [_plain(word) for word in (keyword or "").split() if _plain(word)]
    month_day = parse_month_day(date)
    results = []
    for activity in activities:
        name = _plain(activity["name"])
        if any(word not in name for word in words):
            continue
        if month_day and parse_month_day(activity["start"]) != month_day:
            continue
        if status == NO_HOURS:
            if activity["hours"]:
                continue
            matched = []
        else:
            matched = [
                entry for entry in activity["hours"]
                if _category_matches(entry, category) and status in ("全部", "", entry["status"])
            ]
            # 有指定類別或狀態時，至少要有一筆時數符合，沒有時數的活動只在沒指定時列出來
            if (category or status not in ("全部", "")) and not matched:
                continue
        results.append({**activity, "matched_hours": matched})
    return sorted(results, key=lambda activity: activity["start"], reverse=True)


def needs_checkin(activities: list[dict[str, Any]], limit: int = 10) -> bool:
    """查到的活動有還沒有時數的（而且不多）時，才值得多花幾秒去讀報名紀錄的簽到簽退。
    待核發的不用：主辦單位已經登錄時數了，簽到簽退幫不上忙，讀報名紀錄要多等約 6 秒。"""
    waiting = [activity for activity in activities if not activity["hours"]]
    return 0 < len(waiting) <= limit


def _start_minute(text: str) -> str:
    """「開始：2026-09-23 18:00 結束：…」「2026-09-23 18:00」都取出 "2026-09-23 18:00"。"""
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})", text or "")
    return f"{match.group(1)} {int(match.group(2)):02d}:{match.group(3)}" if match else ""


def matching_registration(activity: dict[str, Any], registrations: list[dict[str, str]]) -> Optional[dict[str, str]]:
    """時數頁的活動在報名紀錄裡是哪一筆，找不到回傳 None。兩頁的名稱寫法不一樣（報名頁的場次名稱
    常附英文），用開始時間相同、場次名稱互相包含來認是同一個活動。"""
    start = _start_minute(activity["start"])
    session_name = _plain(activity["name"].rsplit("/", 1)[-1])
    if not start or not session_name:
        return None
    for registration in registrations or []:
        if _start_minute(registration.get("活動場次時間", "")) != start:
            continue
        registered = _plain(registration.get("場次名稱", "")) or _plain(registration.get("活動名稱", ""))
        if registered and (session_name in registered or registered in session_name):
            return registration
    return None


def attach_checkin(activities: list[dict[str, Any]], registrations: list[dict[str, str]]) -> None:
    """把報名紀錄的「簽到/簽退」接到時數頁的活動上（activity["checkin"]）。"""
    for activity in activities:
        registration = matching_registration(activity, registrations)
        if registration is not None:
            activity["checkin"] = registration.get("簽到/簽退", "")


def _day(start: str) -> str:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", start or "")
    return f"{match.group(1)}/{int(match.group(2))}/{int(match.group(3))}" if match else "時間不明"


def _hours_text(entry: dict) -> str:
    hours = entry["hours"]
    amount = f"{hours:g} 小時" if hours is not None else "時數不明"
    prefix = "" if entry["type"] == "學習護照" else f"{entry['type']}："
    return f"{prefix}{entry['category']} {amount}"


def _describe(keyword: str, date: str, category: str, status: str) -> str:
    """把查詢條件寫成一句話，例如「9/23、「新生電影節」的」「國際視野、待核發的」。"""
    parts = []
    month_day = parse_month_day(date)
    if month_day:
        parts.append(f"{month_day[0]}/{month_day[1]}")
    if keyword.strip():
        parts.append(f"「{keyword.strip()}」")
    if category.strip():
        parts.append(category.strip())
    if status not in ("全部", ""):
        parts.append(status)
    return "、".join(parts)


def _checkin_sentence(activity: dict) -> str:
    checkin = activity.get("checkin", "")
    if not checkin:
        return ""
    if "未簽到" in checkin:
        return f"報名紀錄顯示你「{checkin}」，沒有簽到的話通常不會有時數。"
    if "未簽退" in checkin:
        return f"報名紀錄顯示你「{checkin}」，沒有簽退的話主辦單位可能不會登錄時數。"
    return f"報名紀錄顯示你「{checkin}」。"


def _single_answer(activity: dict, now: datetime) -> str:
    head = f"「{activity['name']}」（{_day(activity['start'])}，{activity['unit']}）"
    unit = activity["unit"] or "主辦單位"
    if not activity["hours"]:
        start = _start_minute(activity["start"])
        if start and start > now.strftime("%Y-%m-%d %H:%M"):
            return f"{head}還沒舉行，時數要等活動結束、主辦單位登錄之後才會出現。"
        checkin = activity.get("checkin", "")
        if checkin and "未" not in checkin:
            return (
                f"{head}你有完成簽到簽退（報名紀錄顯示「{checkin}」），但時數欄還是空的，"
                "代表主辦單位**還沒登錄這個活動的時數**，所以也還不是「待核發」。"
                f"如果活動已經結束一段時間，可以向{unit}確認。"
            )
        return (
            f"{head}目前**還沒有任何時數紀錄**：時數欄是空的，代表主辦單位還沒登錄這個活動的時數，"
            f"所以也還不是「待核發」。{_checkin_sentence(activity)}"
            f"如果活動已經結束一段時間，可以向{unit}確認。"
        )
    pending = [entry for entry in activity["hours"] if entry["status"] == "待核發"]
    issued = [entry for entry in activity["hours"] if entry["status"] == "已核發"]
    sentences = []
    if pending:
        sentences.append(f"{'、'.join(_hours_text(e) for e in pending)}**待核發**（主辦單位已經登錄，還在等核發）")
    if issued:
        sentences.append(f"{'、'.join(_hours_text(e) for e in issued)}**已核發**")
    others = [entry for entry in activity["hours"] if entry not in pending and entry not in issued]
    if others:
        sentences.append("、".join(f"{_hours_text(e)}（{e['status']}）" for e in others))
    return f"{head}的時數：" + "，".join(sentences) + "。" + (_checkin_sentence(activity) if pending else "")


def _table(activities: list[dict]) -> str:
    shown = activities[:MAX_ROWS]
    with_checkin = any(activity.get("checkin") for activity in shown)
    columns = ["活動", "日期", "時數", "狀態"] + (["簽到/簽退"] if with_checkin else [])
    lines = ["| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
    for activity in shown:
        entries = activity["matched_hours"] or activity["hours"]
        cells = [
            activity["name"].replace("|", "｜"),
            _day(activity["start"]),
            "、".join(_hours_text(entry) for entry in entries) or "—",
            "、".join(dict.fromkeys(entry["status"] for entry in entries)) or NO_HOURS,
        ]
        if with_checkin:
            cells.append(activity.get("checkin") or "—")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def answer(activities: list[dict[str, Any]], keyword: str = "", date: str = "", category: str = "",
           status: str = "全部", registrations: Optional[list[dict[str, str]]] = None,
           now: Optional[datetime] = None) -> str:
    """查詢結果寫成 Markdown：第一句直接回答，符合的活動不只一個時再附表。
    registrations 是「我的報名」頁的紀錄，有給就把簽到簽退接到活動上一起回答。"""
    now = now or datetime.now()
    condition = _describe(keyword, date, category, status)
    matches = find_activities(activities, keyword, date, category, status)
    if registrations:
        attach_checkin(matches, registrations)
    if not activities:
        return "你的學習護照時數紀錄裡目前沒有任何活動。"
    if not matches:
        if keyword.strip() or parse_month_day(date):
            return (
                f"你的時數紀錄裡找不到{condition}的活動。時數紀錄只列出你報名過的活動，"
                "如果有參加卻沒出現在這裡，可以向主辦單位確認報名跟簽到紀錄。"
            )
        return f"你目前沒有{condition}的活動。" if condition else "你的時數紀錄裡沒有符合的活動。"
    if len(matches) == 1 and (keyword.strip() or parse_month_day(date)):
        return _single_answer(matches[0], now)

    total = sum(entry["hours"] or 0 for activity in matches for entry in activity["matched_hours"])
    summary = f"{condition}的活動" if condition else "你時數紀錄裡的活動"
    headline = f"{summary}有 {len(matches)} 個"
    if total and (category or status not in ("全部", "")):
        headline += f"，共 {total:g} 小時"
    note = f"\n\n只列出最新的 {MAX_ROWS} 個。" if len(matches) > MAX_ROWS else ""
    return f"{headline}：\n\n{_table(matches)}{note}"
