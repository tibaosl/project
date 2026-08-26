from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
from action_tools import NCUSession, get_schedule, search_courses
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

global_ncu_session = None

class AgentState(TypedDict):
    user_input: str
    username: str
    password: str
    current_step: str
    agent_results: Annotated[List[str], operator.add]
    next_agent: str
    past_queries: Annotated[List[str], operator.add]
    sources: List[str]


async def supervisor_node(state: AgentState):
    user_input = state['user_input']
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-3:]) if history else "無"

    print(f"\n[Supervisor Agent] 收到使用者需求：「{user_input}」")
    print(f"[Supervisor Agent] 參考對話歷史：「{history_str}」")
    
    system_prompt = f"""
    你是中央大學 NCUXplore 系統的最高階任務調度員 (Router)。
    你的任務是精準判斷使用者的意圖。請結合對話歷史，依照以下【核心決策樹】嚴格分類：

    【對話歷史】：{history_str}
    【最新輸入】：{user_input}

    【核心分類決策樹】：
    第一步：區分「法規查詢」與「系統代操」
    - 若問題包含「要錢嗎、多少錢、怎麼借、怎麼申請、規定是什麼、期限、門檻」，或是詢問特定名詞的解釋，這是「法規與資訊」 -> 判斷為 ACADEMIC（或因條件不足進入第二步）。
    - 若明確要求「幫我登入 Portal」、「幫我查我的課表」 -> 判斷為 ACTION。
    - ★ 針對「選課、找課、加選」這類指令：
        - 若有明確提到「課程關鍵字或名稱」（例如：幫我選日文課、找微積分） -> 判斷為 ACTION。
        - 若非常籠統、完全沒提到任何特定課程（例如：幫我選課、我要找課、可以幫我加選嗎） -> 判斷為 CLARIFY。

    第二步：法規查詢的條件檢查
    若問題極度空泛且「完全沒有」指定任何系所或學制（如：單純只講「畢業門檻」、「必修」） -> 判斷為 CLARIFY。
    - 【強制放行原則】：只要對話歷史或最新輸入中，已經出現了「系所」或「學制」，或是使用者明顯在回答上一次的提問，就代表條件已經達成最低門檻 -> 判斷為 ACADEMIC。絕對禁止讓使用者陷入連續 CLARIFY 的迴圈。

    第三步：無效輸入處理
    - 若皆非以上兩者，或是純閒聊、打招呼、無意義字詞 -> 判斷為 FALLBACK。

    【意圖判斷防呆（必讀）】：
    - 看到「要錢嗎」、「費用」、「補助」，這 100% 是法規查詢，請歸類為 ACADEMIC。

    請只輸出 ACTION, CLARIFY, ACADEMIC 或 FALLBACK 其中一個單字，絕對不要輸出其他任何文字與標點符號。
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input)
    ]
    response = await llm_smart.ainvoke(messages)
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


async def clarification_node(state: AgentState):
    print("\n[Clarification Agent] 發現問題範圍太大，準備引導使用者...")
    user_input = state['user_input']
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-3:]) if history else "無"

    prompt = f"""
    你是一個親切的中央大學 NCUXplore 校園助手。
    使用者目前的問題是：「{user_input}」。（對話歷史：{history_str}）
    
    這個問題的範圍太大了或是缺乏關鍵資訊。可能的情況有兩種：
    1. 查詢法規缺乏條件（例如：問畢業門檻但沒說學制）。
    2. 要求系統代操但沒有目標（例如：說要選課但沒說要選什麼課）。

    請根據使用者的問題，產出一段親切的引導文字，請他提供具體細節：
    - 若是法規問題，請引導他提供「學制（如學士班、碩士班）」或特定條件。
    - 若是選課問題，請引導他提供「想搜尋的課程名稱或關鍵字」。

    輸出範例語氣：
    （針對法規）「畢業門檻包含許多不同的項目喔！請告訴我您想查詢的是哪個學制的規定？例如：學士班（大學部）或碩士班？」
    （針對選課）「沒問題！你想找什麼樣的課程呢？請告訴我課程名稱或關鍵字（例如：日文、資料結構），我立刻幫你搜尋！」
    """
    
    try:
        print("[Clarification Agent] 正在等待 OpenAI 回應...")
        response = await llm_fast.ainvoke([HumanMessage(content=prompt)])
        print("[Clarification Agent] OpenAI 回應成功！")
        ai_reply = f"**引導助手**：\n{response.content}"
        return {
            "agent_results": [ai_reply],
            "past_queries": [f"[引導助手]: {response.content}"]
        }
    except Exception as e:
        error_msg = str(e)
        print(f"[Clarification Agent] 呼叫 LLM 發生錯誤：{error_msg}")
        return {"agent_results": [f"[系統提示]: 抱歉，AI 思考時發生了一點錯誤（{error_msg}），請確認 API Key 狀態或稍後再試！"]}


async def action_agent_node(state: AgentState):
    global global_ncu_session
    print("\n[Action Agent] 被喚醒了！準備去操作網頁...")
    user_input = state['user_input']
    username = state.get("username", "")
    password = state.get("password", "")
    
    if not username or not password:
        return {"agent_results": ["[Action Agent 回報]:\n缺乏帳號或密碼，無法執行 Portal 登入自動化操作。請先在左側邊欄輸入帳號密碼！"]}

    extraction_prompt = f"""
    分析以下使用者的輸入，判斷他想要執行的網頁自動化動作：
    使用者輸入：「{user_input}」

    請判斷動作類型為以下兩種之一：
    1. SCHEDULE: 查詢個人課表
    2. SEARCH: 在選課系統搜尋特定課程

    如果你判斷為 SEARCH，請同時提取使用者想搜尋的「課程關鍵字」。
    例如：「幫我找日文課」 -> 關鍵字為「日文」。

    請嚴格遵守以下 JSON 格式輸出，不要輸出任何其他文字：
    {{
        "action_type": "SCHEDULE" 或 "SEARCH",
        "keyword": "搜尋關鍵字(如果是 SCHEDULE 請留空字串)"
    }}
    """
    try:
        extraction_response = llm_fast.invoke([HumanMessage(content=extraction_prompt)])
        import json
        raw_json = extraction_response.content.replace("```json", "").replace("```", "").strip()
        parsed_action = json.loads(raw_json)
        
        action_type = parsed_action.get("action_type", "SCHEDULE")
        keyword = parsed_action.get("keyword", "")
        
        print(f"[Action Agent] 解析動作意圖: 類型={action_type}, 關鍵字={keyword}")
        
    except Exception as e:
        print(f"[Action Agent] 解析動作意圖失敗，預設執行查課表。錯誤：{e}")
        action_type = "SCHEDULE"
        keyword = ""

    try:
        if global_ncu_session is None or global_ncu_session.username != username:
            print("[Action Agent] 啟動全新的瀏覽器 Session，準備登入...")
            if global_ncu_session is not None:
                await global_ncu_session.close()
                
            global_ncu_session = NCUSession(username, password)
            await global_ncu_session.start()
        else:
            print("[Action Agent] 沿用已經開啟的瀏覽器與網頁！不需要重登...")

        if action_type == "SEARCH" and keyword:
            search_data = await search_courses(global_ncu_session, keyword)
            
            if search_data:
                result_text = f"**「{keyword}」搜尋結果：**\n\n"
                for item in search_data:
                    result_text += f"- {item['serial']} | {item['course_no']} | **{item['title']}** | {item['teacher']}\n"
                return {"agent_results": [result_text]}
            else:
                return {"agent_results": [f"**Action Agent 回報**：\n找不到關鍵字為「{keyword}」的課程。"]}
        else:
            schedule_data = await get_schedule(global_ncu_session)
            if schedule_data is not None:
                return {"agent_results": [schedule_data]}
            else:
                return {"agent_results": ["**Action Agent 回報**：\n執行失敗：無法解析課表或查無資料"]}

    except Exception as e:
        print(f"[Action Agent] 發生錯誤: {e}")
        if global_ncu_session is not None:
            await global_ncu_session.close()
            global_ncu_session = None
            
        return {"agent_results": [f"**Action Agent 回報**：\n系統執行時發生錯誤：{str(e)}"]}


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
        "past_queries": [f"[使用者]: {user_message}"],
        "sources": []
    }
    config = {"configurable": {"thread_id": thread_id}}
    final_state = await app.ainvoke(initial_state, config=config)
    return final_state