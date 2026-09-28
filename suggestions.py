"""建議問題：開場推薦問題，以及每輪回答後的「你可能還想問」。

每個 agent 工具都在 TOOL_SUGGESTIONS 登記一筆：前端顯示用的功能名稱、需不需要登入、
幾個範例問題。新增工具時在這裡補一筆（test_agent_tools_schema.py 會檢查有沒有漏），
開場推薦跟追問就會自動涵蓋新功能，前端不用改。範例留空代表這個工具不適合拿來
當推薦問題（例如要先指定某個活動才能用的報名預覽）。
"""

import json
import random
import re
from dataclasses import dataclass
from typing import Any, Optional

from logging_config import make_print_logger

print = make_print_logger(__name__)


@dataclass(frozen=True)
class FeatureSuggestions:
    label: str
    requires_login: bool
    examples: tuple[str, ...] = ()


TOOL_SUGGESTIONS: dict[str, FeatureSuggestions] = {
    "get_my_academic_analysis": FeatureSuggestions("學業分析", True, (
        "我還差幾學分畢業？",
        "我的必修都修完了嗎？",
        "我的累計排名是多少？",
        "我有沒有需要重修的課？",
        "幫我整體分析一下學業狀況",
    )),
    "get_my_schedule": FeatureSuggestions("課表", True, (
        "我這學期的課表",
        "我這學期修了哪些課？",
    )),
    # 選課系統要另外用帳密登入，Chrome 登入沒有密碼、非選課階段也搜不到，
    # 課程搜尋改好之前先不推薦（改好後補回例如「幫我找微積分的課」）
    "search_course_catalog": FeatureSuggestions("課程搜尋", True),
    "get_my_hours_dashboard": FeatureSuggestions("學習護照時數", True, (
        "我的學習護照時數還差多少？",
        "我的時數達到畢業門檻了嗎？",
        "我的服務學習時數夠了嗎？",
    )),
    "recommend_activities_for_my_deficiencies": FeatureSuggestions("活動推薦", True, (
        "有什麼活動可以幫我補時數？",
        "推薦我適合報名的活動",
    )),
    "get_my_registered_activities": FeatureSuggestions("我的報名", True, (
        "我報名了哪些活動？",
        "查一下我的活動報名紀錄",
    )),
    # 活動搜尋是比對活動標題的關鍵字，範例要挑常出現在標題裡的類別（藝文、英文常常查不到）
    "search_campus_activities": FeatureSuggestions("校園活動", False, (
        "有沒有講座類的活動？",
        "最近有什麼工作坊？",
        "有沒有志工相關的活動？",
    )),
    "find_activities_by_hour_category": FeatureSuggestions("時數活動", False, (
        "有沒有國際視野時數的活動？",
        "哪些活動有人文藝術時數？",
        "有提供自我探索與生涯規劃時數的活動嗎？",
    )),
    "search_campus_regulations": FeatureSuggestions("校園法規", False, (
        "資工系的英文畢業門檻是什麼？",
        "獎學金要怎麼申請？",
        "停修的規定和期限是什麼？",
        "教室場地借用要怎麼申請？",
    )),
    "get_activity_details": FeatureSuggestions("活動詳情", False),
    "preview_activity_registration": FeatureSuggestions("活動報名", True),
    "preview_activity_cancellation": FeatureSuggestions("取消報名", True),
}

STARTER_COUNT = 6
FOLLOW_UP_COUNT = 3
MAX_QUESTION_LENGTH = 40


def _available_features(logged_in: bool) -> list[FeatureSuggestions]:
    return [
        f for f in TOOL_SUGGESTIONS.values()
        if f.examples and (logged_in or not f.requires_login)
    ]


def pick_starter_questions(
    logged_in: bool,
    count: int = STARTER_COUNT,
    rng: Optional[random.Random] = None,
    exclude: frozenset[str] = frozenset(),
) -> list[dict[str, str]]:
    """隨機挑開場推薦問題：先讓每個功能各出一題（功能夠多就只挑其中幾個），
    題目不夠才讓同一個功能再出第二題，盡量讓使用者一眼看到不同功能。

    `exclude` 是畫面上正在顯示的題目（「換一批」用）：優先挑沒出現過的，
    題目不夠時才拿來補。
    """
    rng = rng or random.Random()
    features = _available_features(logged_in)
    rng.shuffle(features)
    shuffled = {}
    for f in features:
        examples = rng.sample(f.examples, len(f.examples))
        shuffled[f.label] = [q for q in examples if q not in exclude] + [q for q in examples if q in exclude]
    # 還有新題目的功能排前面，不然「換一批」會先被只剩舊題目的功能佔走名額
    features.sort(key=lambda f: shuffled[f.label][0] in exclude)

    picks: list[dict[str, str]] = []
    for round_index in range(max((len(f.examples) for f in features), default=0)):
        for feature in features:
            if len(picks) == count:
                return picks
            examples = shuffled[feature.label]
            if round_index < len(examples):
                picks.append({"question": examples[round_index], "label": feature.label})
    return picks


