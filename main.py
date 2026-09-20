import sys
import os
import json
import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel
from supervisor_agent import run_ncuxplore_agent, run_ncuxplore_agent_stream
from agent_tools import (
    authenticate_and_get_session,
    issue_session_token,
    resolve_session_token,
    revoke_session_token,
    reset_session,
)
import oauth_portal
from logging_config import make_print_logger

print = make_print_logger(__name__)

app = FastAPI(title="NCUXplore Agent System")

# ⚠️ data/ 底下是爬蟲抓回來的校園法規文件，直接掛在 /files 下對外提供，
# 沒有做任何驗證。目前只靠 uvicorn.run(host="127.0.0.1", ...)（見檔案最下面）
# 限制成只有本機能連得到，所以還算安全。如果之後要把這支服務開放到
# 127.0.0.1 以外（校內網路、雲端主機...），這裡要先加上驗證，不要只是把
# host 改成 "0.0.0.0"。
app.mount("/files", StaticFiles(directory="data"), name="files")

@app.get("/api/health")
async def health():
    """給前端判斷「有沒有連得上後端」用的輕量端點，不做任何實際工作。"""
    return {"status": "ok"}


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(req: LoginRequest):
    """驗證 Portal 帳密（一定會真的重新跑一次登入，不會因為這個帳號剛好
    已經有現成 session 就跳過密碼檢查——否則只要知道別人的帳號、隨便帶一組
    密碼就能拿到那個人現成 session 的 token，見 agent_tools.
    authenticate_and_get_session() 的說明），成功後發一個 session token
    給前端。前端登入成功後只會存這個 token，不會再存密碼，之後每一輪對話
    也只帶 token，不會再把密碼傳過來。
    """
    if not req.username or not req.password:
        return {"status": "error", "message": "請輸入帳號和密碼。"}

    try:
        session = await authenticate_and_get_session(req.username, req.password)
    except Exception as e:
        print(f"[main API] 登入失敗（{req.username}）：{e}")
        return {"status": "error", "message": f"登入失敗，請確認帳號密碼是否正確。（{e}）"}

    if session is None:
        return {"status": "error", "message": "登入失敗，請確認帳號密碼是否正確。"}

    token = issue_session_token(req.username)
    print(f"[main API] {req.username} 登入成功，已核發 session token。")
    return {"status": "success", "token": token, "username": req.username}


@app.get("/api/oauth/login")
async def oauth_login():
    """把瀏覽器導去中大 Portal 官方 OAuth 授權頁（見 oauth_portal.py 開頭的
    說明）——使用者在 portal.ncu.edu.tw 自己的頁面輸入帳密，我們完全看不到
    密碼，只會在使用者同意授權後拿到一個 access token 去換身分。

    這條路是給瀏覽器「整頁導航」用的，不是給前端 fetch/XHR 呼叫（OAuth
    授權本來就需要離開我們的網站、到 Portal 那邊完成，再被導回來）。
    """
    try:
        url = oauth_portal.build_authorization_url()
    except RuntimeError as e:
        return RedirectResponse(oauth_portal.build_return_url("/login", oauth_error=str(e)))
    return RedirectResponse(url)


