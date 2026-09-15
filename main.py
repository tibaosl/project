import sys
import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supervisor_agent import run_ncuxplore_agent

app = FastAPI(title="NCUXplore Agent System")

app.mount("/files", StaticFiles(directory="data"), name="files")

class ChatRequest(BaseModel):
    user_message: str
    username: str = ""
    password: str = ""
    # 前端（ui.py）每個瀏覽器分頁會各自帶一個獨立的 thread_id，讓不同使用者
    # 的對話歷史、pending_action 不會共用同一份 LangGraph 對話狀態。沒帶的話
    # 退回舊的預設值，行為等同改動前（僅供沒更新前端的舊呼叫端相容用）。
    thread_id: str = "default_session"

@app.post("/api/chat")
async def chat_with_agent(req: ChatRequest):
    print(f"\n[main API] 收到前端訊息：「{req.user_message}」（thread_id={req.thread_id}）")
    final_state = await run_ncuxplore_agent(
        req.user_message, req.username, req.password, thread_id=req.thread_id
    )

    if isinstance(final_state, dict):
        all_results = final_state.get("agent_results", [])
        if all_results:
            results = [all_results[-1]]  
        else:
            results = ["系統沒有回傳任何結果。"]
        sources = final_state.get("sources", [])
    else:
        results = [str(final_state)]
        sources = []

    return {
        "status": "success",
        "response": results,
        "sources": sources,
        "debug_info": {
            "current_step": "completed",
            "executed_agent": "Academic Agent"
        }
    }

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)