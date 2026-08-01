from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
from action_tools import ncu_portal_login_tool
from academic_agent import query_academic_knowledge
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

class AgentState(TypedDict):
    user_input: str
    username: str
    password: str
    current_step: str
    agent_results: Annotated[List[str], operator.add]
    next_agent: str

# 定義 Nodes
def supervisor_node(state: AgentState):
    user_input = state['user_input']
    print(f"\n[Supervisor Agent] 收到使用者需求：「{user_input}」")
    print("[Supervisor Agent] 正在思考要派誰去執行...")
    
    system_prompt = """
    你是中央大學 NCUXplore 系統的任務調度員。
    請分析使用者的輸入，並將其嚴格分類為以下三種意圖之一，只能輸出這三個英文單字之一，不要輸出其他廢話：
    1. ACTION：如果使用者想登入 Portal、查詢課表、填寫表單等「操作系統」的行為。
    2. ACADEMIC：如果使用者想詢問法規、學分、選課規定、畢業門檻等「知識查詢」的行為。
    3. FALLBACK：如果不屬於以上兩者，或者是無意義的閒聊。
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input)
    ]
    response = llm.invoke(messages)
    intent = response.content.strip().upper()
    
    print(f"[Supervisor Agent] LLM 判斷意圖為：{intent}")
    
    if "ACTION" in intent:
        next_action = "Action Agent"
    elif "ACADEMIC" in intent:
        next_action = "Academic Agent"
    else:
        next_action = "Fallback Agent"
        
    print(f"[Supervisor Agent] 決定指派任務給 ➔ {next_action}")
    return {"current_step": "supervisor_decided", "next_agent": next_action}

async def action_agent_node(state: AgentState):
    print("\n[Action Agent] 被喚醒了！準備去操作網頁...")
    if not state.get("username") or not state.get("password"):
        return {"agent_results": ["[Action Agent 回報] 失敗：請提供 Portal 帳號與密碼才能執行登入喔！"]}
    
    try:
        action_result = await ncu_portal_login_tool(state["username"], state["password"])
        result_message = action_result["message"]
    except Exception as e:
        result_message = f"自動化操作發生錯誤：{str(e)}"
        
    return {"agent_results": [result_message]}

def academic_agent_node(state: AgentState):
    print("\n[Academic Agent] 被喚醒了！準備去翻找法規...")
    user_question = state["user_input"]
    
    try:
        result = query_academic_knowledge(user_question)
    except Exception as e:
        result = f"查詢法規時發生錯誤：{str(e)}"
        
    return {"agent_results": [f"[Academic Agent 回報]: {result}"]}

def fallback_node(state: AgentState):
    print("\n[Fallback Agent] 被喚醒了！發現這不在系統的服務範圍內...")
    result = "我是 NCUXplore 校園助手，目前僅提供「校園法規查詢」與「Portal 自動化登入」服務喔！其他問題我暫時還聽不懂～"
    return {"agent_results": [result]}

# 定義 Edges
def router(state: AgentState):
    if state["next_agent"] == "Action Agent":
        return "action_node"
    elif state["next_agent"] == "Academic Agent":
        return "academic_node"
    elif state["next_agent"] == "Fallback Agent":
        return "fallback_node"
    else:
        return "end_node"

# 組裝 LangGraph
workflow = StateGraph(AgentState)
workflow.add_node("supervisor_node", supervisor_node)
workflow.add_node("action_node", action_agent_node)
workflow.add_node("academic_node", academic_agent_node)
workflow.add_node("fallback_node", fallback_node)
workflow.set_entry_point("supervisor_node")
workflow.add_conditional_edges(
    "supervisor_node",
    router,
    {
        "action_node": "action_node",
        "academic_node": "academic_node",
        "fallback_node": "fallback_node",
        "end_node": END
    }
)
workflow.add_edge("action_node", END)
workflow.add_edge("academic_node", END)
workflow.add_edge("fallback_node", END)
app = workflow.compile()

async def run_ncuxplore_agent(user_message: str, username: str = "", password: str = ""):
    initial_state = {
        "user_input": user_message, 
        "username": username,
        "password": password,
        "agent_results": []
    }
    final_state = await app.ainvoke(initial_state)
    return final_state