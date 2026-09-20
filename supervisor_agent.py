from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
from agent_tools import build_tools, get_or_create_session, reset_session
from activity_tools import (
    recommend_activities_for_categories,
    find_activities_by_hour_tag,
)
import academic_agent
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from logging_config import make_print_logger

print = make_print_logger(__name__)

load_dotenv()
llm_smart = ChatOpenAI(model="gpt-4o-mini", temperature=0)
memory = MemorySaver()

# 對話歷史要往回看幾輪，餵給 agent 當上下文。
# 原本只取 3 輪，多輪任務（例如活動報名的確認流程、選課的來回釐清）
# 很容易就把最相關的那句話擠出視窗，所以放大一點。
HISTORY_WINDOW = 12

# 單輪對話最多讓 LLM 呼叫幾輪工具（目前每個工具都會直接產生最終回覆，
# 正常情況下第一輪就會結束；保留這個上限只是給未來可能需要串接多個
# 工具的情境一個安全網，避免無限迴圈）。
MAX_TOOL_ROUNDS = 4

# 「確認/繼續」這類回覆用簡單關鍵字判斷，不靠 LLM（見 agent_node 開頭的
# 說明）。這組關鍵字要在呼叫 LLM 之前就攔截，才能確保「會真的改動學校
# 系統資料」的動作只在使用者明確表態時才會發生，行為要可預期，不交給
# 模型自己判斷要不要送出。
CONFIRM_KEYWORDS = ["確定"]
CONTINUE_ALL_KEYWORDS = ["全部列出", "列出全部", "都列出", "全部顯示", "都給我"]
CONTINUE_MORE_KEYWORDS = ["繼續", "更多", "還有", "再多", "再幾個", "再找"]

AGENT_SYSTEM_PROMPT = """\
你是中央大學 NCUXplore 系統的校園助手，服務全校師生。你可以呼叫提供給你的工具來
查課表、選課、查詢時數進度、查詢/推薦/報名活動，或查詢校園法規。

【對話歷史】：{history_str}

【原則】：
1. 只有在確定使用者要問「自己的」個人資料/操作（課表、時數進度、選課、活動報名）
   時才呼叫對應工具；問的是「規則/門檻/費用本身」這種制度規則，才呼叫
   search_campus_regulations。
2. 每輪對話通常只需要呼叫一個工具，不要沒必要地一次呼叫多個工具。
3. 如果問題範圍太大、缺乏關鍵資訊（例如法規問題沒講系所/學制、選課沒講要選什麼課），
   不要亂猜著呼叫工具，直接用親切的語氣回覆文字，請使用者補充細節。
4. 如果問題明顯跟中央大學校園服務無關（純閒聊、打招呼、無意義字詞），不要呼叫任何
   工具，直接回覆：「我是 NCUXplore 校園助手，目前提供「校園法規查詢」、「Portal
   自動化登入／課表／選課」、「個人時數進度查詢」與「活動查詢／推薦／報名」服務喔！
   其他問題我暫時還聽不懂～」
5. 報名/取消報名一律只能呼叫 preview_ 開頭的工具做預覽，你沒有辦法、也不應該嘗試
   真的送出；使用者確認後系統會自動處理送出，不需要你再呼叫任何工具完成送出。
"""


class AgentState(TypedDict):
    user_input: str
    username: str
    password: str
    agent_results: Annotated[List[str], operator.add]
    past_queries: Annotated[List[str], operator.add]
    sources: List[str]
    # 用來記住「上一輪請使用者確認是否要報名/取消報名某活動」這件事，
    # 讓確認動作可以跨輪對話（不用一次講完）。沒有 Annotated reducer，
    # 所以每個節點回傳時要嘛明確清空（{}），要嘛明確設成新的待確認動作，
    # 否則舊的待確認動作會一直留著（checkpointer 只覆蓋有回傳的欄位）。
    pending_action: dict
    # 這一輪實際呼叫了哪些工具（或走了哪個非 LLM 的攔截分支），只給
    # debug_info 用；每輪都要明確回傳（覆蓋掉上一輪的值，不是累加）。
    called_tools: List[str]


