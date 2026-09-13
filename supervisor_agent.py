from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
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
)
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

# 對話歷史要往回看幾輪，餵給各個 node 的 prompt 當上下文。
# 原本只取 3 輪，多輪任務（例如活動報名的確認流程、選課的來回釐清）
# 很容易就把最相關的那句話擠出視窗，所以放大一點。
HISTORY_WINDOW = 12

class AgentState(TypedDict):
    user_input: str
    username: str
    password: str
    current_step: str
    agent_results: Annotated[List[str], operator.add]
    next_agent: str
    past_queries: Annotated[List[str], operator.add]
    sources: List[str]
    # 用來記住「上一輪請使用者確認是否要報名/取消報名某活動」這件事，
    # 讓確認動作可以跨輪對話（不用一次講完）。沒有 Annotated reducer，
    # 所以每個節點回傳時要嘛明確清空（{}），要嘛明確設成新的待確認動作，
    # 否則舊的待確認動作會一直留著（checkpointer 只覆蓋有回傳的欄位）。
    pending_action: dict


async def supervisor_node(state: AgentState):
    user_input = state['user_input']
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"

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
    - ★ 針對「時數、活動」相關的問題，注意分辨「問自己的狀況」還是「問規則本身」：
        - 若是問「我自己的」時數進度、還差多少小時、有沒有達到畢業門檻、我報名了什麼活動 -> 判斷為 ACTION（這是要查個人資料，不是查規則）。
        - 若是查詢活動列表、活動有哪些場次、活動內容是什麼、依時數缺口推薦活動、幫我報名/取消報名活動 -> 判斷為 ACTION。
        - 若是問「服務學習時數的規定是什麼」「畢業門檻是幾小時」這種制度規則本身、沒有涉及個人資料查詢或操作 -> 判斷為 ACADEMIC。
        - 若使用者這句話明顯是在回覆上一輪系統要求的「確認報名/確認取消」（例如訊息裡包含「確定」兩個字，且對話歷史顯示上一輪在問是否要報名/取消） -> 判斷為 ACTION。

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
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"

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
            "past_queries": [f"[引導助手]: {response.content}"],
            "pending_action": {},
        }
    except Exception as e:
        error_msg = str(e)
        print(f"[Clarification Agent] 呼叫 LLM 發生錯誤：{error_msg}")
        return {
            "agent_results": [f"[系統提示]: 抱歉，AI 思考時發生了一點錯誤（{error_msg}），請確認 API Key 狀態或稍後再試！"],
            "pending_action": {},
        }


