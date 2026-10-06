"""supervisor 選工具的測試：每題只看模型選了哪個工具（或直接回文字），不會真的執行工具，
所以不用登入也能測到需要登入的功能。需要 .env 的 OPENAI_API_KEY：

    python agent_eval/run_routing.py                          # 測 supervisor_agent 目前設定的模型
    python agent_eval/run_routing.py --holdout                # 換了說法的保留題
    python agent_eval/run_routing.py --model gpt-4o-mini --model gpt-5.4-mini:low   # 比較模型

改了 AGENT_SYSTEM_PROMPT、工具說明（agent_tools.py 的 docstring）或 supervisor 的模型之後跑一次。
新增工具時也在 CASES 補幾題會用到它的問法。

2026-09-28 的結果：gpt-4o-mini 44/48、保留題 18/21，gpt-5.4-mini（低推理）48/48、21/21，
不開推理的 gpt-4.1-mini、gpt-5.4-nano、gpt-5.4 都在 45～46 題。
2026-10-03 加了 6 題「系統反問之後，使用者點選項回答」（歷史裡有 [系統反問]／[系統] 那句），
gpt-5.4-mini 54/54。
2026-10-06 加了獎學金推薦（開發題 5 題、保留題 3 題）：58～59/59、24/24，新加的題目都對。原本的題目裡
「幫我報名職涯講座」本來就有四成機率直接反問活動名稱（加之前跟加之後各跑 10 次都是 6 對 4），
「我的英文畢業門檻過了嗎？」偶爾會選成法規查詢，所以一次少一兩題不一定是改壞了，可以多跑幾次比較。
同一天報名、取消報名的工具加了 session 參數（指定場次），加 2 題：60/61、24/24，少的是「幫我報名職涯講座」
（跑 10 次 8 對），「11/17 那場」跑 10 次都對，也都帶了 session="11/17"。
之後 AGENT_SYSTEM_PROMPT 加了「問密碼是什麼、去哪拿是在問制度」：「導師密碼是什麼」原本 10 次有 8 次直接拒絕、
不查文件，改完 10 次都查文件。加 3 題（開發 2、保留 1）：63/63、25/25。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

import supervisor_agent  # noqa: E402
from agent_tools import build_tools  # noqa: E402

R = "search_campus_regulations"
NONE = "none"  # 不呼叫工具、直接回文字
NOT_CANNED = "none:not_canned"  # 直接回文字，而且不能是「其他問題我暫時還聽不懂」那段制式訊息
CANNED_MARK = "暫時還聽不懂"

# (這一句, 之前說過的話, 可以接受的工具)
CASES = [
    ("學生證不見了怎麼辦？", [], {R}), ("在學證明要怎麼申請？", [], {R}), ("成績單可以用 email 申請嗎？", [], {R}),
    ("資工系的英文畢業門檻是多少？", [], {R}), ("大學部一學期學雜費多少？", [], {R}), ("教室可以借嗎？", [], {R}),
    ("選課是先搶先贏嗎？", [], {R}), ("衝堂的話會怎樣？", [], {R}), ("研究生修大學部的課要繳學分費嗎？", [], {R}),
    ("博士班資格考什麼時候考？", [], {R}), ("專題指導老師確認表要交給誰？", [], {R}), ("老師請假要怎麼補課？", [], {R}),
    ("畢業門檻", [], {R, NONE}), ("畢業要修幾學分？", [], {R, "get_my_academic_analysis"}),
    ("學習護照時數的畢業門檻是幾小時？", [], {R}), ("How do I apply for an enrollment certificate?", [], {R}),
    ("外國學生要怎麼確認論文指導教授？", [], {R}), ("宿舍怎麼申請？", [], {R}),
    ("我這學期的課表", [], {"get_my_schedule"}), ("我禮拜三有什麼課？", [], {"get_my_schedule"}),
    ("我還差幾學分才能畢業？", [], {"get_my_academic_analysis"}), ("我的必修修完了嗎？", [], {"get_my_academic_analysis"}),
    ("我這學期平均幾分？", [], {"get_my_academic_analysis"}), ("我有被當的課嗎？", [], {"get_my_academic_analysis"}),
    ("我的英文畢業門檻過了嗎？", [], {"get_my_academic_analysis"}),
    ("我可以申請哪些獎學金？", [], {"recommend_scholarships_for_me"}), ("有什麼獎學金適合我？", [], {"recommend_scholarships_for_me"}),
    ("我是低收入戶，有什麼助學金可以申請？", [], {"recommend_scholarships_for_me"}),
    ("書卷獎可以拿多少錢？", [], {R}), ("羅家倫獎學金要交什麼資料？", [], {R}),
    # 問密碼「是什麼、去哪拿」是在問制度，不是要密碼（以前會直接拒絕回答）
    ("導師密碼是什麼？", [], {R}), ("課程密碼要跟誰拿？", [], {R}),
    ("我的學習護照時數還差多少？", [], {"get_my_hours_dashboard"}), ("我的時數達到畢業門檻了嗎？", [], {"get_my_hours_dashboard"}),
    ("我報名了哪些活動？", [], {"get_my_registered_activities"}),
    ("最近有什麼講座？", [], {"search_campus_activities"}), ("有沒有志工相關的活動？", [], {"search_campus_activities"}),
    ("有沒有國際視野時數的活動？", [], {"find_activities_by_hour_category"}),
    ("哪些活動有人文藝術時數？", [], {"find_activities_by_hour_category"}),
    ("推薦我可以補時數的活動", [], {"recommend_activities_for_my_deficiencies"}),
    ("有什麼活動適合我參加？", [], {"recommend_activities_for_my_deficiencies"}),
    ("幫我報名職涯講座", [], {"preview_activity_registration"}),
    ("我要取消 AI 工作坊的報名", [], {"preview_activity_cancellation"}),
    ("幫我取消客家學院學生出國說明會的報名", [], {"preview_activity_cancellation"}),  # 報名紀錄上的場次名稱
    ("職涯講座的詳細內容是什麼？", [], {"get_activity_details"}),
    ("幫我找日文課", [], {"search_course_catalog"}), ("有沒有機器學習的課？", [], {"search_course_catalog"}),
    ("我要選課", [], {NONE}),
    ("你好", [], {NONE}), ("今天天氣如何？", [], {NONE}), ("謝謝", [], {NONE}),
    ("我是物理系的", [], {NOT_CANNED}), ("我大二", [], {NOT_CANNED}),
    ("那地科學院呢？", ["文學院的外文畢業門檻多益要幾分？"], {R}), ("學費多少？", ["我是資工系的"], {R}),
    ("那我還差幾學分畢業？", ["我這學期的課表"], {"get_my_academic_analysis"}),
    # 系統反問之後，使用者點選項回答（回答通常很短，要靠歷史裡的反問才看得懂）
    ("地球科學學院", ["英文畢業門檻是多少？", "[系統反問]: 請問你是哪個學院的學生？"], {R}),
    ("要", ["英文門檻是多少？", "[系統反問]: 要我把各學院的英文畢業門檻都列出來嗎？"], {R}),
    ("微積分", ["我要選課", "[系統反問]: 請問你想找什麼課？"], {"search_course_catalog"}),
    ("日文", ["幫我選課", "[系統]: 可以，請告訴我你想找哪一門課或關鍵字，例如「微積分」、「程式設計」。"],
     {"search_course_catalog"}),
    ("看我的時數進度", ["我想查時數", "[系統反問]: 你是要看自己的時數進度，還是想知道時數畢業門檻的規定？"],
     {"get_my_hours_dashboard"}),
    ("不用了", ["學生證不見了怎麼辦？", "[系統反問]: 需要我說明悠遊卡餘額怎麼退嗎？"], {NONE}),
    # 活動有好幾個場次時系統會列出來問要哪一場
    ("11/17 那場", ["幫我報名人本AI論壇", "[系統反問]: 你要報名「115-1人本AI論壇」的哪一場？"],
     {"preview_activity_registration"}),
]

# 換了說法的保留題：平常不要照著它調 prompt，改完之後跑一次確認不是只對 CASES 有效
HOLDOUT = [
    ("幫我看一下我這學期修了哪些課", [], {"get_my_schedule"}), ("我的 GPA 多少？", [], {"get_my_academic_analysis"}),
    ("我會不會被二一？", [], {"get_my_academic_analysis"}), ("服務學習時數我還差幾小時？", [], {"get_my_hours_dashboard"}),
    ("有沒有生涯規劃時數的活動？", [], {"find_activities_by_hour_category"}),
    ("我想報名那個 Python 工作坊", [], {"preview_activity_registration"}),
    ("Python 工作坊我不想去了", [], {"preview_activity_cancellation"}), ("找一下作業系統這門課", [], {"search_course_catalog"}),
    ("學雜費什麼時候要繳？", [], {R}), ("休學要怎麼辦？", [], {R}), ("在學證明英文版多少錢？", [], {R}),
    ("工五館門禁怎麼申請？", [], {R}), ("學分抵免怎麼申請？", [], {R}), ("體育課要修幾學期？", [], {R}),
    ("我體育修完了嗎？", [], {"get_my_academic_analysis"}), ("hi", [], {NONE}), ("你會做什麼？", [], {NONE}),
    ("有沒有我拿得到的獎學金", [], {"recommend_scholarships_for_me"}),
    ("我成績不錯，可以申請什麼獎學金嗎？", [], {"recommend_scholarships_for_me"}),
    ("清寒獎學金的申請條件是什麼？", [], {R}), ("沒有導師密碼可以選課嗎？", [], {R}),
    ("我是大三的資工系學生", [], {NOT_CANNED}), ("講座類的", ["我想查活動"], {"search_campus_activities"}),
    ("那韓文呢？", ["幫我找日文課"], {"search_course_catalog"}), ("那理學院呢？", ["工學院英檢成績要交去哪裡？"], {R}),
]


def make_llm(spec: str) -> ChatOpenAI:
    """"gpt-4o-mini" 用 temperature 0；"gpt-5.4-mini:low" 這種帶推理強度的走 Responses API
    （Chat Completions 在帶工具時不能開推理）；推理強度寫 none 就走 Chat Completions。
    """
    model, _, effort = spec.partition(":")
    if not effort:
        return ChatOpenAI(model=model, temperature=0)
    if effort == "none":
        return ChatOpenAI(model=model, reasoning_effort="none")
    return ChatOpenAI(model=model, use_responses_api=True, reasoning={"effort": effort})


async def choose_tool(llm, message: str, history: list[str], semaphore) -> tuple[str, dict, str, float]:
    """組出跟 supervisor_agent._agent_turn_events 一樣的訊息，只看模型第一步的決定。"""
    async with semaphore:
        # 歷史裡的系統反問直接寫成 "[系統反問]: ..."，其他句子當成使用者說的話
        history_str = " -> ".join(m if m.startswith("[") else f"[使用者]: {m}" for m in history + [message])
        llm_with_tools = llm.bind_tools(build_tools("", "", history_str), parallel_tool_calls=False)
        messages = [
            SystemMessage(content=supervisor_agent.AGENT_SYSTEM_PROMPT.format(history_str=history_str)),
            HumanMessage(content=message),
        ]
        started = time.time()
        ai_msg = await llm_with_tools.ainvoke(messages)
        elapsed = time.time() - started
        if ai_msg.tool_calls:
            return ai_msg.tool_calls[0]["name"], ai_msg.tool_calls[0]["args"], "", elapsed
        return NONE, {}, ai_msg.text or "", elapsed


def is_correct(tool: str, reply: str, accepted: set) -> bool:
    if NOT_CANNED in accepted:
        return tool == NONE and CANNED_MARK not in reply
    return tool in accepted


async def evaluate(label: str, llm, cases: list) -> None:
    semaphore = asyncio.Semaphore(8)
    outcomes = await asyncio.gather(*(choose_tool(llm, m, h, semaphore) for m, h, _ in cases))
    wrong = []
    for (message, _, accepted), (tool, args, reply, _) in zip(cases, outcomes):
        if not is_correct(tool, reply, accepted):
            detail = f"{args}" if args else f"「{reply[:40]}」".replace("\n", " ")
            wrong.append(f"{message} → {tool} {detail}")
    seconds = sorted(o[3] for o in outcomes)
    print(f"\n{label}：正確 {len(cases) - len(wrong)}/{len(cases)}｜中位數 {seconds[len(seconds) // 2]:.2f}s｜最慢 {seconds[-1]:.2f}s")
    for line in wrong:
        print(f"  X {line}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", action="store_true", help="改跑換了說法的保留題")
    parser.add_argument("--model", action="append", default=[], help="要比較的模型，例如 gpt-4o-mini 或 gpt-5.4-mini:low")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cases = HOLDOUT if args.holdout else CASES
    if not args.model:
        await evaluate(f"目前設定（{supervisor_agent.llm_smart.model_name}）", supervisor_agent.llm_smart, cases)
    for spec in args.model:
        await evaluate(spec, make_llm(spec), cases)


if __name__ == "__main__":
    asyncio.run(main())