def _summarize_tool_result(result: dict) -> str:
    """把工具回傳的 content 轉成適合塞進 ToolMessage 的字串。

    目前每個工具都會直接產生最終回覆（agent_node 執行完就結束該輪，
    不會把這個字串再餵回 LLM），這裡單純是為了讓訊息序列合法、並給
    未來可能需要串接多個工具的情境一個看得懂的摘要。
    """
    content = result.get("content")
    if isinstance(content, str):
        return content[:2000]
    return "工具已回傳結構化資料，會直接顯示給使用者，不需要再摘要。"


async def _agent_turn_events(user_input: str, username: str, password: str, pending_action: dict, history_str: str):
    """一輪對話的核心邏輯，用事件流表達，兩個呼叫端共用：

    - `agent_node`（LangGraph 用，`/api/chat` 非串流路徑）：把這裡吐出來的
      事件收集成最終的 state dict，中間的 status/token 事件直接丟棄。
    - `run_ncuxplore_agent_stream`（SSE 串流路徑）：原封不動把事件往外
      轉發給前端。

    這樣「該呼叫哪個工具、安全機制怎麼攔截」只有一份邏輯，不會因為要
    支援串流又長出一份幾乎一樣、但容易跟原本那份長歪的複本。

    依序可能 yield 的事件（都是 dict，呼叫端自己決定怎麼用）：
    - {"type": "status", "text": "..."}
    - {"type": "token", "text": "..."}                         # 僅法規查詢這條路會有
    - {"type": "result", "content": ...}                       # 某個工具/攔截分支的最終內容
    - {"type": "final", "agent_results": [...], "sources": [...],
       "pending_action": {...}, "called_tools": [...]}          # 一定是最後一個事件
    """
    print(f"\n[Agent] 收到使用者需求：「{user_input}」")
    print(f"[Agent] 參考對話歷史：「{history_str}」")

    # ------------------------------------------------------------------
    # 確認送出：如果上一輪有等待確認的報名/取消報名動作，而這一輪的輸入
    # 裡有「確定」兩個字，就直接執行，不經過 LLM。
    #
    # 這是刻意寫死、不讓 LLM 判斷的安全機制：會改變學校系統資料的動作，
    # 只在使用者明確表態時才會發生，行為要可預期。改成 tool use 之後這點
    # 完全不變——LLM 唯一能呼叫到的報名/取消工具永遠只會 dry-run 預覽，
    # 真正送出只會經過這段程式碼。
    # ------------------------------------------------------------------
    if pending_action and any(kw in user_input for kw in CONFIRM_KEYWORDS):
        print("[Agent] 偵測到針對 pending_action 的確認回覆，直接送出。")

        if not username:
            content = "[Action Agent 回報]:\n缺乏帳號，無法執行。請先登入 Portal 帳號密碼！"
            yield {"type": "result", "content": content}
            yield {"type": "final", "agent_results": [content], "sources": [], "pending_action": {}, "called_tools": []}
            return

        yield {"type": "status", "text": "正在送出..."}
        action_type = pending_action.get("type")
        activity_id = pending_action.get("activity_id")
        session_id = pending_action.get("session_id")

        try:
            # ⚠️ token 登入流程下 password 一律是空字串（真正的密碼只在登入
            # 當下驗證過一次，見 main.py 的 /api/login、agent_tools.py 的
            # get_or_create_session 說明），所以這裡不能像以前一樣直接把
            # 「password 是空字串」當成「沒登入」——要看 get_or_create_session
            # 有沒有真的拿到現成 session（沒有現成 session 又沒帶密碼，才是
            # 真的沒登入）。
            session = await get_or_create_session(username, password)

            if session is None:
                content = "[Action Agent 回報]:\n缺乏帳號或密碼，無法執行。請先登入 Portal 帳號密碼！"
                yield {"type": "result", "content": content}
                yield {
                    "type": "final", "agent_results": [content], "sources": [], "pending_action": {},
                    "called_tools": [],
                }
                return

            if action_type == "ACTIVITY_REGISTER":
                result = await session.register_for_activity_session(
                    activity_id, session_id=session_id, confirm=True
                )
            elif action_type == "ACTIVITY_CANCEL":
                result = await session.cancel_activity_registration(
                    activity_id, session_id=session_id, confirm=True
                )
            else:
                result = {"message": "沒有找到待確認的動作，請重新告訴我想做什麼。"}

            content = f"**Action Agent 回報**：\n{result.get('message', '')}"
            yield {"type": "result", "content": content}
            yield {
                "type": "final", "agent_results": [content], "sources": [], "pending_action": {},
                "called_tools": [f"__confirm__:{action_type}"],
            }
        except Exception as e:
            print(f"[Agent] 確認動作執行時發生錯誤: {e}")
            await reset_session(username)
            content = f"**Action Agent 回報**：\n系統執行時發生錯誤：{str(e)}"
            yield {"type": "result", "content": content}
            yield {
                "type": "final", "agent_results": [content], "sources": [], "pending_action": {},
                "called_tools": [f"__confirm__:{action_type}"],
            }
        return

    # ------------------------------------------------------------------
    # 「繼續 / 還要更多 / 全部列出」：活動推薦、依標籤查詢預設只找滿 5 筆
    # 就停止，使用者想看更多時從上次停下的位置接續掃描。不需要登入，
    # 同樣用簡單關鍵字判斷、不經過 LLM，行為才可預期。
    # ------------------------------------------------------------------
    continuation_pending_types = ("ACTIVITY_RECOMMEND", "ACTIVITY_SEARCH_BY_TAG")
    wants_all = any(kw in user_input for kw in CONTINUE_ALL_KEYWORDS)
    wants_more = wants_all or any(kw in user_input for kw in CONTINUE_MORE_KEYWORDS)

    if pending_action.get("type") in continuation_pending_types and wants_more:
        yield {"type": "status", "text": "正在接續查詢..."}
        tag_names = pending_action.get("tag_names", [])
        deficiencies = pending_action.get("deficiencies")
        resume_indices = pending_action.get("next_indices", {})
        next_limit = None if wants_all else 5

        try:
            if pending_action["type"] == "ACTIVITY_RECOMMEND":
                more_matches, next_indices, exhausted_by_tag = recommend_activities_for_categories(
                    deficiencies, limit_per_tag=next_limit, start_indices=resume_indices
                )
                envelope_kind = "activity_recommendations"
            else:
                more_matches, next_indices, exhausted_by_tag = find_activities_by_hour_tag(
                    tag_names, limit_per_tag=next_limit, start_indices=resume_indices
                )
                envelope_kind = "activity_tag_search"

            has_more = (not wants_all) and not all(exhausted_by_tag.values())

            envelope = {
                "kind": envelope_kind,
                "recommendations": more_matches,
                "exhausted_by_tag": exhausted_by_tag,
                "has_more": has_more,
            }

            new_pending_action = (
                {}
                if not has_more
                else {**pending_action, "next_indices": next_indices}
            )

            yield {"type": "result", "content": envelope}
            yield {
                "type": "final", "agent_results": [envelope], "sources": [], "pending_action": new_pending_action,
                "called_tools": [f"__continue__:{pending_action['type']}"],
            }
        except Exception as e:
            content = f"**Action Agent 回報**：\n查詢活動時發生錯誤：{str(e)}"
            yield {"type": "result", "content": content}
            yield {
                "type": "final", "agent_results": [content], "sources": [], "pending_action": {},
                "called_tools": [f"__continue__:{pending_action['type']}"],
            }
        return

    # ------------------------------------------------------------------
    # 其餘情況：交給 tool-calling agent 自己從完整工具清單裡挑要呼叫哪個
    # （取代原本 supervisor_node 的意圖分類決策樹 + action_agent_node 的
    # JSON 動作抽取 prompt）。日後新增功能只要在 agent_tools.py 加一個
    # @tool 函式，不需要再動這裡的 prompt。
    # ------------------------------------------------------------------
    yield {"type": "status", "text": "正在理解你的問題..."}
    tools = build_tools(username, password, history_str)
    # parallel_tool_calls=False：沒有這個，模型偶爾會對同一個問題（尤其是
    # 短、承接上一輪對話的追問，例如「客家系呢」）一次發出好幾個
    # search_campus_regulations 呼叫、每個問法都不太一樣，每個都要跑一次
    # 完整的 RAG 流程（intent 判斷 + 多組 embedding + LLM rerank + 答案
    # 生成），一輪對話從十幾秒變成快兩分鐘，卻只有最後一個結果會顯示
    # 出來——等於使用者白等了前面幾次。強制一輪只能呼叫一個工具，不夠
    # 精準的話讓 academic_agent 自己的 query 擴寫/多變體檢索去處理，不需要
    # 靠 LLM 重複呼叫同一個工具來「多試幾次」。
    llm_with_tools = llm_smart.bind_tools(tools, parallel_tool_calls=False)
    tools_by_name = {t.name: t for t in tools}

    messages = [
        SystemMessage(content=AGENT_SYSTEM_PROMPT.format(history_str=history_str)),
        HumanMessage(content=user_input),
    ]

    agent_results: list = []
    sources: list = []
    new_pending_action: dict = {}
    called_tools: list = []

    for _ in range(MAX_TOOL_ROUNDS):
        ai_msg = await llm_with_tools.ainvoke(messages)

        if not ai_msg.tool_calls:
            if ai_msg.content:
                agent_results.append(ai_msg.content)
                yield {"type": "token", "text": ai_msg.content}
            break

        messages.append(ai_msg)
        stop = False

        for tool_call in ai_msg.tool_calls:
            name = tool_call["name"]
            tool_fn = tools_by_name.get(name)
            if tool_fn is None:
                messages.append(
                    ToolMessage(content="找不到這個工具。", tool_call_id=tool_call["id"])
                )
                continue

            print(f"[Agent] 呼叫工具：{name}({tool_call['args']})")
            called_tools.append(name)

            # search_campus_regulations 改走 academic_agent 的串流版本，
            # 逐字把答案吐出去——這是整條流程裡最慢、也最適合逐字呈現的
            # 一步。其他工具回傳的是結構化資料（課表/活動卡片/時數進度），
            # 本來就不是「用打字機效果呈現」的東西，維持一次性回傳即可。
            if name == "search_campus_regulations":
                query = tool_call["args"].get("query", user_input)
                answer_parts: list[str] = []
                result_sources: list = []
                try:
                    for event in academic_agent.query_academic_knowledge_stream(query, history_str):
                        if event["type"] == "status":
                            yield event
                        elif event["type"] == "token":
                            answer_parts.append(event["text"])
                            yield event
                        elif event["type"] == "sources":
                            result_sources = event["sources"]
                        elif event["type"] == "error":
                            answer_parts.append(f"\n（{event['message']}）")
                except Exception as e:
                    print(f"[Agent] search_campus_regulations 串流失敗：{e}")
                    answer_parts.append(f"查詢法規時發生錯誤：{e}")

                content = f"**Academic Agent 回報**：\n{''.join(answer_parts)}"
                agent_results.append(content)
                sources = result_sources
                stop = True
                messages.append(ToolMessage(content=content[:2000], tool_call_id=tool_call["id"]))
                continue

            yield {"type": "status", "text": f"正在執行 {name}..."}
            try:
                result = await tool_fn.ainvoke(tool_call["args"])
            except Exception as e:
                print(f"[Agent] 工具 {name} 執行失敗：{e}")
                result = {"content": f"執行 {name} 時發生錯誤：{e}"}

            if result.get("pending_action") is not None:
                new_pending_action = result["pending_action"]
            if result.get("sources"):
                sources = result["sources"]
            if "content" in result:
                agent_results.append(result["content"])
                yield {"type": "result", "content": result["content"]}
                stop = True

            messages.append(
                ToolMessage(content=_summarize_tool_result(result), tool_call_id=tool_call["id"])
            )

        if stop:
            break
    else:
        content = "這個問題牽涉的步驟比較多，麻煩換個方式，或是分開問我，謝謝！"
        agent_results.append(content)
        yield {"type": "result", "content": content}

    if not agent_results:
        agent_results = ["抱歉，我沒有得到明確的回覆，請換個方式再問一次。"]

    yield {
        "type": "final",
        "agent_results": agent_results,
        "sources": sources,
        "pending_action": new_pending_action,
        "called_tools": called_tools,
    }