# ============================================================
# 追問（你可能還想問）
# ============================================================

_follow_up_llm = None


def _get_follow_up_llm():
    global _follow_up_llm
    if _follow_up_llm is None:
        from langchain_openai import ChatOpenAI

        _follow_up_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, timeout=10, max_retries=1)
    return _follow_up_llm


def describe_answer(content: Any) -> str:
    """把這輪回覆轉成給 LLM 看的簡短描述（卡片類的結構化內容只描述類型）。"""
    if isinstance(content, str):
        return content.strip()[:600]
    if isinstance(content, dict) and content.get("kind"):
        extra = f"，顯示重點：{content['focus']}" if content.get("focus") else ""
        return f"（以卡片顯示結構化資料，類型：{content['kind']}{extra}）"
    if isinstance(content, list):
        return "（以表格顯示，例如課表）"
    return ""


def _feature_catalog(logged_in: bool) -> str:
    return "\n".join(
        f"- {f.label}：例如「{'」「'.join(f.examples[:3])}」"
        for f in _available_features(logged_in)
    )


def _build_follow_up_prompt(user_message: str, answer: str, used_labels: list[str], logged_in: bool) -> str:
    return f"""你是中央大學校園助手 NCUXplore 的介面，要在回答後提供「你可能還想問」的追問建議。

系統目前能處理的功能（只能建議這些功能做得到的問題）：
{_feature_catalog(logged_in)}

使用者剛剛問：{user_message}
系統這輪用到的功能：{"、".join(used_labels) or "無（直接文字回覆）"}
回答內容摘要：{answer or "（無）"}

請給出 {FOLLOW_UP_COUNT} 個使用者接下來最可能想問的問題：
- 要跟剛才的問答相關、是自然的下一步，不要重複剛剛問過的問題
- 用使用者的口吻、繁體中文、每題 25 字以內，不要編號
- 只能是上面功能做得到的問題

只輸出 JSON 字串陣列，例如 ["問題一", "問題二", "問題三"]。"""


def parse_question_list(text: str) -> list[str]:
    """從 LLM 回覆裡抓出 JSON 字串陣列；格式不對就回傳空陣列。"""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [q.strip() for q in data if isinstance(q, str) and q.strip()]


def _clean_questions(questions: list[str], user_message: str) -> list[str]:
    asked = user_message.strip()
    cleaned: list[str] = []
    for q in questions:
        if q != asked and q not in cleaned and len(q) <= MAX_QUESTION_LENGTH:
            cleaned.append(q)
    return cleaned


def _fallback_follow_ups(
    user_message: str, called_tools: list[str], logged_in: bool, rng: random.Random
) -> list[str]:
    """LLM 失敗時的備案：先挑這輪用到的功能的其他範例，再從其他功能隨機補。"""
    available = _available_features(logged_in)
    related = [TOOL_SUGGESTIONS[t] for t in called_tools if TOOL_SUGGESTIONS.get(t) in available]
    others = [f for f in available if f not in related]
    rng.shuffle(others)

    candidates: list[str] = []
    for feature in related + others:
        candidates.extend(rng.sample(feature.examples, len(feature.examples)))
    return _clean_questions(candidates, user_message)


async def generate_follow_up_questions(
    user_message: str,
    answer: Any,
    called_tools: list[str],
    logged_in: bool,
    rng: Optional[random.Random] = None,
) -> list[str]:
    rng = rng or random.Random()
    used_labels = [TOOL_SUGGESTIONS[t].label for t in called_tools if t in TOOL_SUGGESTIONS]

    questions: list[str] = []
    try:
        prompt = _build_follow_up_prompt(user_message, describe_answer(answer), used_labels, logged_in)
        reply = await _get_follow_up_llm().ainvoke(prompt)
        questions = _clean_questions(parse_question_list(reply.content), user_message)
    except Exception as exc:
        print(f"[Suggestions] 產生追問失敗，改用範例問題：{exc}")

    if len(questions) < FOLLOW_UP_COUNT:
        for q in _fallback_follow_ups(user_message, called_tools, logged_in, rng):
            if q not in questions:
                questions.append(q)
    return questions[:FOLLOW_UP_COUNT]