@app.get("/api/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Portal OAuth 授權完成後導回這裡。成功的話換到使用者身分（identifier
    當作我們系統內的 username），核發跟 /api/login 同一套 session token，
    再把瀏覽器導回前端、由前端把 token 存起來（見 frontend 的
    /oauth-complete 頁面）。

    ⚠️ 這裡只驗證了身分，還沒有建立 Playwright 背景 session——課表/時數/
    選課這些需要自動化操作 Portal/選課系統的功能，第一次使用時仍然會需要
    使用者另外提供一次密碼（走 /api/login，這裡拿到的 username 可以直接
    帶進去，不用使用者重打帳號），因為 OAuth 官方 API 沒有提供這些資料/
    操作的介面。
    """
    if error:
        print(f"[main API] Portal OAuth 授權失敗或被使用者拒絕：{error}")
        return RedirectResponse(oauth_portal.build_return_url("/login", oauth_error="Portal 授權失敗或已取消。"))

    if not oauth_portal.consume_state(state):
        print("[main API] Portal OAuth callback 的 state 驗證失敗（可能逾時、重複使用，或是偽造的請求）。")
        return RedirectResponse(
            oauth_portal.build_return_url("/login", oauth_error="登入驗證逾時或無效，請重新登入一次。")
        )

    try:
        identity = await oauth_portal.exchange_code_for_identity(code)
    except Exception as e:
        print(f"[main API] Portal OAuth 換身分失敗：{e}")
        return RedirectResponse(
            oauth_portal.build_return_url("/login", oauth_error=f"登入失敗，請再試一次。（{e}）")
        )

    username = identity["identifier"]
    token = issue_session_token(username)
    print(f"[main API] {username}（{identity.get('chinese_name') or '未知姓名'}）透過 Portal OAuth 登入成功。")

    return RedirectResponse(
        oauth_portal.build_return_url(
            "/oauth-complete",
            token=token,
            username=username,
            chinese_name=identity.get("chinese_name", ""),
        )
    )


class LogoutRequest(BaseModel):
    token: str
    username: str = ""


@app.post("/api/logout")
async def logout(req: LogoutRequest):
    """登出：讓 token 失效，並順手關掉背景瀏覽器 session（不是必要動作，
    純粹避免長時間掛著沒人用的 Playwright session 占資源）。
    """
    revoke_session_token(req.token)
    if req.username:
        await reset_session(req.username)
    return {"status": "success"}


class ChatRequest(BaseModel):
    user_message: str
    # 登入後只會帶 token，不會再帶明文密碼（訪客模式 token 是空字串）。
    # ⚠️ 這裡刻意不接受 username/password：get_or_create_session() 對「已有
    # 現成 session 的帳號」不會比對密碼，如果這裡還留著 username/password
    # 當作沒有 token 時的備援，等於任何人只要猜到/知道一個已經登入過的
    # 帳號，就能不帶任何憑證直接冒用該帳號的對話與 Playwright session
    # （曾經因為「相容舊呼叫端」的理由留著這個備援，但唯一會用到它的舊版
    # 前端 ui.py 在這個分支已經刪掉了，沒有理由再冒這個風險）。
    token: str = ""
    # 前端每個瀏覽器分頁會各自帶一個獨立的 thread_id，讓不同使用者的對話
    # 歷史、pending_action 不會共用同一份 LangGraph 對話狀態。沒帶的話退回
    # 舊的預設值，行為等同改動前（僅供沒更新前端的舊呼叫端相容用）。
    thread_id: str = "default_session"


def _resolve_credentials(req: ChatRequest) -> tuple[str, str]:
    """用 token 換回 username；沒有 token 或 token 已經失效（伺服器重啟過、
    使用者登出過、從未登入過）一律視為訪客，回傳空字串——不接受任何形式的
    明文密碼備援，見 ChatRequest.token 的說明。
    """
    if req.token:
        resolved_username = resolve_session_token(req.token)
        if resolved_username:
            return resolved_username, ""
    return "", ""

@app.post("/api/chat")
async def chat_with_agent(req: ChatRequest):
    print(f"\n[main API] 收到前端訊息：「{req.user_message}」（thread_id={req.thread_id}）")
    username, password = _resolve_credentials(req)

    try:
        final_state = await run_ncuxplore_agent(
            req.user_message, username, password, thread_id=req.thread_id
        )
    except Exception as e:
        # agent 內部任何未預期的例外（Playwright 掛掉、OpenAI timeout...）都
        # 在這裡接住，回傳跟正常情況一樣的 JSON 形狀，讓前端不用另外處理
        # 500 錯誤頁——ui.py 原本就是直接把 response["response"][0] 當成
        # 回覆內容顯示，這裡維持同樣的形狀就能自然顯示出友善的錯誤訊息。
        print(f"[main API] Agent 執行時發生未預期錯誤：{e}")
        return {
            "status": "error",
            "response": [f"系統處理這則訊息時發生未預期的錯誤，請稍後再試一次。（{e}）"],
            "sources": [],
            "debug_info": {
                "current_step": "failed",
                "error": str(e),
            },
        }

    if isinstance(final_state, dict):
        all_results = final_state.get("agent_results", [])
        if all_results:
            results = [all_results[-1]]
        else:
            results = ["系統沒有回傳任何結果。"]
        sources = final_state.get("sources", [])
        called_tools = final_state.get("called_tools", [])
    else:
        results = [str(final_state)]
        sources = []
        called_tools = []

    return {
        "status": "success",
        "response": results,
        "sources": sources,
        "debug_info": {
            "current_step": "completed",
            # 實際這輪呼叫了哪些工具（或走了哪個攔截分支），取代原本寫死的
            # "Academic Agent"——tool use 化之後這個資訊已經可以準確拿到了。
            "executed_agent": ", ".join(called_tools) if called_tools else "（沒有呼叫任何工具，直接回覆文字）",
        }
    }


@app.post("/api/chat/stream")
async def chat_with_agent_stream(req: ChatRequest):
    """`/api/chat` 的 SSE 串流版本：邊算邊把事件吐給前端（狀態文字、逐字
    答案、結構化卡片），不用等整段答案算完才有畫面反應。事件格式見
    `supervisor_agent.run_ncuxplore_agent_stream()` 的說明。
    """
    print(f"\n[main API] 收到前端串流請求：「{req.user_message}」（thread_id={req.thread_id}）")
    username, password = _resolve_credentials(req)

    async def event_source():
        try:
            async for event in run_ncuxplore_agent_stream(
                req.user_message, username, password, thread_id=req.thread_id
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            print(f"[main API] 串流時發生未預期錯誤：{e}")
            error_event = {"type": "error", "message": f"系統處理這則訊息時發生未預期的錯誤：{e}"}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # 避免中間有反向代理（例如 nginx）把 SSE 串流整段緩衝住，
            # 讓「邊算邊吐」在瀏覽器那端變成又是等全部算完才一次收到。
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# 掛載 React 前端打包結果（frontend/dist，跟 main.py 同一個專案目錄下）。
# 一定要放在 /api、/files 這些路由都註冊完之後才 mount，不然這個
# catch-all 靜態掛載會先攔截掉所有路徑、蓋掉上面的 API 路由。
# frontend/dist 還沒 build 出來之前這裡會被跳過，不影響 API 本身
# （開發時前端另外用 `npm run dev` 起 Vite dev server，用不到這個掛載）。
_frontend_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
else:
    print(f"[main API] 找不到前端打包結果（{_frontend_dist}），略過掛載；開發時請用 `npm run dev` 另外起前端。")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # ⚠️ 這裡的 host 一定要維持 "127.0.0.1"（僅本機可連線）——/files 掛的
    # data/ 資料夾、/api/chat 收的帳號密碼目前都沒有任何驗證機制，
    # 改成 "0.0.0.0" 之前請先看上面 StaticFiles 掛載處的說明。
    # ⚠️ 特意直接傳 app 物件（不是 "main:app" 字串）。字串形式是給
    # reload=True 用的：uvicorn 需要能夠在檔案變動時重新 import 一次拿到
    # 新版的 app。但這裡 reload=False，而且本來就已經在 `python main.py`
    # 這個 process 裡把整支檔案當 __main__ 執行過一次了——如果還傳字串，
    # uvicorn 會再用模組名稱 "main"（不是 "__main__"，Python 對這是
    # 兩個不同的 sys.modules cache key）重新 import 一次整支檔案，等於
    # 這支檔案的最上層程式碼（包含建立 FastAPI() app、上面那些
    # print/掛載判斷）會跑兩遍，只是第一遍建出來的 app 沒被用到、白跑。
    # 直接傳物件就不會有這個問題。
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