async def agent_node(state: AgentState):
    user_input = state["user_input"]
    username = state.get("username", "")
    password = state.get("password", "")
    pending_action = state.get("pending_action") or {}
    history = state.get("past_queries", [])
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"

    async for event in _agent_turn_events(user_input, username, password, pending_action, history_str):
        if event["type"] == "final":
            return {
                "agent_results": event["agent_results"],
                "sources": event["sources"],
                "pending_action": event["pending_action"],
                "called_tools": event["called_tools"],
            }

    # _agent_turn_events 保證一定會 yield 一個 "final" 事件才結束；
    # 理論上不會走到這裡，留著純粹是型別/防呆考量。
    return {"agent_results": ["抱歉，我沒有得到明確的回覆，請換個方式再問一次。"], "sources": [], "pending_action": {}, "called_tools": []}


workflow = StateGraph(AgentState)
workflow.add_node("agent_node", agent_node)
workflow.set_entry_point("agent_node")
workflow.add_edge("agent_node", END)

app = workflow.compile(checkpointer=memory)


async def run_ncuxplore_agent(user_message: str, username: str = "", password: str = "", thread_id: str = "default_session"):
    initial_state = {
        "user_input": user_message,
        "username": username,
        "password": password,
        "agent_results": [],
        "past_queries": [f"[使用者]: {user_message}"],
        "sources": [],
        "called_tools": [],
    }
    config = {"configurable": {"thread_id": thread_id}}
    final_state = await app.ainvoke(initial_state, config=config)
    return final_state


