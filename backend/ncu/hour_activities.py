"""學習護照時數頁（iNCU「時數紀錄」）活動總表的每一個活動：活動名稱、申請單位、時間，以及它登錄了哪些時數、
核發了沒。

2026-10-13 用真實帳號看過頁面：活動總表的表頭是「活動名稱/場次名稱、申請單位、申請類型、活動時間、
時數類型/時數標籤/時數/時數狀態」，最後一格是內嵌的小表格（一列一筆時數：學習護照或軟實力、類別、
時數、「時數待核發」或「已核發」）。最後一格是空的，代表主辦單位還沒登錄這個活動的時數。
原本 action_tools.get_hours_dashboard 只讀內嵌小表格算各類別的總數，活動名稱整張略過，
使用者問「9/23 的新生電影節時數核發了沒」「國際視野待核發的是什麼活動」就答不出來。
"""

import re
from typing import Any, Optional

# 在頁面上執行，回傳活動總表每一列：前四格的文字跟內嵌小表格每一列的文字（只取活動總表自己的列，
# 不會把內嵌小表格的列也當成活動）
HOUR_ACTIVITY_ROWS_JS = """() => {
    const overview = Array.from(document.querySelectorAll('table')).find(
        t => /活動名稱|申請單位/.test((t.querySelector('tr') || {}).innerText || ''));
    if (!overview) return [];
    return Array.from(overview.rows).filter(r => r.closest('table') === overview).slice(1).map(row => {
        const cells = Array.from(row.cells);
        const nested = cells.length ? cells[cells.length - 1].querySelector('table') : null;
        return {
            cells: cells.slice(0, 4).map(c => c.innerText.trim()),
            hours: nested ? Array.from(nested.rows).map(r => Array.from(r.cells).map(c => c.innerText.trim())) : [],
        };
    });
}"""

_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?")


def _status(text: str) -> str:
    if "待核發" in text:
        return "待核發"
    if "已核發" in text:
        return "已核發"
    return text.strip()


def _hours(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_activity_rows(rows: list[dict]) -> list[dict[str, Any]]:
    """HOUR_ACTIVITY_ROWS_JS 的結果整理成活動清單，格式對不上的列略過。"""
    activities = []
    for row in rows or []:
        cells = row.get("cells") or []
        if len(cells) < 4 or not cells[0]:
            continue
        name, unit, kind, period = (re.sub(r"\s+", " ", cell).strip() for cell in cells[:4])
        times = _DATETIME_RE.findall(period)
        hours = [
            {"type": entry[0], "category": entry[1], "hours": _hours(entry[2]), "status": _status(entry[3])}
            for entry in row.get("hours") or []
            if len(entry) == 4
        ]
        activities.append({
            "name": name,
            "unit": unit,
            "kind": kind,
            "start": times[0] if times else "",
            "end": times[1] if len(times) > 1 else "",
            "hours": hours,
        })
    return activities
