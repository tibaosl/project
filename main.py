import sys
import asyncio
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from action_tools import ncu_portal_login_tool
from supervisor_agent import run_ncuxplore_agent

app = FastAPI(title="NCUXplore Agent System")

class ChatRequest(BaseModel):
    user_message: str
    username: str = ""
    password: str = ""

@app.post("/api/chat")
async def chat_with_agent(req: ChatRequest):
    """
    接收學生輸入的白話文，交給 Supervisor Agent 處理
    """
    print(f"\n[main API] 收到前端訊息：「{req.user_message}」")

    result_state = await run_ncuxplore_agent(req.user_message, req.username, req.password)

    print(f"[main API] LangGraph 執行完畢的狀態：{result_state}")
    
    results = result_state.get("agent_results", ["系統沒有回傳任何結果。"])

    return {
        "status": "success",
        "response": results,
        "debug_info": {
            "current_step": result_state["current_step"],
            "executed_agent": result_state["next_agent"]
        }
    }

# 舊測試
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/action/login")
async def trigger_login(req: LoginRequest):
    result = await ncu_portal_login_tool(req.username, req.password)
    return {"status": "success", "data": result}

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)