async def run_ncuxplore_agent_stream(user_message: str, username: str = "", password: str = "", thread_id: str = "default_session"):
    """`run_ncuxplore_agent()` 的串流版本，給 SSE 端點用。

    不透過 `app.ainvoke()`（那個是「整段跑完才回傳」，沒辦法邊跑邊把中間
    事件吐出去）；改成自己讀/寫同一個 checkpointer 的狀態
    （`app.aget_state`/`app.aupdate_state`），拿到跟非串流版本一致的
    `pending_action`/對話歷史，實際的判斷/工具呼叫邏輯則是跟 `agent_node`
    共用同一個 `_agent_turn_events()`，不會有兩份容易長歪的複本。

    收尾時用 `app.aupdate_state()` 把這輪結果寫回同一個 checkpointer——
    只要 thread_id 一樣，這輪不管是走串流還是非串流呼叫的，下一輪都能
    接續看到正確的對話歷史/pending_action。

    依序 yield 跟 `_agent_turn_events()` 一樣的事件，額外保證最後一定會
    有一個 `{"type": "done"}` 收尾（串流端點可以拿這個當作關閉連線的訊號）。
    """
    config = {"configurable": {"thread_id": thread_id}}

    try:
        snapshot = await app.aget_state(config)
        prior = snapshot.values if snapshot else {}
    except Exception as e:
        print(f"[Agent-Stream] 讀取既有對話狀態失敗（視為全新對話）：{e}")
        prior = {}

    pending_action = prior.get("pending_action") or {}
    # ⚠️ 這裡刻意把「這一輪自己的訊息」也併進歷史字串（不是只給上一輪
    # 為止的舊歷史）：因為非串流那條路（agent_node 由 app.ainvoke() 呼叫）
    # 剛好會有這個行為——LangGraph 的 checkpointer 在節點真正執行「之前」
    # 就先用 reducer 把 run_ncuxplore_agent() 傳進去的 initial_state 併回
    # 既有狀態，所以 agent_node 讀到的 past_queries 其實已經包含這一輪自己
    # 的訊息。這行為本身沒被特別設計過，但已經是現行、使用者一直在用、
    # 也驗證過好用的行為——拿掉「客家系呢」這種很短的追問句就會查不清楚
    # 上下文、變成反問使用者（已經用真實案例反覆測試驗證過這個落差）。
    # 這裡讓串流路徑刻意複製同樣的歷史語意，兩條路徑对同一句話的判斷才會一致。
    history = prior.get("past_queries", []) + [f"[使用者]: {user_message}"]
    history_str = " -> ".join(history[-HISTORY_WINDOW:]) if history else "無"

    try:
        async for event in _agent_turn_events(user_message, username, password, pending_action, history_str):
            if event["type"] == "final":
                # as_node="agent_node"：LangGraph 的 aupdate_state 需要知道
                # 這次更新要套用哪個節點的 channel 規則，不然即使圖上只有
                # 一個節點也會直接丟 "Ambiguous update, specify as_node"，
                # 整個更新完全沒寫入、下一輪對話歷史會是空的（已實測踩過）。
                await app.aupdate_state(
                    config,
                    {
                        "user_input": user_message,
                        "username": username,
                        "password": password,
                        "agent_results": event["agent_results"],
                        "past_queries": [f"[使用者]: {user_message}"],
                        "sources": event["sources"],
                        "pending_action": event["pending_action"],
                        "called_tools": event["called_tools"],
                    },
                    as_node="agent_node",
                )
                if event["sources"]:
                    yield {"type": "sources", "sources": event["sources"]}
            else:
                yield event
    except Exception as e:
        print(f"[Agent-Stream] 執行時發生未預期錯誤：{e}")
        yield {"type": "error", "message": str(e)}

    yield {"type": "done"}
