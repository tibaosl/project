"""LangChain tool（function calling）包裝層。

這裡把 action_tools / activity_tools / academic_agent 裡原本的功能，
包成一個一個帶 docstring/參數說明的 @tool，讓模型自己挑要呼叫哪一個。

日後要新增功能，只要在這個檔案裡新增一個 @tool 函式（寫清楚 docstring：
什麼時候該用、參數是什麼），並加進 build_tools() 回傳的清單就好，
不需要去改任何分類/路由用的 prompt。

⚠️ 帳號密碼一律用 closure 包起來（見 build_tools 內部），不會出現在
任何工具的參數 schema 裡——模型看不到、也不可能透過工具呼叫拿到帳密。

⚠️ 報名/取消報名只有 preview_activity_registration / preview_activity_cancellation
兩個「永遠 dry-run」的工具開放給模型；真正送出（confirm=True）的邏輯完全不
經過這裡、也不開放給模型呼叫，是 supervisor_agent.py 在呼叫 LLM 之前，
用關鍵字判斷使用者是否回覆「確定」來觸發的。
"""

import asyncio
import secrets
import time
from typing import Any, Literal, Optional

from langchain_core.tools import tool

from backend.ncu.action_tools import (
    NCUSession,
    RegistrationUnavailableError,
    get_schedule,
    search_courses,
    get_deficiency_details,
)
from backend.ncu.activity_tools import (
    search_activities,
    get_activity_detail,
    recommend_activities_for_categories,
    find_activities_by_hour_tag,
    choose_session,
    compact_name,
    find_activity_id,
    is_confirmation,
    match_registrations,
    session_matches,
    sort_registrations,
)
from backend.rag.academic_agent import query_academic_knowledge
from backend.analysis.academic_calendar import (
    PERIODS,
    calendar_envelope,
    calendar_source,
    events_between,
    format_day,
    load_calendar,
    period_range,
    search_events,
)
from backend.analysis.academic_tools import analyze_academic_progress, fetch_academic_records
from backend.analysis.agenda import build_agenda
from backend.analysis.scholarship_tools import DeclaredStatus, recommend_scholarships

CalendarPeriod = Literal["today", "tomorrow", "this_week", "next_week", "next_30_days", "this_month", "next_month"]
AgendaPeriod = Literal["today", "tomorrow", "this_week", "next_week", "next_7_days"]


def _period_title(period: str) -> str:
    first, last = period_range(period)
    days = format_day(first) if first == last else f"{format_day(first)}～{format_day(last)}"
    return f"{PERIODS.get(period, '')}（{days}）"

NO_CREDENTIALS_MSG = (
    "[Action Agent 回報]:\n尚未登入或登入已失效，無法執行。請登出後重新用 Portal 登入！"
)

