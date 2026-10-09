"""校曆：把教務處的〈XXX 學年度校曆〉解析成有日期的事件，給「校曆查詢」跟「我的行程」用。

校曆是固定格式的表格（年、月、週、日～六、記事），記事欄的寫法：
    1日 115學年度第1學期開始
    2日～16日 加退選
    19日～11月27日 受理課程停修申請
    14日～116年1月15日 受理舊生學雜費減免申請
    1日起 碩博生新生入住
    25日 行憲紀念日放假1日 ； 寒假開始        ← 「；」後面是同一天的另一件事
直接用規則解析，不呼叫模型：結果固定、可以測試，每次查詢也不用等。檔案來自爬蟲（data/教務處/），
文字用 rag_documents.parse_file 的快取。
"""

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from backend import paths
from backend.logging_config import make_print_logger
from backend.rag import rag_documents

print = make_print_logger(__name__)

CALENDAR_FOLDER = "教務處"
_CALENDAR_FILE_RE = re.compile(r"(\d{3})\s*學年度校曆\.pdf$")
_ROW_YEAR_RE = re.compile(r"^(\d{3})\s*年$")
_ROW_MONTH_RE = re.compile(r"^(\d{1,2})\s*月$")
# 一筆記事的開頭：「3日」「1日起」「3日～9日」「3日～9月18日」「14日～116年1月15日」，前面要是開頭或空白，
# 「放假1日」這種句子裡的「1日」不算
_ENTRY_RE = re.compile(
    r"(?:^|(?<=\s))(\d{1,2})日(起)?(?:～(?:(\d{3})年)?(?:(\d{1,2})月)?(\d{1,2})日)?(?=\s)"
)
_NO_CLASS_RE = re.compile(r"放假|補假|停課")
# 校務會議、委員會、教師的截止日：校曆上有，但不是學生要做的事，列一段期間的行事時不放
_STAFF_ONLY_RE = re.compile(r"委員會|校務會議|行政會議|教師繳交")
_KIND_RULES = (
    ("holiday", re.compile(r"放假|補假|停課|寒假開始|暑假開始")),
    ("exam", re.compile(r"期中|期末")),
    ("course", re.compile(r"加退選|初選|選課|停修|寒修|暑修")),
    ("deadline", re.compile(r"截止")),
)


@dataclass(frozen=True)
class CalendarEvent:
    start: date
    end: date
    title: str
    kind: str  # holiday（放假、停課、寒暑假）、exam（期中、期末評量）、course（選課、停修）、deadline（截止）、other

    @property
    def no_class(self) -> bool:
        """這段期間停課（放假、補假、停課）。寒假開始、暑假開始只是一天的事件，學期上課期間另外算。"""
        return bool(_NO_CLASS_RE.search(self.title)) and "不停課" not in self.title

    @property
    def for_students(self) -> bool:
        return not _STAFF_ONLY_RE.search(self.title)

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"], data["end"] = self.start.isoformat(), self.end.isoformat()
        return data


def event_kind(title: str) -> str:
    return next((kind for kind, pattern in _KIND_RULES if pattern.search(title)), "other")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_calendar_text(text: str) -> list[CalendarEvent]:
    """把 rag_documents 解析出來的校曆表格（Markdown 表格）轉成事件，照開始日期排序。"""
    events: list[CalendarEvent] = []
    year = month = None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        year_match = _ROW_YEAR_RE.match(cells[0]) if cells else None
        if not year_match or len(cells) < 3:
            continue  # 標題列、表頭、分隔線
        year = int(year_match.group(1)) + 1911
        month_match = _ROW_MONTH_RE.match(cells[1])
        if month_match:
            month = int(month_match.group(1))
        if month is None:
            continue
        events.extend(_parse_notes(cells[-1], year, month))
    return sorted(events, key=lambda e: (e.start, e.end, e.title))


def _parse_notes(notes: str, year: int, month: int) -> list[CalendarEvent]:
    events = []
    matches = list(_ENTRY_RE.finditer(notes))
    for i, match in enumerate(matches):
        text = notes[match.end(): matches[i + 1].start() if i + 1 < len(matches) else len(notes)]
        day, since, end_year, end_month, end_day = match.groups()
        try:
            start = date(year, month, int(day))
            end = start
            if end_day:
                end_month_value = int(end_month) if end_month else month
                end_year_value = int(end_year) + 1911 if end_year else year + (end_month_value < month)
                end = date(end_year_value, end_month_value, int(end_day))
        except ValueError:
            continue
        # 「；」分隔同一天的好幾件事
        for title in (part.strip(" 　") for part in re.split(r"[；;]", text)):
            if title:
                title = f"{title}（{start.month}/{start.day} 起）" if since else title
                events.append(CalendarEvent(start, max(start, end), title, event_kind(title)))
    return events


def latest_calendar_file(data_dir: Optional[Path] = None) -> Optional[Path]:
    """data/教務處/ 底下學年度最新的〈XXX 學年度校曆.pdf〉。"""
    folder = (data_dir or paths.DATA_DIR) / CALENDAR_FOLDER
    if not folder.is_dir():
        return None
    candidates = [(int(m.group(1)), path) for path in folder.iterdir() if (m := _CALENDAR_FILE_RE.search(path.name))]
    return max(candidates)[1] if candidates else None


