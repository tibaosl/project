"""開場畫面「最近要注意」要用的 iNCU 資料（活動報名紀錄、時數紀錄、成績單比對出的獎學金），登入後在背景先抓好。
整理成卡片的邏輯在 backend/analysis/reminders.py。

iNCU 抓一輪大約 10 秒，開場畫面不能等：
- 登入成功後呼叫 start（main.py，接在 agent_tools.start_prewarm 後面），先等預熱把 iNCU 的授權同意頁
  走完再開始抓。
- 用自己的 iNCU 分頁（_side_view）。工具用的是 session.incu_page，兩邊各用各的分頁，背景還在抓的時候
  使用者問問題不用等，兩邊也不會打斷對方換頁。
- 不處理重新登入：登入狀態失效時不跳 Chrome 視窗（使用者沒有要登入），那一部分記成沒抓到，
  使用者自己問問題時工具會照常處理。背景準備失敗也不清掉 session（不會害使用者被登出）。
- 每一部分抓完就先存起來，開場畫面可以先顯示抓到的部分。結果只放在記憶體、登出時清掉，
  超過 REFRESH_SECONDS 再打開開場畫面時在背景重抓，重抓的這段時間先給舊的。

獎學金用的是 scholarship_tools.get_scholarship_index（每週更新文件時就整理好，這裡只比對、不呼叫模型，
只有剛好有還沒整理過的新文件時會跟「推薦獎學金」工具一樣先整理）。
"""

import asyncio
import copy
import time
from typing import Any, Optional

from backend.agent import agent_tools
from backend.analysis.academic_tools import fetch_academic_records
from backend.analysis.reminders import PART_LABELS, PARTS
from backend.analysis.scholarship_tools import build_profile, get_scholarship_index, match_scholarships
from backend.logging_config import make_print_logger

print = make_print_logger(__name__)

STEP_TIMEOUT_SECONDS = 60
REFRESH_SECONDS = 30 * 60
RETRY_FAILED_SECONDS = 2 * 60  # 有一部分沒抓到時，隔這麼久再打開開場畫面就重抓

# 帳號 → {"data": {part: 資料}, "failed": set, "task": asyncio.Task, "finished_at": time.monotonic()}
_states: dict[str, dict[str, Any]] = {}


def _side_view(session):
    """跟 session 共用同一個瀏覽器（登入狀態一樣），但 iNCU 用另一個分頁的 NCUSession。

    action_tools 的方法都用 self.incu_page 開 iNCU，複製一份把它清掉，第一次用到時就會開新的分頁。
    重新登入（_login_on_page）會開看得到的 Chrome 請使用者登入，背景準備時不做。"""
    view = copy.copy(session)
    view.incu_page = None

    async def _no_relogin(page):
        raise RuntimeError("iNCU 的登入狀態失效了，背景準備提醒時不重新登入")

    view._login_on_page = _no_relogin
    return view


async def _registrations(view) -> list:
    return (await view.get_my_activity_registrations()).get("registrations", [])


async def _hour_activities(view) -> list:
    return (await view.get_hours_dashboard()).get("activities", [])


def _eligible_scholarships(transcript: dict) -> list:
    return match_scholarships(get_scholarship_index(), build_profile(transcript))["eligible"]


async def _scholarships(view) -> list:
    transcript, _ = await fetch_academic_records(view, include_graduation=False)
    # 載入法規文件目錄跟比對獎學金是同步的，丟到背景 thread，不要卡住 event loop
    return await asyncio.to_thread(_eligible_scholarships, transcript)


STEPS = {"activities": _registrations, "hours": _hour_activities, "scholarships": _scholarships}


async def _fetch(username: str, state: dict) -> None:
    # 會先等預熱做完（預熱走完授權同意頁，兩個分頁同時走同意頁會互相干擾）。沒有登入中的 session 就是 None
    try:
        session = await agent_tools.get_or_create_session(username, "")
    except Exception as e:
        print(f"[提醒] {username} 取得登入狀態失敗：{e}")
        session = None
    if session is None:
        state["failed"].update(PARTS)
        return
    view = _side_view(session)
    try:
        for part in PARTS:
            try:
                state["data"][part] = await asyncio.wait_for(STEPS[part](view), STEP_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 包括逾時，其他部分照樣抓
                state["failed"].add(part)
                print(f"[提醒] {username} 的{PART_LABELS[part]}沒有抓到：{type(e).__name__} {e}")
    finally:
        page = view.incu_page
        if page is not None and page is not session.incu_page:
            try:
                await page.close()
            except Exception:
                pass


def start(username: str) -> None:
    """在背景開始準備（已經在準備就不重來）。登入成功後由 main.py 呼叫，要在 event loop 裡呼叫。"""
    state = _states.setdefault(username, {"data": {}, "failed": set(), "task": None, "finished_at": None})
    if _running(state):
        return
    state["failed"] = set()

    async def run():
        started = time.monotonic()
        try:
            await _fetch(username, state)
            print(f"[提醒] {username} 的「最近要注意」準備好了（{time.monotonic() - started:.1f} 秒）")
        finally:
            state["finished_at"] = time.monotonic()

    state["task"] = asyncio.get_running_loop().create_task(run())


def _running(state: dict) -> bool:
    return state["task"] is not None and not state["task"].done()


def _should_refresh(state: dict) -> bool:
    if _running(state) or state["finished_at"] is None:
        return False
    age = time.monotonic() - state["finished_at"]
    return age > REFRESH_SECONDS or (bool(state["failed"]) and age > RETRY_FAILED_SECONDS)


def snapshot(username: str) -> dict[str, Any]:
    """給 reminders.build_reminders 的參數：抓到的資料、還在抓的部分、這次沒抓到的部分。
    還沒準備過或資料太舊時，順便在背景開始準備（要在 event loop 裡呼叫）。"""
    state = _states.get(username)
    if state is None or _should_refresh(state):
        start(username)
        state = _states[username]
    data, running = state["data"], _running(state)
    return {
        "registrations": data.get("activities"),
        "hour_activities": data.get("hours"),
        "scholarships": data.get("scholarships"),
        # 重抓時已經有舊資料的部分不算載入中，也不算沒抓到（先給舊的）
        "loading": [p for p in PARTS if running and p not in data and p not in state["failed"]],
        "failed": [p for p in PARTS if p in state["failed"] and p not in data],
    }


def clear(username: str) -> None:
    """登出時呼叫：停掉還在跑的準備，清掉這個帳號的資料（裡面有成績、報名紀錄）。"""
    state: Optional[dict] = _states.pop(username, None)
    if state is not None and _running(state):
        state["task"].cancel()