# 背景瀏覽器 session 依「帳號」各自保存一份，而不是整個程式共用一個全域
# session——不然兩個不同使用者（或同一使用者很快點兩次）同時進來，後一個
# request 會直接把前一個人正在用的瀏覽器 session 關掉、換成自己的帳密重登，
# 等於幫別人把登入狀態換掉。
#
# _session_locks 是「每個帳號各自一把鎖」，用來保護「檢查有沒有現成 session、
# 沒有的話建立一個」這段（避免同一帳號的兩個並行 request 同時各開一個瀏覽器）；
# _locks_guard 只是保護 _session_locks 這個 dict 本身不要被並行寫壞，鎖的時間
# 極短（沒有任何 I/O），不會讓不同帳號的登入互相卡住。
_sessions: dict[str, NCUSession] = {}
_session_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_session_lock(username: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _session_locks.get(username)
        if lock is None:
            lock = asyncio.Lock()
            _session_locks[username] = lock
        return lock


async def get_or_create_session(username: str, password: str) -> Optional[NCUSession]:
    """取得（或視需要重新建立）指定帳號已登入的 NCUSession。

    `password` 只有在「這個帳號目前沒有現成 session」時才會用到（拿去建立
    新的 NCUSession、實際登入一次 Portal）；已經有現成 session 時就直接
    重用，不會比對傳進來的密碼對不對——這也是 token 登入流程能成立的原因：
    登入之後的每一輪對話只需要帶 username，不用再夾帶密碼，只要 session
    還在（沒被 reset_session 清掉），`password=""` 一樣拿得到 session。
    """
    if not username:
        return None

    lock = await _get_session_lock(username)
    async with lock:
        session = _sessions.get(username)
        if session is None:
            if not password:
                return None
            session = NCUSession(username, password)
            await session.start()
            _sessions[username] = session
        return session


async def authenticate_and_get_session(username: str, password: str) -> Optional[NCUSession]:
    """真正驗證這組帳密（一定會實際跑一次 Portal 登入），給「使用者正在
    證明自己是誰」的入口用（目前是 main.py 的 /api/login）。

    跟 get_or_create_session() 的關鍵差異：那個是給「呼叫端身分已經確認過」
    的情境用的（例如已經拿到 token、只是要用現成的 session 執行查詢），
    現成 session 存在時刻意不比對密碼，才能讓 token 流程只憑 username
    就重用 session。但 /api/login 收到的帳密是使用者這次自己輸入、還沒
    驗證過的，如果沿用同一個「有現成 session 就跳過密碼檢查」的邏輯，等於
    只要某帳號之前有人登入過、留著現成 session，任何人拿那個帳號＋隨便一組
    密碼呼叫 /api/login 都能換到一個有效 token——這裡一律真的重新跑一次
    登入來驗證，不管現成 session 存不存在，帳密錯就是會失敗。
    """
    if not username or not password:
        return None

    lock = await _get_session_lock(username)
    async with lock:
        old_session = _sessions.pop(username, None)
        if old_session is not None:
            await old_session.close()

        session = NCUSession(username, password)
        await session.start()
        _sessions[username] = session
        return session


async def adopt_session(username: str, session: NCUSession):
    """登記一個「身分已經由官方 OAuth 驗證過」、已經登入好的 session（Chrome 登入流程用）。"""
    lock = await _get_session_lock(username)
    async with lock:
        old_session = _sessions.pop(username, None)
        if old_session is not None:
            await old_session.close()
        _sessions[username] = session


async def reset_session(username: str):
    """該帳號的登入類操作出錯時關閉並清掉它的 session，下次呼叫會重新登入。"""
    lock = await _get_session_lock(username)
    async with lock:
        session = _sessions.pop(username, None)
    if session is not None:
        await session.close()


# ----------------------------------------------------------------------------
# Session token：登入成功後發一個不透明 token 給前端，取代「每次對話都夾帶
# 明文密碼」。純記憶體儲存，伺服器重啟就會全部失效（使用者只是要重新登入
# 一次，不是資料遺失）。
#
# 同一個帳號可以同時持有多組有效 token（例如同一個使用者開兩個分頁各自
# 登入一次），彼此不會互相頂掉——所有 token 反正都指向同一個共用的
# NCUSession（見上面 _sessions），讓其中一個分頁失效並不會讓 Playwright
# session 變得更安全，只會讓另一個分頁的使用者莫名其妙被登出、一頭霧水。
# `_username_tokens` 是反向索引（username -> 該帳號目前所有有效 token），
# 用來讓「登出時只清掉自己這個 token，且只有真的是最後一個 token 時才
# 順便關掉共用的背景瀏覽器」這件事是 O(1) 查表，不用整個 _session_tokens
# 掃一遍。
#
# ⚠️ 沿用上面 get_or_create_session() 既有的行為：只要 username 對得上
# 現成的 _sessions cache，就不會再比對密碼——token 只是把「不再重複傳密碼」
# 這件事往前挪到登入當下一次性驗證，不是額外新增的信任假設。
# ----------------------------------------------------------------------------
_session_tokens: dict[str, str] = {}
_username_tokens: dict[str, set[str]] = {}
# token 最後一次使用的時間（time.monotonic）。閒置太久的 token 失效，前端會請使用者重新登入：
# 忘了登出、電腦借給別人用的時候，別人不能一直用你的身分查成績、報名活動。
_token_last_used: dict[str, float] = {}
TOKEN_IDLE_SECONDS = 12 * 60 * 60


def issue_session_token(username: str) -> str:
    """核發一個新的 session token，不會動到該帳號其他分頁既有的 token。"""
    token = secrets.token_urlsafe(32)
    _session_tokens[token] = username
    _username_tokens.setdefault(username, set()).add(token)
    _token_last_used[token] = time.monotonic()
    return token


def resolve_session_token(token: str) -> Optional[str]:
    """把 token 換回 username；token 不存在（沒登入過/已登出/伺服器重啟過）或閒置太久回傳 None。"""
    if not token or token not in _session_tokens:
        return None
    now = time.monotonic()
    if now - _token_last_used.get(token, now) > TOKEN_IDLE_SECONDS:
        revoke_session_token(token)
        return None
    _token_last_used[token] = now
    return _session_tokens[token]


def revoke_session_token(token: str):
    _token_last_used.pop(token, None)
    username = _session_tokens.pop(token, None)
    if username is None:
        return
    tokens = _username_tokens.get(username)
    if tokens is not None:
        tokens.discard(token)
        if not tokens:
            _username_tokens.pop(username, None)


def has_active_tokens(username: str) -> bool:
    """這個帳號目前是否還有其他分頁/裝置持有的有效 token——給登出流程
    判斷「可不可以順便關掉共用的背景瀏覽器」用，見 main.py 的 /api/logout。
    """
    return bool(_username_tokens.get(username))


class ActivityLookupError(Exception):
    """查公開的活動資料失敗（例如學校網站一時連不上），跟登入狀態無關，不用清掉 session。"""


async def _lookup_activity(func, *args):
    """在背景 thread 跑 activity_tools 的同步查詢，失敗時包成 ActivityLookupError。"""
    try:
        return await asyncio.to_thread(func, *args)
    except Exception as e:
        raise ActivityLookupError(e) from e


def _format_my_registration(item: dict[str, str]) -> str:
    """把 NCUSession.get_my_activity_registrations() 抓回來的一列報名紀錄
    整理成好讀的文字。

    欄位名稱（活動名稱/場次名稱/活動地點/活動場次時間/時數標籤/報名序號/
    報名狀態/所屬角色/簽到簽退/前測問卷後測問卷/心得與反思/功能）是 2026-09-21
    使用者拿真實帳號實際測過、貼真實截圖比對確認的，不是用猜的——但底層的
    NCUSession.get_my_activity_registrations() 仍然是「表頭當 key」的通用
    解析，沒有寫死這些欄位一定要存在，所以這裡全部用 .get()，就算校方
    之後調整了頁面欄位、對不上了，也只是那個欄位不顯示，不會整支壞掉。

    報名序號、所屬角色這兩欄刻意不顯示——前者大部分是「手動報名」或內部
    流水號，後者幾乎固定是「一般參加者」，對使用者判斷有沒有報名/該做什麼
    沒有實質幫助，全部列出來只會讓人更難找到真正有用的資訊。前測/後測問卷、
    心得與反思這兩欄只在「不是無需填寫」時才顯示，避免每一筆都印一樣的
    「無需填寫」造成雜訊，但真的需要填寫時要讓使用者看得到。

    ⚠️ 每個欄位都用 Markdown 的項目符號（"- "）開頭、獨立一行，不是單純用
    換行符號分段——前端是用 react-markdown 原樣渲染這段文字（見
    MessageContent.jsx），沒有加 remark-breaks 這類外掛，純 CommonMark 規則
    下同一段落裡「單一個」換行符號會被當成空白直接接在一起，導致原本排好
    的多行版面在畫面上擠成一整條看不出斷行的文字（第一版真的這樣被使用者
    抓到過）。項目符號清單不受這條規則影響，每個 "- " 開頭的行一定會各自
    斷行，才是這裡故意這樣寫的原因，不要改回單純縮排的純文字。

    活動標題改用 Markdown 三級標題（"### "），不是清單項目——標題是獨立的
    區塊層級元素，CommonMark 會自動把它跟前後的清單切開，瀏覽器預設的
    標題樣式（字級變大、有自己的上下留白）剛好同時達成「標題要跟內文有
    大小區別」跟「不同活動之間要有間隔」這兩個使用者要求的效果，不需要
    额外寫 CSS。呼叫端（get_my_registered_activities）組多筆結果時，每筆
    之間還是要留一個空行，讓上一筆的清單跟下一筆的標題確實斷成兩個區塊，
    不然某些 Markdown 剖析器碰到清單後緊接標題可能會誤判成清單的延伸內容。
    """

    title = item.get("活動名稱", "（未知活動）")
    session_name = item.get("場次名稱", "")

    header = f"【{title}】"
    if session_name and session_name != title:
        header += f"（{session_name}）"

    lines = [f"### {header}"]

    status = item.get("報名狀態")
    if status:
        lines.append(f"- 報名狀態：{status}")

    event_time = item.get("活動場次時間")
    if event_time:
        lines.append(f"- 時間：{event_time}")

    location = item.get("活動地點")
    if location:
        lines.append(f"- 地點：{location}")

    hours_tag = item.get("時數標籤")
    if hours_tag and "不提供時數" not in hours_tag:
        lines.append(f"- 時數標籤：{hours_tag}")

    checkin = item.get("簽到/簽退")
    if checkin:
        lines.append(f"- 簽到/簽退：{checkin}")

    for field in ("前測問卷/後測問卷", "心得與反思"):
        value = item.get(field, "")
        if value and "無需填寫" not in value:
            lines.append(f"- {field}：{value}")

    if "取消報名" in item.get("功能", ""):
        lines.append("- （目前可以線上取消這個場次的報名）")

    return "\n".join(lines)


def build_tools(username: str, password: str, history_str: str = "無"):
    """組出這一輪對話可以用的完整工具清單。

    每次對話都重新呼叫一次（而不是在模組層級建立一份固定工具），
    是為了把這一輪的帳密、對話歷史用 closure 包進工具裡——這些不是
    模型該知道、也不是模型該自己填的參數。
    """

    async def _ensure_session() -> Optional[NCUSession]:
        return await get_or_create_session(username, password)

    @tool
    async def get_my_schedule() -> dict:
        """查詢使用者「自己」本學期的個人課表（需要先在側邊欄輸入帳密登入 Portal）。

        使用時機：使用者要看自己的課表、這學期修了哪些課、上課時間地點。
        不需要任何參數。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            schedule_data = await get_schedule(session)
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        if schedule_data is not None:
            return {"content": schedule_data}
        return {"content": "**Action Agent 回報**：\n執行失敗：無法解析課表或查無資料"}

    @tool
    async def search_course_catalog(keyword: str) -> dict:
        """在選課系統依關鍵字搜尋課程（需要登入）。

        使用時機：使用者明確講出課程名稱或關鍵字，要求查詢/加選課程
        （例如「幫我選日文課」「找微積分」）。如果使用者只是很籠統地說
        「我要選課」「幫我加選」而完全沒提到任何課程名稱或關鍵字，
        不要呼叫這個工具，直接用文字請他提供想找的課程名稱或關鍵字。

        Args:
            keyword: 課程名稱關鍵字，只放課名本身，不要加「課」「這門課」這類字
                （例如「幫我找日文課」傳「日文」）——選課系統是用課名包含關鍵字來比對的。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            search_data = await search_courses(session, keyword)
        except RegistrationUnavailableError as e:
            # 只是選課系統進不去，Portal 登入還好好的，不要把整個 session 清掉害使用者被登出
            return {"content": f"**Action Agent 回報**：\n{e}"}
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        if not search_data:
            return {"content": f"**Action Agent 回報**：\n找不到關鍵字為「{keyword}」的課程。"}

        result_text = f"**「{keyword}」搜尋結果：**\n\n"
        for item in search_data:
            result_text += f"- {item['serial']} | {item['course_no']} | **{item['title']}** | {item['teacher']}\n"
        return {"content": result_text}

    @tool
    async def get_my_hours_dashboard() -> dict:
        """查詢使用者「自己」的學習護照時數進度、各類別是否已達畢業門檻（需要登入）。

        使用時機：使用者問自己的時數進度、還差多少小時、有沒有達到畢業門檻。
        不適用於「時數規定/門檻本身是幾小時」這種制度規則問題——那種請改用
        search_campus_regulations。不需要任何參數。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            dashboard_data = await session.get_hours_dashboard()
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        envelope = {
            "kind": "hours_dashboard",
            "graduated": dashboard_data["graduated"],
            "categories": dashboard_data["categories"],
        }
        return {"content": envelope}

    @tool
    async def get_my_academic_analysis(
        focus: Literal["credits", "grades", "overview"] = "overview",
    ) -> dict:
        """分析使用者「自己」的學業狀況（需要登入）。

        使用時機：使用者問自己的學分夠不夠畢業、還差幾學分、必修修完了沒、
        成績/平均/排名、有沒有被當或需要重修的課。
        不適用於學習護照「時數」進度（請用 get_my_hours_dashboard），也不適用於
        「畢業學分規定本身是多少」這種制度問題（請用 search_campus_regulations）。

        Args:
            focus: 只回答使用者問的那一塊，不要附上沒問到的內容。
                - "credits"：學分／畢業相關（還差幾學分、各畢業類別缺什麼、必修修完沒）。
                - "grades"：成績相關（學期平均、成績趨勢、班/系排名、累計排名、被當或停修的課）。
                - "overview"：使用者要「整體學業分析」或同時問到學分跟成績時才用。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            transcript, graduation = await fetch_academic_records(
                session, include_graduation=focus != "grades"
            )
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        return {"content": analyze_academic_progress(transcript, graduation, focus)}

    @tool
    async def recommend_scholarships_for_me(statuses: Optional[list[DeclaredStatus]] = None) -> dict:
        """依使用者「自己」的學制、年級、系所、學業成績與排名，找出他可能可以申請的獎學金、助學金、
        獎勵金（需要登入）。

        使用時機：使用者想知道「自己」能申請哪些獎學金，例如「我可以申請哪些獎學金」「有什麼獎學金
        適合我」「我的成績拿得到獎學金嗎」「我是低收入戶，有什麼助學金可以申請」。
        不適用於問某個獎學金本身的規定（金額、資格、怎麼申請，例如「書卷獎可以拿多少錢」「羅家倫
        獎學金要交什麼資料」），那種請用 search_campus_regulations。

        Args:
            statuses: 使用者在對話裡「自己說過」的身分，用來判斷要這些身分才能申請的獎學金（例如
                「我是低收入戶」「我家清寒」→ 經濟弱勢，「我是原住民」→ 原住民，「我是僑生」→ 僑生）。
                使用者沒說的不要猜，留空。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            transcript, _ = await fetch_academic_records(session, include_graduation=False)
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        try:
            # 要載入法規文件目錄（有新文件時還要呼叫模型整理獎學金資格），丟到背景 thread 跑，
            # 不然這段時間整個 FastAPI event loop 都會被卡住
            envelope = await asyncio.to_thread(recommend_scholarships, transcript, statuses or [])
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n整理獎學金資料時發生錯誤：{e}"}
        return {"content": envelope}

    @tool
    async def get_my_registered_activities() -> dict:
        """查詢使用者自己已經報名過的活動清單（需要登入）。

        使用時機：使用者問「我報名了什麼活動」「我有報名過什麼」「查一下我的
        報名紀錄」這類想看自己報名狀況的問題。跟 search_campus_activities／
        recommend_activities_for_my_deficiencies 的差別：這個查的是「已經報名
        過」的紀錄，不是查有什麼活動可以報名。不需要任何參數。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            data = await session.get_my_activity_registrations()
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        registrations = data.get("registrations", [])
        if not registrations:
            return {"content": "**Action Agent 回報**：\n目前沒有查到任何活動報名紀錄。"}

        upcoming, past = sort_registrations(registrations)
        lines = [f"**你的活動報名紀錄（共 {len(registrations)} 筆）：**"]
        for heading, items in ((f"接下來的活動（{len(upcoming)} 筆）", upcoming), (f"已經結束的活動（{len(past)} 筆）", past)):
            if not items:
                continue
            if upcoming and past:
                lines += ["", f"## {heading}"]
            for item in items:
                lines.append("")
                lines.append(_format_my_registration(item))
        return {"content": "\n".join(lines)}

    @tool
    async def get_campus_calendar(keyword: str = "", period: Optional[CalendarPeriod] = None) -> dict:
        """查詢中央大學的校曆（學校行事曆）上的日期：開始上課、加退選、停修、期中期末評量、放假補假、
        寒暑假、畢業典禮、校慶、各種申請截止日（不需要登入）。

        使用時機：使用者問學校某件事「什麼時候」「到哪一天」，或某段期間學校有什麼行事，例如
        「期中考是什麼時候」「什麼時候放寒假」「下週有放假嗎」「加退選到哪一天」「這個月有什麼要注意的日期」。
        不適用於規定的內容本身（例如停修有什麼限制、加退選要怎麼操作，請用 search_campus_regulations），
        也不適用於使用者自己的課表、活動行程（請用 get_my_agenda）。

        Args:
            keyword: 要找的事件，例如「期中」「寒假」「加退選」「停修」「畢業典禮」「放假」。只給 keyword
                會列出這學年所有符合的日期。問一段期間有什麼事（「下週有什麼」）就留空，只給 period。
            period: 要看的期間：today、tomorrow、this_week（今天到週日）、next_week（下週一到週日）、
                next_30_days、this_month、next_month。跟 keyword 一起給時，只找這段期間裡符合的事件
                （例如「下週有放假嗎」給 keyword「放假」、period「next_week」）。兩個都沒給就是接下來 30 天。
        """
        try:
            events = await asyncio.to_thread(load_calendar)
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n讀取校曆時發生錯誤：{e}"}
        if not events:
            return {"content": "**Action Agent 回報**：\n目前沒有收錄校曆，查不到學校行事曆上的日期。"}

        keyword = keyword.strip()
        if keyword and not period:
            matched = search_events(events, keyword)
            if not matched:
                return {"content": f"**Action Agent 回報**：\n這學年的校曆裡找不到跟「{keyword}」有關的日期。"}
            return {"content": calendar_envelope(matched, f"校曆：{keyword}", source=calendar_source())}

        period = period or "next_30_days"
        first, last = period_range(period)
        title = _period_title(period)
        in_range = [e for e in events if e.start <= last and e.end >= first]
        if keyword:
            matched = search_events(in_range, keyword)
            if not matched:
                return {"content": f"**校曆**：{title}沒有跟「{keyword}」有關的日期。"}
            return {"content": calendar_envelope(matched, f"校曆：{title}的「{keyword}」", source=calendar_source())}
        days_off = [e for e in in_range if e.no_class]
        summary = (
            f"這段期間放假、停課：{'、'.join(f'{format_day(e.start)} {e.title}' for e in days_off)}"
            if days_off else "這段期間沒有放假或停課。"
        )
        return {"content": calendar_envelope(events_between(events, first, last), f"校曆：{title}",
                                             source=calendar_source(), summary=summary)}

    @tool
    async def get_my_agenda(period: AgendaPeriod = "this_week") -> dict:
        """整理使用者「自己」這段期間每天的行程（需要登入）：要上的課（放假、停課的日子會跳過並標出原因）、
        已經報名的活動場次，以及校曆上的重要日期（截止日、考試、放假）。

        使用時機：使用者問自己某段時間有什麼事，例如「我這週有什麼行程」「明天要上什麼課」「今天有課嗎」
        「下週有哪些事要注意」。只想看整學期固定的課表請用 get_my_schedule，只想看活動報名紀錄請用
        get_my_registered_activities，問學校的行事曆日期（不是自己的行程）請用 get_campus_calendar。

        Args:
            period: today、tomorrow、this_week（今天到這週日）、next_week（下週一到週日）、next_7_days。
        """
        session = await _ensure_session()
        if session is None:
            return {"content": NO_CREDENTIALS_MSG}

        schedule = registrations = None
        try:
            schedule = await get_schedule(session)
        except Exception as e:
            print(f"[Agent] 我的行程：課表抓取失敗：{e}")
        try:
            registrations = (await session.get_my_activity_registrations()).get("registrations", [])
        except Exception as e:
            print(f"[Agent] 我的行程：報名紀錄抓取失敗：{e}")
        if schedule is None and registrations is None:
            await reset_session(username)
            return {"content": "**Action Agent 回報**：\n課表跟活動報名紀錄都抓不到，可能是登入狀態失效了，請重新登入再試一次。"}

        try:
            events = await asyncio.to_thread(load_calendar)
        except Exception as e:
            print(f"[Agent] 我的行程：讀取校曆失敗：{e}")
            events = []
        first, last = period_range(period)
        return {"content": build_agenda(first, last, events, schedule, registrations, f"我的行程：{_period_title(period)}")}

    @tool
    async def search_campus_activities(keyword: str) -> dict:
        """單純查詢/瀏覽校內活動列表（不需要登入）。

        使用時機：使用者自己講出明確的活動名稱關鍵字或一般類別，單純要看活動列表
        （例如「查一下有沒有日文相關的活動」「最近有什麼藝文活動」），沒有要系統
        依他個人狀況判斷該報名什麼。

        跟另外兩個活動查詢工具的差別：
        - 使用者完全沒指定活動名稱/類別，要系統依他自己的時數缺口主動推薦
          → 用 recommend_activities_for_my_deficiencies，不要用這個。
        - 使用者指名一個具體的「學習護照時數標籤/類別」（例如「自我探索與生涯規劃」
          「校外服務」「人文藝術」「國際視野」），問有沒有活動提供這種時數
          → 用 find_activities_by_hour_category，不要用這個。

        Args:
            keyword: 活動名稱關鍵字或一般類別。
        """
        try:
            # activity_tools 用同步的 requests 打學校網站，一律丟到背景 thread 跑，
            # 不然查詢的這段時間整個後端（包括其他人的請求、前端的連線檢查）都會卡住
            results = await asyncio.to_thread(search_activities, keyword=keyword)
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n查詢活動時發生錯誤：{e}"}

        if not results:
            return {"content": f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動。"}

        lines = [f"**「{keyword}」活動搜尋結果（共 {len(results)} 筆）：**\n"]
        for item in results[:10]:
            lines.append(f"- [{item['activity_id']}] {item['title']}（{item['status']}）")
        return {"content": "\n".join(lines)}

    @tool
    async def get_activity_details(keyword: str) -> dict:
        """查看某個「已經指名」的活動的詳細資訊/內容/場次（不需要登入）。

        Args:
            keyword: 活動名稱關鍵字（使用者通常只會講活動名稱的一部分，不需要活動編號，
                系統會自動搜尋比對最接近的活動）。
        """
        try:
            activity_id = await asyncio.to_thread(find_activity_id, keyword)
            if not activity_id:
                return {"content": f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。"}
            detail = await asyncio.to_thread(get_activity_detail, activity_id)
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n查詢活動詳情時發生錯誤：{e}"}

        return {"content": {"kind": "activity_detail", **detail}}

    @tool
    async def recommend_activities_for_my_deficiencies() -> dict:
        """依使用者「自己」目前的學習護照時數缺口，從開放報名中的活動找出適合報名的場次（需要登入）。

        使用時機：使用者沒有指定任何活動名稱或時數類別，而是要系統依他自己的狀況
        （時數缺口、還差什麼）主動推薦，例如「有沒有什麼活動可以推薦我報名的」
        「有什麼活動適合我」「我還缺時數，有什麼可以參加的」。不需要任何參數。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            dashboard_data = await session.get_hours_dashboard()
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        deficiencies = get_deficiency_details(dashboard_data)
        if not deficiencies:
            return {"content": "**Action Agent 回報**：\n你的學習護照時數已經全部達標了，沒有需要補的細項！"}

        try:
            recommendations, next_indices, exhausted_by_tag = await asyncio.to_thread(
                recommend_activities_for_categories, deficiencies, limit_per_tag=5
            )
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n查詢活動時發生錯誤：{e}"}
        has_more = not all(exhausted_by_tag.values())

        envelope = {
            "kind": "activity_recommendations",
            "deficiencies": deficiencies,
            "recommendations": recommendations,
            "exhausted_by_tag": exhausted_by_tag,
            "has_more": has_more,
        }
        result: dict[str, Any] = {"content": envelope}
        if has_more:
            result["pending_action"] = {
                "type": "ACTIVITY_RECOMMEND",
                "deficiencies": deficiencies,
                "next_indices": next_indices,
            }
        return result

    # tag_name 說明裡的類別清單要跟 activity_tools.STUDY_PASSPORT_TAG_FILTER_MAP 一致：
    # 傳完整名稱才能用伺服器端篩選，傳簡稱會退回逐一掃描全部活動，慢很多。
    @tool
    async def find_activities_by_hour_category(tag_name: str) -> dict:
        """依「學習護照時數標籤/類別」名稱直接查詢有提供該類時數的活動。

        不需要登入、也不需要知道使用者自己的時數狀況——是使用者自己指名想找哪個
        類別（跟 recommend_activities_for_my_deficiencies 的差別：那個是系統依
        個人缺口主動推薦，這個是使用者自己講出類別名稱）。

        使用時機：使用者自己講出一個具體的時數類別/標籤名稱（例如「自我探索與生涯規劃」
        「校外服務」「人文藝術」「國際視野」），問有沒有活動提供這種時數，
        例如「有沒有自我探索與生涯規劃時數的活動」「哪些活動有校外服務時數」。

        Args:
            tag_name: 時數類別的完整名稱，是下面其中一個：自我探索與生涯規劃、其他生活知能、
                服務學習課程、校外服務、人文藝術、國際視野、大一週會、院週會、大一CPR。
                使用者講簡稱時換成完整名稱（例如「生涯規劃」→「自我探索與生涯規劃」）。
        """
        try:
            matches, next_indices, exhausted_by_tag = await asyncio.to_thread(
                find_activities_by_hour_tag, [tag_name], limit_per_tag=5
            )
        except Exception as e:
            return {"content": f"**Action Agent 回報**：\n查詢活動時發生錯誤：{e}"}

        has_more = not all(exhausted_by_tag.values())
        envelope = {
            "kind": "activity_tag_search",
            "recommendations": matches,
            "exhausted_by_tag": exhausted_by_tag,
            "has_more": has_more,
        }
        result: dict[str, Any] = {"content": envelope}
        if has_more:
            result["pending_action"] = {
                "type": "ACTIVITY_SEARCH_BY_TAG",
                "tag_names": [tag_name],
                "next_indices": next_indices,
            }
        return result

    def _report(message: str) -> dict:
        return {"content": f"**Action Agent 回報**：\n{message}"}

    def _ask_which_session(question: str, options: list[str], note: str = "") -> dict:
        """選不出是哪一個場次時列出候選請使用者選。不給 pending_action，所以不可能送出任何東西。"""
        lines = [note, ""] if note else []
        lines += [f"- {option}" for option in options]
        lines += ["", f"{question}跟我說場次名稱或日期就可以。"]
        return {"content": "\n".join(lines)}

    def _session_option(name: str, period: str) -> str:
        return f"{name}（{period}）" if period else name

    async def _find_session_to_register(keyword: str, hint: str):
        """回傳 (活動編號, 活動詳情, 場次)，選不出來時回傳要給使用者看的回覆。"""
        activity_id = await _lookup_activity(find_activity_id, keyword)
        if not activity_id:
            return _report(f"找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。")
        detail = await _lookup_activity(get_activity_detail, activity_id)
        sessions = detail.get("sessions", [])
        if not sessions:
            return _report(f"「{detail.get('title', keyword)}」的活動頁上沒有場次資料，請直接到活動頁面確認。")

        chosen, candidates = choose_session(sessions, hint)
        if chosen is not None:
            return activity_id, detail, chosen
        title = detail.get("title", keyword)
        hint_missed = hint.strip() and not any(
            session_matches(s.get("session_name", ""), s.get("event_period", ""), hint) for s in candidates
        )
        note = f"「{title}」沒有對得上「{hint}」的場次，這個活動有這些場次：" if hint_missed else f"「{title}」有 {len(candidates)} 個場次可以選："
        return _ask_which_session(
            f"你要報名「{title}」的哪一場？",
            [_session_option(s.get("session_name", ""), s.get("event_period", "")) for s in candidates],
            note,
        )

    async def _find_session_to_cancel(session: NCUSession, keyword: str, hint: str):
        """從使用者自己的報名紀錄找要取消的場次，回傳 (活動編號, 活動詳情, 場次) 或要給使用者看的回覆。"""
        data = await session.get_my_activity_registrations()
        records = match_registrations(data.get("registrations", []), keyword, hint)
        described = f"「{keyword}」" + (f"（{hint}）" if hint.strip() else "")
        if not records:
            return _report(f"你的報名紀錄裡找不到{described}。可以先問我「我報名了哪些活動？」，再用紀錄上的活動或場次名稱跟我說。")

        cancellable = [r for r in records if "取消報名" in r.get("功能", "")]
        if not cancellable:
            names = "、".join(f"「{r.get('場次名稱') or r.get('活動名稱')}」" for r in records[:3])
            return _report(f"{names}目前沒辦法線上取消（可能已經超過可以自己取消的時間，或活動已經結束），要取消的話請聯絡承辦單位。")
        if len(cancellable) > 1:
            return _ask_which_session(
                f"你要取消{described}的哪一場？",
                [_session_option(f"{r.get('活動名稱', '')}：{r.get('場次名稱', '')}", r.get("活動場次時間", "")) for r in cancellable],
                f"你的報名紀錄裡有 {len(cancellable)} 筆{described}可以取消：",
            )

        record = cancellable[0]
        # 報名紀錄頁上沒有活動編號（2026-10 實測「檢視活動資訊」的連結裡也沒有），用活動名稱找
        activity_id = await _lookup_activity(find_activity_id, record.get("活動名稱", ""))
        if not activity_id:
            return _report(f"找不到「{record.get('活動名稱', keyword)}」的活動頁面，請直接到 iNCU 的報名紀錄取消。")
        detail = await _lookup_activity(get_activity_detail, activity_id)
        sessions = detail.get("sessions", [])
        wanted = compact_name(record.get("場次名稱", ""))
        target = next((s for s in sessions if compact_name(s.get("session_name", "")) == wanted), None)
        if target is None and len(sessions) == 1:
            target = sessions[0]
        if target is None:
            return _report(f"在活動頁上找不到「{record.get('場次名稱', '')}」這個場次，請直接到 iNCU 的報名紀錄取消。")
        return activity_id, detail, target

    async def _preview_activity_action(keyword: str, hint: str, action_type: str) -> dict:
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}

            if action_type == "ACTIVITY_REGISTER":
                found = await _find_session_to_register(keyword, hint)
            else:
                found = await _find_session_to_cancel(session, keyword, hint)
            if isinstance(found, dict):
                return found
            activity_id, detail, target = found

            # 活動常常有好幾個場次，預覽跟之後真的送出都要指定場次，不然會變成第一個場次
            session_id = target.get("session_id") or None
            if action_type == "ACTIVITY_REGISTER":
                dry_run = await session.register_for_activity_session(activity_id, session_id=session_id, confirm=False)
                action_label = "報名"
            else:
                dry_run = await session.cancel_activity_registration(activity_id, session_id=session_id, confirm=False)
                action_label = "取消報名"
        except ActivityLookupError as e:
            # 查公開活動資料失敗跟登入無關，不要把 session 清掉（清掉的話使用者要重新登入）
            return _report(f"查詢活動資料時發生錯誤：{e}")
        except Exception as e:
            await reset_session(username)
            return _report(f"系統執行時發生錯誤：{e}")

        if not dry_run.get("would_click"):
            return _report(dry_run.get("reason", "目前無法執行這個動作，請確認是否符合報名資格，或有其他限制條件。"))

        envelope = {
            "kind": "activity_confirmation",
            "action_label": action_label,
            **detail,
            # 確認卡片只放要報名/取消的那個場次，使用者才知道送出的是哪一場
            "sessions": [target],
            "session_name": target.get("session_name", ""),
        }
        return {
            "content": envelope,
            "pending_action": {"type": action_type, "activity_id": activity_id, "session_id": session_id},
        }

    @tool
    async def preview_activity_registration(keyword: str, session: str = "") -> dict:
        """⚠️ 只會「預覽」報名，絕對不會真的送出報名。

        使用者要求幫忙報名某個「已經指名」的活動時呼叫這個，會回傳那個場次的內容 +
        報名按鈕的預覽，讓使用者確認要不要真的報名。真正送出報名不是由你決定、
        你也沒有辦法直接送出——系統會在使用者「下一則」訊息裡看到明確的「確定」
        才會真的送出，你只需要負責預覽跟提醒使用者確認即可。
        活動有好幾個場次、使用者又沒說是哪一場時，這個工具會列出場次請使用者選，
        使用者回答之後再呼叫一次，把他選的場次放在 session。

        Args:
            keyword: 活動名稱關鍵字。
            session: 使用者指定的場次名稱關鍵字或日期（例如「藍海策略」「11/17」），沒指定就留空。
        """
        return await _preview_activity_action(keyword, session, "ACTIVITY_REGISTER")

    @tool
    async def preview_activity_cancellation(keyword: str, session: str = "") -> dict:
        """⚠️ 只會「預覽」取消報名，絕對不會真的送出取消。

        使用者要求取消某個「已經指名」活動的報名時呼叫這個，會從使用者自己的報名紀錄
        找出那一場。真正送出取消同樣需要使用者在下一則訊息裡明確回覆「確定」，不是由你
        決定、你也沒有辦法直接送出。

        Args:
            keyword: 活動名稱或場次名稱的關鍵字（例如「出國講座」「客家學院學生出國說明會」）。
            session: 同一個活動報名了好幾場時，使用者指定的場次名稱關鍵字或日期，沒指定就留空。
        """
        return await _preview_activity_action(keyword, session, "ACTIVITY_CANCEL")

    @tool
    async def search_campus_regulations(query: str) -> dict:
        """查詢中央大學的校園法規、辦法、申請流程、表單、費用、期限、門檻等「規定本身」
        （不是查使用者自己的個人資料）。

        適用：學生證遺失、在學證明、成績單申請、教室借用、外文畢業門檻、學雜費、
        選課與停修規則、轉系、獎助學金的規定（金額、資格、怎麼申請）、宿舍、學生請假、
        校曆日期（加退選、畢業典禮）、各種申請表要交給誰等問題。不適用於查詢使用者自己的
        課表/時數進度/報名紀錄，也不適用於要系統依使用者自己的成績、身分找出他能申請的
        獎學金（請用 recommend_scholarships_for_me），那些請用對應的其他工具。

        沒指定系所或學制也可以直接呼叫，這個工具會依文件內容判斷要不要請使用者補充。

        Args:
            query: 使用者的問題原話，保留使用者提到的身分、系所、學制、年度等條件，不要刪減。
        """
        try:
            # query_academic_knowledge 是同步呼叫（要等 OpenAI 回應），丟到背景
            # thread 跑，不然這段時間整個 FastAPI event loop 都會被卡住。
            result_dict = await asyncio.to_thread(query_academic_knowledge, query, history_str)
        except Exception as e:
            return {"content": f"查詢法規時發生錯誤：{e}"}

        return {
            "content": result_dict.get("answer", ""),
            "sources": result_dict.get("sources", []),
        }

    return [
        get_my_schedule,
        search_course_catalog,
        get_my_hours_dashboard,
        get_my_academic_analysis,
        recommend_scholarships_for_me,
        search_campus_activities,
        get_activity_details,
        recommend_activities_for_my_deficiencies,
        find_activities_by_hour_category,
        get_my_registered_activities,
        get_my_agenda,
        get_campus_calendar,
        preview_activity_registration,
        preview_activity_cancellation,
        search_campus_regulations,
    ]