_cache: dict = {"key": None, "events": []}


def load_calendar() -> list[CalendarEvent]:
    """讀最新的校曆。檔案沒變就用記憶體裡的結果（解析過的文字本來就有快取）。"""
    path = latest_calendar_file()
    if path is None:
        return []
    key = (str(path), path.stat().st_mtime_ns)
    if _cache["key"] != key:
        parsed = rag_documents.parse_file(path)
        _cache.update(key=key, events=parse_calendar_text(parsed.text) if parsed else [])
        print(f"[校曆] 讀取 {path.name}，共 {len(_cache['events'])} 件事")
    return _cache["events"]


# ============================================================
# 查詢
# ============================================================
# 使用者常講的說法 → 校曆裡的寫法
_HOLIDAY_TERMS = ("放假", "補假", "停課")
_KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "期中考": ("期中",), "期末考": ("期末",), "期中評量": ("期中",), "期末評量": ("期末",),
    "開學": ("開始上課",), "上課": ("開始上課",), "放假": _HOLIDAY_TERMS, "連假": _HOLIDAY_TERMS,
    "停課": _HOLIDAY_TERMS, "退選": ("加退選",), "加選": ("加退選",), "繳費": ("學雜費",), "學費": ("學雜費",),
}


def search_events(events: list[CalendarEvent], keyword: str) -> list[CalendarEvent]:
    """關鍵字比對事件名稱（去掉空白、「期中考」「放假」這類常見說法換成校曆的寫法）。"""
    keyword = re.sub(r"\s", "", keyword)
    if not keyword:
        return []
    terms = {keyword}
    for word, aliases in _KEYWORD_ALIASES.items():
        if word in keyword:
            terms.update(aliases)
    return [e for e in events if any(term in e.title for term in terms)]


def events_between(events: list[CalendarEvent], first: date, last: date) -> list[CalendarEvent]:
    """這段期間「開始」或「結束」的事件。好幾個月的受理期間不會每天都列出來，只在開始跟最後一天出現。
    校務會議這類不是學生要做的事不列（用關鍵字找的時候還是找得到）。"""
    return [e for e in events if e.for_students and (first <= e.start <= last or first <= e.end <= last)]


def no_class_reason(events: list[CalendarEvent], day: date) -> str:
    """這天停課的原因（放假、補假、停課），沒有就回傳空字串。"""
    return "、".join(e.title for e in events if e.no_class and e.covers(day))


def semester_class_period(events: list[CalendarEvent], day: date) -> Optional[tuple[date, date]]:
    """包含這天的學期「上課期間」：從「開始上課」到寒假／暑假開始的前一天。不在上課期間回傳 None。"""
    starts = sorted(e.start for e in events if "開始上課" in e.title)
    breaks = sorted(e.start for e in events if re.search(r"寒假開始|暑假開始", e.title))
    for start in starts:
        end = next((b - timedelta(days=1) for b in breaks if b > start), None)
        if end and start <= day <= end:
            return start, end
    return None


PERIODS = {
    "this_week": "這週", "next_week": "下週", "today": "今天", "tomorrow": "明天",
    "next_7_days": "接下來 7 天", "next_30_days": "接下來 30 天", "this_month": "這個月", "next_month": "下個月",
}


def period_range(period: str, today: Optional[date] = None) -> tuple[date, date]:
    """把「這週」「下週」這類說法換成日期範圍（一週從週一算到週日）。"""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    if period == "today":
        return today, today
    if period == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if period == "this_week":
        return today, monday + timedelta(days=6)
    if period == "next_week":
        return monday + timedelta(days=7), monday + timedelta(days=13)
    if period == "this_month":
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return today, next_month - timedelta(days=1)
    if period == "next_month":
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if period == "next_30_days":
        return today, today + timedelta(days=29)
    return today, today + timedelta(days=6)  # next_7_days 跟其他沒看過的說法


WEEKDAYS = "一二三四五六日"


def format_day(day: date) -> str:
    return f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"


def calendar_source() -> str:
    """校曆檔案在 data/ 底下的相對路徑，前端做成「資料來源」連結（/files/...）。"""
    path = latest_calendar_file()
    return path.relative_to(paths.DATA_DIR).as_posix() if path else ""


def calendar_envelope(
    events: list[CalendarEvent], title: str, today: Optional[date] = None, source: str = "", summary: str = "",
) -> dict:
    """給前端校曆卡片的資料（MessageContent 的 kind === "campus_calendar"）。summary 是卡片最上面的一句話
    （例如查一段期間時，這段期間有沒有放假）。"""
    today = today or date.today()
    return {
        "kind": "campus_calendar",
        "title": title,
        "summary": summary,
        "source": source,
        "today": today.isoformat(),
        "events": [
            {**e.to_dict(), "label": format_day(e.start) if e.start == e.end else f"{format_day(e.start)}～{format_day(e.end)}",
             "past": e.end < today}
            for e in events
        ],
    }