async def action_agent_node(state: AgentState):
    global global_ncu_session
    print("\n[Action Agent] 被喚醒了！準備去操作網頁...")
    user_input = state['user_input']
    username = state.get("username", "")
    password = state.get("password", "")
    pending_action = state.get("pending_action") or {}

    # ------------------------------------------------------------------
    # 先處理「確認送出」：如果上一輪有等待確認的報名/取消報名動作，
    # 而這一輪的輸入裡有「確定」兩個字，就直接執行，不用再重新判斷意圖。
    # 用簡單的關鍵字比對而不是再問一次 LLM，是為了確保這個會改變學校
    # 系統資料的動作只在使用者明確表態時才會發生，行為要可預期。
    # ------------------------------------------------------------------
    if pending_action and "確定" in user_input:
        if not username or not password:
            return {
                "agent_results": ["[Action Agent 回報]:\n缺乏帳號或密碼，無法執行。請先在左側邊欄輸入帳號密碼！"],
                "pending_action": {},
            }

        action_type = pending_action.get("type")
        activity_id = pending_action.get("activity_id")
        session_id = pending_action.get("session_id")

        try:
            if global_ncu_session is None or global_ncu_session.username != username:
                if global_ncu_session is not None:
                    await global_ncu_session.close()
                global_ncu_session = NCUSession(username, password)
                await global_ncu_session.start()

            if action_type == "ACTIVITY_REGISTER":
                result = await global_ncu_session.register_for_activity_session(
                    activity_id, session_id=session_id, confirm=True
                )
            elif action_type == "ACTIVITY_CANCEL":
                result = await global_ncu_session.cancel_activity_registration(
                    activity_id, session_id=session_id, confirm=True
                )
            else:
                result = {"message": "沒有找到待確認的動作，請重新告訴我想做什麼。"}

            return {
                "agent_results": [f"**Action Agent 回報**：\n{result.get('message', '')}"],
                "pending_action": {},
            }
        except Exception as e:
            print(f"[Action Agent] 確認動作執行時發生錯誤: {e}")
            if global_ncu_session is not None:
                await global_ncu_session.close()
                global_ncu_session = None
            return {
                "agent_results": [f"**Action Agent 回報**：\n系統執行時發生錯誤：{str(e)}"],
                "pending_action": {},
            }

    # ------------------------------------------------------------------
    # 不是確認動作，走意圖判斷流程
    # ------------------------------------------------------------------
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"

    extraction_prompt = f"""
    分析以下使用者的輸入，判斷他想要執行的網頁自動化動作：
    對話歷史：{history_str}
    使用者輸入：「{user_input}」

    請判斷動作類型為以下其中之一：
    1. SCHEDULE：查詢個人課表
    2. SEARCH：在選課系統搜尋特定課程
    3. HOURS：查詢「使用者自己」的學習護照時數進度、離畢業門檻還差多少
    4. ACTIVITY_SEARCH：單純查詢/瀏覽校內活動列表，使用者自己講出明確的
       篩選條件（活動名稱關鍵字、類別），沒有要系統幫忙判斷該報名什麼
    5. ACTIVITY_INFO：查看某個「已經指名」的活動的詳細資訊/內容/場次
    6. ACTIVITY_RECOMMEND：使用者沒有指定活動名稱，而是要系統依他自己的
       狀況（時數缺口、還差什麼）主動推薦適合報名的活動
    7. ACTIVITY_REGISTER：幫使用者報名某個「已經指名」的活動
    8. ACTIVITY_CANCEL：取消使用者某個「已經指名」活動的報名

    ★★ ACTIVITY_SEARCH 與 ACTIVITY_RECOMMEND 是最容易搞混的兩個，請特別注意：
    - 只要使用者的輸入裡出現「推薦」，或是「有沒有活動可以報名/參加」這種
      沒有指定任何活動名稱或類別、希望系統幫忙挑的說法，一律判斷為
      ACTIVITY_RECOMMEND，絕對不要判斷成 ACTIVITY_SEARCH。
      例如：「有沒有什麼活動可以推薦我報名的」「有什麼活動適合我」
      「幫我推薦活動」「我還缺時數，有什麼可以參加的」-> 都是 ACTIVITY_RECOMMEND。
    - 只有使用者自己講出具體條件（例如「查一下有沒有日文相關的活動」
      「最近有什麼藝文活動」「幫我查一下活動列表」），沒有要系統依他個人
      狀況判斷時，才是 ACTIVITY_SEARCH。這種情況通常有明確的 keyword
      可以提取；如果想不出合理的 keyword，那大概不該判斷成 ACTIVITY_SEARCH。

    如果是 SEARCH 或 ACTIVITY_SEARCH，請提取搜尋關鍵字。
    如果是 ACTIVITY_INFO / ACTIVITY_REGISTER / ACTIVITY_CANCEL，請提取活動的名稱關鍵字
    （使用者通常只會講活動名稱的一部分，不會知道活動編號，用 keyword 表示即可，
    系統會自動搜尋比對最接近的活動）。
    ACTIVITY_RECOMMEND 不需要 keyword，請留空字串。

    請嚴格遵守以下 JSON 格式輸出，不要輸出任何其他文字：
    {{
        "action_type": "SCHEDULE" 或 "SEARCH" 或 "HOURS" 或 "ACTIVITY_SEARCH" 或 "ACTIVITY_INFO" 或 "ACTIVITY_RECOMMEND" 或 "ACTIVITY_REGISTER" 或 "ACTIVITY_CANCEL",
        "keyword": "搜尋關鍵字或活動名稱關鍵字（不需要時留空字串）"
    }}
    """
    try:
        extraction_response = llm_fast.invoke([HumanMessage(content=extraction_prompt)])
        import json
        raw_json = extraction_response.content.replace("```json", "").replace("```", "").strip()
        parsed_action = json.loads(raw_json)

        action_type = parsed_action.get("action_type", "SCHEDULE")
        keyword = parsed_action.get("keyword", "")

        # 保險機制：LLM 偶爾會把「推薦活動」誤判成 ACTIVITY_SEARCH（然後
        # keyword 留空，變成列出全部活動）。只要看到「推薦」兩個字又沒有
        # 抓到具體關鍵字，直接強制修正，不要完全依賴 LLM 的判斷。
        if action_type == "ACTIVITY_SEARCH" and not keyword and "推薦" in user_input:
            print("[Action Agent] 偵測到「推薦」但關鍵字是空的，修正為 ACTIVITY_RECOMMEND")
            action_type = "ACTIVITY_RECOMMEND"

        print(f"[Action Agent] 解析動作意圖: 類型={action_type}, 關鍵字={keyword}")

    except Exception as e:
        print(f"[Action Agent] 解析動作意圖失敗，預設執行查課表。錯誤：{e}")
        action_type = "SCHEDULE"
        keyword = ""

    # ------------------------------------------------------------------
    # 不需要登入的分支：活動查詢／活動詳情都是公開資料，先處理掉，
    # 不用管有沒有帳號密碼。
    # ------------------------------------------------------------------
    if action_type == "ACTIVITY_SEARCH":
        try:
            results = search_activities(keyword=keyword)
            if not results:
                return {
                    "agent_results": [f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動。"],
                    "pending_action": {},
                }
            lines = [f"**「{keyword}」活動搜尋結果（共 {len(results)} 筆）：**\n"]
            for item in results[:10]:
                lines.append(f"- [{item['activity_id']}] {item['title']}（{item['status']}）")
            return {"agent_results": ["\n".join(lines)], "pending_action": {}}
        except Exception as e:
            return {
                "agent_results": [f"**Action Agent 回報**：\n查詢活動時發生錯誤：{str(e)}"],
                "pending_action": {},
            }

    if action_type == "ACTIVITY_INFO":
        try:
            activity_id = _find_activity_id_by_keyword(keyword)
            if not activity_id:
                return {
                    "agent_results": [f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。"],
                    "pending_action": {},
                }
            detail = get_activity_detail(activity_id)
            return {
                "agent_results": [{"kind": "activity_detail", **detail}],
                "pending_action": {},
            }
        except Exception as e:
            return {
                "agent_results": [f"**Action Agent 回報**：\n查詢活動詳情時發生錯誤：{str(e)}"],
                "pending_action": {},
            }

    # ------------------------------------------------------------------
    # 以下都需要登入
    # ------------------------------------------------------------------
    if not username or not password:
        return {
            "agent_results": ["[Action Agent 回報]:\n缺乏帳號或密碼，無法執行 Portal 登入自動化操作。請先在左側邊欄輸入帳號密碼！"],
            "pending_action": {},
        }

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
                return {"agent_results": [result_text], "pending_action": {}}
            else:
                return {
                    "agent_results": [f"**Action Agent 回報**：\n找不到關鍵字為「{keyword}」的課程。"],
                    "pending_action": {},
                }

        elif action_type == "HOURS":
            dashboard_data = await global_ncu_session.get_hours_dashboard()
            envelope = {
                "kind": "hours_dashboard",
                "graduated": dashboard_data["graduated"],
                "categories": dashboard_data["categories"],
            }
            return {"agent_results": [envelope], "pending_action": {}}

        elif action_type == "ACTIVITY_RECOMMEND":
            dashboard_data = await global_ncu_session.get_hours_dashboard()
            deficiencies = get_deficiency_details(dashboard_data)

            if not deficiencies:
                return {
                    "agent_results": ["**Action Agent 回報**：\n你的學習護照時數已經全部達標了，沒有需要補的細項！"],
                    "pending_action": {},
                }

            recommendations = recommend_activities_for_categories(deficiencies)
            envelope = {
                "kind": "activity_recommendations",
                "deficiencies": deficiencies,
                "recommendations": recommendations,
            }
            return {"agent_results": [envelope], "pending_action": {}}

        elif action_type in ("ACTIVITY_REGISTER", "ACTIVITY_CANCEL"):
            activity_id = _find_activity_id_by_keyword(keyword)
            if not activity_id:
                return {
                    "agent_results": [f"**Action Agent 回報**：\n找不到符合「{keyword}」的活動，麻煩提供更明確的活動名稱。"],
                    "pending_action": {},
                }

            if action_type == "ACTIVITY_REGISTER":
                dry_run = await global_ncu_session.register_for_activity_session(
                    activity_id, confirm=False
                )
                action_label = "報名"
            else:
                dry_run = await global_ncu_session.cancel_activity_registration(
                    activity_id, confirm=False
                )
                action_label = "取消報名"

            if not dry_run.get("would_click"):
                reason = dry_run.get("reason", "無法執行，請查看後端 log。")
                return {
                    "agent_results": [f"**Action Agent 回報**：\n{reason}"],
                    "pending_action": {},
                }

            detail = get_activity_detail(activity_id)
            envelope = {
                "kind": "activity_confirmation",
                "action_label": action_label,
                **detail,
            }
            return {
                "agent_results": [envelope],
                "pending_action": {
                    "type": action_type,
                    "activity_id": activity_id,
                    "session_id": None,
                },
            }

        else:
            schedule_data = await get_schedule(global_ncu_session)
            if schedule_data is not None:
                return {"agent_results": [schedule_data], "pending_action": {}}
            else:
                return {
                    "agent_results": ["**Action Agent 回報**：\n執行失敗：無法解析課表或查無資料"],
                    "pending_action": {},
                }

    except Exception as e:
        print(f"[Action Agent] 發生錯誤: {e}")
        if global_ncu_session is not None:
            await global_ncu_session.close()
            global_ncu_session = None

        return {
            "agent_results": [f"**Action Agent 回報**：\n系統執行時發生錯誤：{str(e)}"],
            "pending_action": {},
        }


def _find_activity_id_by_keyword(keyword: str) -> str | None:
    """依關鍵字搜尋活動，回傳最符合的第一筆活動的 activity_id（找不到回傳 None）。

    這是給 agent 用的：使用者通常只會講活動名稱的一部分，
    不會知道系統內部的活動編號。
    """
    if not keyword:
        return None
    matches = search_activities(keyword=keyword)
    return matches[0]["activity_id"] if matches else None


def academic_agent_node(state: AgentState):
    print("\n[Academic Agent] 被喚醒了！準備去翻找法規...")
    user_question = state["user_input"]
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"
    
    try:
        result_dict = query_academic_knowledge(user_question, history_str)
        answer_text = result_dict.get("answer", "")
        sources = result_dict.get("sources", [])
        return {
            "agent_results": [f"**Academic Agent 回報**：\n{answer_text}"],
            "sources": sources,
            "pending_action": {},
        }
    except Exception as e:
        return {
            "agent_results": [f"查詢法規時發生錯誤：{str(e)}"],
            "sources": [],
            "pending_action": {},
        }


def fallback_node(state: AgentState):
    print("\n[Fallback Agent] 被喚醒了！發現這不在系統的服務範圍內...")
    result = "我是 NCUXplore 校園助手，目前提供「校園法規查詢」、「Portal 自動化登入／課表／選課」、「個人時數進度查詢」與「活動查詢／推薦／報名」服務喔！其他問題我暫時還聽不懂～"
    return {"agent_results": [result], "pending_action": {}}


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