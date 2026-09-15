import sys
import os
import json
import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from supervisor_agent import run_ncuxplore_agent, run_ncuxplore_agent_stream
from logging_config import make_print_logger

print = make_print_logger(__name__)

app = FastAPI(title="NCUXplore Agent System")

# ⚠️ data/ 底下是爬蟲抓回來的校園法規文件，直接掛在 /files 下對外提供，
# 沒有做任何驗證。目前只靠 uvicorn.run(host="127.0.0.1", ...)（見檔案最下面）
# 限制成只有本機能連得到，所以還算安全。如果之後要把這支服務開放到
# 127.0.0.1 以外（校內網路、雲端主機...），這裡要先加上驗證，不要只是把
# host 改成 "0.0.0.0"。
app.mount("/files", StaticFiles(directory="data"), name="files")

class ChatRequest(BaseModel):
    user_message: str
    username: str = ""
    password: str = ""
    # 前端每個瀏覽器分頁會各自帶一個獨立的 thread_id，讓不同使用者的對話
    # 歷史、pending_action 不會共用同一份 LangGraph 對話狀態。沒帶的話退回
    # 舊的預設值，行為等同改動前（僅供沒更新前端的舊呼叫端相容用）。
    thread_id: str = "default_session"

@app.post("/api/chat")
async def chat_with_agent(req: ChatRequest):
    print(f"\n[main API] 收到前端訊息：「{req.user_message}」（thread_id={req.thread_id}）")

    try:
        final_state = await run_ncuxplore_agent(
            req.user_message, req.username, req.password, thread_id=req.thread_id
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

    async def event_source():
        try:
            async for event in run_ncuxplore_agent_stream(
                req.user_message, req.username, req.password, thread_id=req.thread_id
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
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
