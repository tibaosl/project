"""supervisor 選工具的測試：每題只看模型選了哪個工具（或直接回文字），不會真的執行工具，
所以不用登入也能測到需要登入的功能。需要 .env 的 OPENAI_API_KEY：

    python agent_eval/run_routing.py                          # 測 supervisor_agent 目前設定的模型
    python agent_eval/run_routing.py --holdout                # 換了說法的保留題
    python agent_eval/run_routing.py --model gpt-4o-mini --model gpt-5.4-mini:low   # 比較模型

改了 AGENT_SYSTEM_PROMPT、工具說明（agent_tools.py 的 docstring）或 supervisor 的模型之後跑一次。
新增工具時也在 CASES 補幾題會用到它的問法。

2026-09-28 的結果：gpt-4o-mini 44/48、保留題 18/21，gpt-5.4-mini（低推理）48/48、21/21，
不開推理的 gpt-4.1-mini、gpt-5.4-nano、gpt-5.4 都在 45～46 題。
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
    ("我的學習護照時數還差多少？", [], {"get_my_hours_dashboard"}), ("我的時數達到畢業門檻了嗎？", [], {"get_my_hours_dashboard"}),
    ("我報名了哪些活動？", [], {"get_my_registered_activities"}),
    ("最近有什麼講座？", [], {"search_campus_activities"}), ("有沒有志工相關的活動？", [], {"search_campus_activities"}),
    ("有沒有國際視野時數的活動？", [], {"find_activities_by_hour_category"}),
    ("哪些活動有人文藝術時數？", [], {"find_activities_by_hour_category"}),
    ("推薦我可以補時數的活動", [], {"recommend_activities_for_my_deficiencies"}),
    ("有什麼活動適合我參加？", [], {"recommend_activities_for_my_deficiencies"}),
    ("幫我報名職涯講座", [], {"preview_activity_registration"}),
    ("我要取消 AI 工作坊的報名", [], {"preview_activity_cancellation"}),
    ("職涯講座的詳細內容是什麼？", [], {"get_activity_details"}),
    ("幫我找日文課", [], {"search_course_catalog"}), ("有沒有機器學習的課？", [], {"search_course_catalog"}),
    ("我要選課", [], {NONE}),
    ("你好", [], {NONE}), ("今天天氣如何？", [], {NONE}), ("謝謝", [], {NONE}),
    ("我是物理系的", [], {NOT_CANNED}), ("我大二", [], {NOT_CANNED}),
    ("那地科學院呢？", ["文學院的外文畢業門檻多益要幾分？"], {R}), ("學費多少？", ["我是資工系的"], {R}),
    ("那我還差幾學分畢業？", ["我這學期的課表"], {"get_my_academic_analysis"}),
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
        history_str = " -> ".join(f"[使用者]: {m}" for m in history + [message])
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
