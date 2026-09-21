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
from typing import Any, Optional

from langchain_core.tools import tool

from action_tools import (
    NCUSession,
    get_schedule,
    search_courses,
    get_deficiency_details,
)
from activity_tools import (
    search_activities,
    get_activity_detail,
    recommend_activities_for_categories,
    find_activities_by_hour_tag,
)
from academic_agent import query_academic_knowledge

NO_CREDENTIALS_MSG = (
    "[Action Agent 回報]:\n缺乏帳號或密碼，無法執行。請先登入 Portal 帳號密碼！"
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


def issue_session_token(username: str) -> str:
    """核發一個新的 session token，不會動到該帳號其他分頁既有的 token。"""
    token = secrets.token_urlsafe(32)
    _session_tokens[token] = username
    _username_tokens.setdefault(username, set()).add(token)
    return token


def resolve_session_token(token: str) -> Optional[str]:
    """把 token 換回 username；token 不存在（沒登入過/已登出/伺服器重啟過）回傳 None。"""
    if not token:
        return None
    return _session_tokens.get(token)


def revoke_session_token(token: str):
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


def _find_activity_id_by_keyword(keyword: str) -> Optional[str]:
    """依關鍵字搜尋活動，回傳最符合的第一筆活動的 activity_id（找不到回傳 None）。"""
    if not keyword:
        return None
    matches = search_activities(keyword=keyword)
    return matches[0]["activity_id"] if matches else None


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
    """

    title = item.get("活動名稱", "（未知活動）")
    session_name = item.get("場次名稱", "")

    header = f"【{title}】"
    if session_name and session_name != title:
        header += f"（{session_name}）"

    lines = [f"- **{header}**"]

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
            keyword: 課程名稱或關鍵字。
        """
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}
            search_data = await search_courses(session, keyword)
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

        lines = [f"**你的活動報名紀錄（共 {len(registrations)} 筆）：**"]
        for item in registrations:
            lines.append("")
            lines.append(_format_my_registration(item))
        return {"content": "\n".join(lines)}

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
            results = search_activities(keyword=keyword)
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
        activity_id = _find_activity_id_by_keyword(keyword)
        if not activity_id:
            return {"content": f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。"}

        try:
            detail = get_activity_detail(activity_id)
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

        recommendations, next_indices, exhausted_by_tag = recommend_activities_for_categories(
            deficiencies, limit_per_tag=5
        )
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
            tag_name: 時數類別/標籤名稱。
        """
        try:
            matches, next_indices, exhausted_by_tag = find_activities_by_hour_tag(
                [tag_name], limit_per_tag=5
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

    async def _preview_activity_action(keyword: str, action_type: str) -> dict:
        try:
            session = await _ensure_session()
            if session is None:
                return {"content": NO_CREDENTIALS_MSG}

            activity_id = _find_activity_id_by_keyword(keyword)
            if not activity_id:
                return {"content": f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。"}

            if action_type == "ACTIVITY_REGISTER":
                dry_run = await session.register_for_activity_session(activity_id, confirm=False)
                action_label = "報名"
            else:
                dry_run = await session.cancel_activity_registration(activity_id, confirm=False)
                action_label = "取消報名"
        except Exception as e:
            await reset_session(username)
            return {"content": f"**Action Agent 回報**：\n系統執行時發生錯誤：{e}"}

        if not dry_run.get("would_click"):
            reason = dry_run.get(
                "reason",
                "目前無法執行這個動作，請確認是否符合報名資格，或有其他限制條件。",
            )
            return {"content": f"**Action Agent 回報**：\n{reason}"}

        detail = get_activity_detail(activity_id)
        envelope = {"kind": "activity_confirmation", "action_label": action_label, **detail}
        return {
            "content": envelope,
            "pending_action": {
                "type": action_type,
                "activity_id": activity_id,
                "session_id": None,
            },
        }

    @tool
    async def preview_activity_registration(keyword: str) -> dict:
        """⚠️ 只會「預覽」報名，絕對不會真的送出報名。

        使用者要求幫忙報名某個「已經指名」的活動時呼叫這個，會回傳活動內容 +
        報名按鈕的預覽，讓使用者確認要不要真的報名。真正送出報名不是由你決定、
        你也沒有辦法直接送出——系統會在使用者「下一則」訊息裡看到明確的「確定」
        才會真的送出，你只需要負責預覽跟提醒使用者確認即可。

        Args:
            keyword: 活動名稱關鍵字。
        """
        return await _preview_activity_action(keyword, "ACTIVITY_REGISTER")

    @tool
    async def preview_activity_cancellation(keyword: str) -> dict:
        """⚠️ 只會「預覽」取消報名，絕對不會真的送出取消。

        使用者要求取消某個「已經指名」活動的報名時呼叫這個。真正送出取消同樣
        需要使用者在下一則訊息裡明確回覆「確定」，不是由你決定、你也沒有辦法
        直接送出。

        Args:
            keyword: 活動名稱關鍵字。
        """
        return await _preview_activity_action(keyword, "ACTIVITY_CANCEL")

    @tool
    async def search_campus_regulations(query: str) -> dict:
        """查詢中央大學的校園法規、辦法、申請規定、費用、期限等「制度規則本身」
        （不是查使用者自己的個人資料）。

        適用：畢業門檻規定、獎學金申請辦法、場地借用費用、選課相關規定等問法規/
        規定內容的問題。不適用於查詢使用者自己的課表/時數進度/報名紀錄，那些請用
        對應的其他工具。

        若問題極度空泛、完全沒有指定任何系所或學制（例如只講「畢業門檻」「必修」），
        不要呼叫這個工具，先直接用文字請使用者補充是哪個系所/學制；但只要對話歷史
        或這句話裡已經出現系所或學制，就算條件足夠，直接呼叫這個工具即可。

        Args:
            query: 使用者的法規問題，可以直接沿用使用者原話。
        """
        try:
            result_dict = query_academic_knowledge(query, history_str)
        except Exception as e:
            return {"content": f"查詢法規時發生錯誤：{e}"}

        answer_text = result_dict.get("answer", "")
        sources = result_dict.get("sources", [])
        return {
            "content": f"**Academic Agent 回報**：\n{answer_text}",
            "sources": sources,
        }

    return [
        get_my_schedule,
        search_course_catalog,
        get_my_hours_dashboard,
        search_campus_activities,
        get_activity_details,
        recommend_activities_for_my_deficiencies,
        find_activities_by_hour_category,
        get_my_registered_activities,
        preview_activity_registration,
        preview_activity_cancellation,
        search_campus_regulations,
    ]
