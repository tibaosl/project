from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
from action_tools import main
from academic_agent import query_academic_knowledge
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()
llm_smart = ChatOpenAI(model="gpt-4o-mini", temperature=0)
llm_fast = ChatOpenAI(model="gpt-4o-mini", temperature=0)
memory = MemorySaver()

class AgentState(TypedDict):
    user_input: str
    username: str
    password: str
    current_step: str
    agent_results: Annotated[List[str], operator.add]
    next_agent: str
    past_queries: Annotated[List[str], operator.add]
    sources: List[str]

def supervisor_node(state: AgentState):
    user_input = state['user_input']
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-3:]) if history else "無"

    print(f"\n[Supervisor Agent] 收到使用者需求：「{user_input}」")
    print(f"[Supervisor Agent] 參考對話歷史：「{history_str}」")
    
    system_prompt = f"""
    你是中央大學 NCUXplore 系統的最高階任務調度員 (Router)。
    你的任務是精準判斷使用者的意圖，絕不能被字面上的動詞（如：借、申請、選課）誤導。請結合對話歷史，依照以下【核心決策樹】嚴格分類：

    【對話歷史】：{history_str}
    【最新輸入】：{user_input}

    【核心分類決策樹】：
    第一步：區分「資訊查詢」還是「系統代操」？
    - 若問題包含「要錢嗎、多少錢、怎麼借、怎麼申請、規定是什麼、期限、門檻」，或是詢問特定名詞的解釋，這是想知道「法規與資訊」 -> 進入第二步。
    - 若使用者明確下達指令要求「幫我登入 Portal、幫我查我的課表、幫我自動填寫、幫我選課」，這是要求「自動化代操」 -> 判斷為 ACTION。

    第二步：若是資訊查詢，條件足夠嗎？
    若問題極度空泛且「完全沒有」指定任何系所或學制（如：單純只講「畢業門檻」、「必修」） -> 判斷為 CLARIFY。
    - 【強制放行原則】：只要對話歷史或最新輸入中，已經出現了「系所（如資工系）」或「學制（如學士班）」，或是使用者明顯在回答上一次的提問，就代表條件已經達成最低門檻 -> 判斷為 ACADEMIC。絕對禁止讓使用者陷入連續 CLARIFY 的迴圈。

    第三步：無效輸入處理
    - 若皆非以上兩者，或是純閒聊、打招呼、無意義字詞 -> 判斷為 FALLBACK。

    【常見陷阱防呆（必讀）】：
    - 陷阱 1：看到「借教室」、「請假」、「選課」等動詞，絕對不要直接當作 ACTION。除非使用者說「幫我登入系統去操作」，否則預設為 ACADEMIC。
    - 陷阱 2：看到「要錢嗎」、「費用」、「補助」，這 100% 是法規查詢，請歸類為 ACADEMIC。

    請只輸出 ACTION, CLARIFY, ACADEMIC 或 FALLBACK 其中一個單字，絕對不要輸出其他任何文字與標點符號。
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input)
    ]
    response = llm_smart.invoke(messages)
    intent = response.content.strip().upper()
    
    print(f"[Supervisor Agent] LLM 判斷意圖為：{intent}")
    
    if "ACTION" in intent:
        next_action = "Action Agent"
    elif "CLARIFY" in intent:
        next_action = "Clarification Agent"
    elif "ACADEMIC" in intent:
        next_action = "Academic Agent"
    else:
        next_action = "Fallback Agent"
        
    print(f"[Supervisor Agent] 決定指派任務給 ➔ {next_action}")
    return {"current_step": "supervisor_decided", "next_agent": next_action}

def clarification_node(state: AgentState):
    print("\n[Clarification Agent] 發現問題範圍太大，準備引導使用者...")
    user_input = state['user_input']
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-3:]) if history else "無"

    prompt = f"""
    你是一個親切的中央大學 NCUXplore 校園助手。
    使用者目前的問題是：「{user_input}」。（對話歷史：{history_str}）
    
    這個問題的範圍太大了（例如：畢業門檻包含不同學制與多種條件）。
    請產出一段引導文字，告訴使用者你需要更多資訊，並「條列出具體的選項」讓他參考。

    輸出範例語氣：
    「根據您的查詢，畢業門檻包含許多不同的項目。請告訴我您想查詢的是哪個學制的規定？
    1. 學士班（大學部）
    2. 碩士班
    或者是特定條件類別：
    - 英文能力檢定標準
    - 必修學分數
    請告訴我您的具體需求，我會立刻為您尋找法規！」
    """
    
    response = llm_fast.invoke([HumanMessage(content=prompt)])
    return {"agent_results": [f"[引導助手]:\n{response.content}"]}


async def action_agent_node(state: AgentState):
    print("\n[Action Agent] 被喚醒了！準備去操作網頁...")
    user_input = state['user_input']
    username = state.get("username", "")
    password = state.get("password", "")

    if not username or not password:
        return {"agent_results": ["[Action Agent 回報]:\n缺乏帳號或密碼，無法執行 Portal 登入自動化操作。請先在左側邊欄輸入帳號密碼！"]}

    try:
        print("[Action Agent] 正在啟動無頭瀏覽器執行任務...")
        result = await main(username, password)
        
        if result.get("status") == "success":
            schedule_data = result.get("data", [])
            return {"agent_results": [schedule_data]}
        else:
            return {"agent_results": [f"[Action Agent 回報]:\n執行失敗：{result.get('message')}"]}

    except Exception as e:
        print(f"[Action Agent] 發生錯誤: {e}")
        return {"agent_results": [f"[Action Agent 回報]:\n系統執行時發生錯誤：{str(e)}"]}


def academic_agent_node(state: AgentState):
    print("\n[Academic Agent] 被喚醒了！準備去翻找法規...")
    user_question = state["user_input"]
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-3:]) if history else "無"
    
    try:
        result_dict = query_academic_knowledge(user_question, history_str)
        answer_text = result_dict.get("answer", "")
        sources = result_dict.get("sources", [])
        return {
            "agent_results": [f"**Academic Agent 回報**：\n{answer_text}"],
            "sources": sources
        }
    except Exception as e:
        return {
            "agent_results": [f"查詢法規時發生錯誤：{str(e)}"],
            "sources": []
        }


def fallback_node(state: AgentState):
    print("\n[Fallback Agent] 被喚醒了！發現這不在系統的服務範圍內...")
    result = "我是 NCUXplore 校園助手，目前僅提供「校園法規查詢」與「Portal 自動化登入」服務喔！其他問題我暫時還聽不懂～"
    return {"agent_results": [result]}


def router(state: AgentState):
    agent_map = {
        "Action Agent": "action_node",
        "Clarification Agent": "clarification_node",
        "Academic Agent": "academic_node",
        "Fallback Agent": "fallback_node"
    }
    return agent_map.get(state["next_agent"], "end_node")


workflow = StateGraph(AgentState)
workflow.add_node("supervisor_node", supervisor_node)
workflow.add_node("clarification_node", clarification_node)
workflow.add_node("action_node", action_agent_node)
workflow.add_node("academic_node", academic_agent_node)
workflow.add_node("fallback_node", fallback_node)

workflow.set_entry_point("supervisor_node")
workflow.add_conditional_edges(
    "supervisor_node",
    router,
    {
        "action_node": "action_node",
        "clarification_node": "clarification_node",
        "academic_node": "academic_node",
        "fallback_node": "fallback_node",
        "end_node": END
    }
)

workflow.add_edge("action_node", END)
workflow.add_edge("clarification_node", END)
workflow.add_edge("academic_node", END)
workflow.add_edge("fallback_node", END)

app = workflow.compile(checkpointer=memory)


async def run_ncuxplore_agent(user_message: str, username: str = "", password: str = "", thread_id: str = "default_session"):
    initial_state = {
        "user_input": user_message, 
        "username": username,
        "password": password,
        "current_step": "start",
        "agent_results": [],
        "next_agent": "",
        "past_queries": [user_message],
        "sources": []
    }
    config = {"configurable": {"thread_id": thread_id}}
    final_state = await app.ainvoke(initial_state, config=config)
    return final_state