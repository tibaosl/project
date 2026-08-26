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

@app.post("/api/chat")
async def chat_with_agent(req: ChatRequest):
    print(f"\n[main API] 收到前端訊息：「{req.user_message}」")
    final_state = await run_ncuxplore_agent(req.user_message, req.username, req.password)

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