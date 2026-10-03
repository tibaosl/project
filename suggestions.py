"""建議問題：開場推薦問題，以及每輪回答後顯示在下面的選項。

回答後的選項有兩種，由模型看系統的回覆決定（generate_suggestions）：
- 回覆在等使用者補充資訊或做選擇（例如「請問你是哪個學院的？」「請告訴我你想找哪一門課」）
  → 給能直接回答它的選項，數量看問題決定，是非題就只給兩個。
- 已經回答完了 →「你可能還想問」的追問。

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
    # 法規查詢只能回答 data/ 裡有的文件，範例要挑文件裡查得到的（獎學金、停修目前都沒有文件）
    "search_campus_regulations": FeatureSuggestions("校園法規", False, (
        "資工系的英文畢業門檻是什麼？",
        "學生證不見了要怎麼補辦？",
        "在學證明要怎麼申請？",
        "選課是先搶先贏嗎？",
        "教研大樓的教室要怎麼借？",
        "書卷獎可以拿多少錢？",
        "停修有什麼限制？",
        "學生生病要怎麼請假？",
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
# 回答後的選項：等使用者回答時給回答選項，回答完了給「你可能還想問」
# ============================================================

MAX_REPLY_OPTIONS = 8
MAX_OPTION_LENGTH = 20
COLLEGES = "文學院、理學院、工學院、管理學院、資訊電機學院、地球科學學院、客家學院、生醫理工學院"
_QUESTION_SENTENCE = re.compile(r"[^。！!？?\n]*[？?]")
_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")

_suggestion_llm = None


def _get_suggestion_llm():
    """gpt-5.4-mini 開低推理：比過 gpt-4o-mini（同一題跑兩次結果不一致，還會把學院名稱套到
    「想找什麼課」），也比過不開推理（要使用者提供活動名稱時，6 次有 5 次會放「活動名稱」這種
    佔位選項，開低推理 12 次都正確地不給選項）。選項在回答結束後才顯示，多花一點時間沒關係。
    """
    global _suggestion_llm
    if _suggestion_llm is None:
        from langchain_openai import ChatOpenAI

        _suggestion_llm = ChatOpenAI(model="gpt-5.4-mini", reasoning_effort="low", timeout=15, max_retries=1)
    return _suggestion_llm


def describe_answer(content: Any) -> str:
    """把這輪回覆轉成給 LLM 看的描述（卡片類的結構化內容只描述類型）。
    長的回覆留開頭跟結尾，反問通常放在最後。
    """
    if isinstance(content, str):
        text = content.strip()
        return text if len(text) <= 800 else f"{text[:400]}\n……\n{text[-400:]}"
    if isinstance(content, dict) and content.get("kind"):
        extra = f"，顯示重點：{content['focus']}" if content.get("focus") else ""
        return f"（以卡片顯示結構化資料，類型：{content['kind']}{extra}）"
    if isinstance(content, list):
        return "（以表格顯示，例如課表）"
    return ""


def extract_question_to_user(reply: Any) -> str:
    """回覆最後兩行裡如果有問句，回傳最後一句，沒有就回傳空字串。

    只看結尾：反問一定放在最後（常見的是「請問你想查什麼呢？例如課表、成績。」），
    法規回答中間偶爾會引用常見問答的問句，那不是在問使用者。引號裡的問號也不算，那是在引用
    （「你要查「英文門檻」還是「外文門檻」？」結尾的問號在引號外，還是算）。
    「請告訴我你想找哪一門課」這種沒有問號的請求要靠模型判斷。
    """
    if not isinstance(reply, str):
        return ""
    lines = [line.strip() for line in reply.replace("*", "").splitlines() if line.strip()]
    # 引號裡的問號先換成別的字元，找完問句再換回來
    tail = _QUOTED.sub(lambda m: m.group(0).replace("？", "\x01").replace("?", "\x02"), "\n".join(lines[-2:]))
    questions = [q.strip(" -：:") for q in _QUESTION_SENTENCE.findall(tail) if q.strip(" -：:")]
    return questions[-1].replace("\x01", "？").replace("\x02", "?")[:150] if questions else ""


def _feature_catalog(logged_in: bool) -> str:
    return "\n".join(
        f"- {f.label}：例如「{'」「'.join(f.examples[:3])}」"
        for f in _available_features(logged_in)
    )


def _build_suggestion_prompt(
    user_message: str, answer: str, question: str, used_labels: list[str], logged_in: bool
) -> str:
    return f"""你是中央大學校園助手 NCUXplore 的介面，要在系統的回覆下面放幾個讓使用者直接點選的選項。

系統的功能（選項只能是這些功能做得到的事）：
{_feature_catalog(logged_in)}

使用者剛剛說：{user_message}
系統這輪用到的功能：{"、".join(used_labels) or "無（直接文字回覆）"}
系統的回覆：
{answer or "（無）"}
回覆結尾的問句：{question or "（沒有問句）"}

先判斷系統的回覆是不是在等使用者補充資訊或做選擇（例如反問、請使用者提供課名或活動名稱、
問要不要），再依判斷給選項。

一、在等使用者回答 → kind 填 "answers"，options 是使用者對它的回答，不是新的問題：
- 有結尾問句時，選項要直接回答那一句，不要回答使用者原本的問題
  （例如問「要我把各學院的門檻都列出來嗎？」就給「要」「不用了」，不要給學院名稱）。
- 數量看問題決定：
  - 是非題或二選一：給 2 個（例如「要」「不用了」）。
  - 答案是固定幾種（例如學院、大學部或研究所）：全部列出，最多 {MAX_REPLY_OPTIONS} 個。
  - 開放式問題：給 3 到 4 個具體的例子（例如問想找什麼課，就給「微積分」「程式設計」這種具體課名，
    不要給「必修課」「通識課」這種籠統的分類）。
- 同時問了好幾件事時，只針對最主要的一件給選項，不要排列組合（同時問學院跟學制時以學院為主，
  因為規定大多是依學院不同）。
- 要使用者提供實際存在的名稱、但你不知道實際有哪些時（例如要報名哪一場活動），options 一定給空陣列 []：
  不要自己編名稱（像「Python 入門工作坊」），也不要放「活動名稱」「工作坊名稱」這種佔位文字，
  這種選項點下去沒有用，使用者自己打字比較快。
- 每個 {MAX_OPTION_LENGTH} 字以內。系統看得到剛剛的對話，所以選項可以很短。
- 只有在問「哪個學院」時才用這些學院名稱：{COLLEGES}。
- 問使用者想查什麼時，給具體、系統做得到的事（例如「英文畢業門檻」「最近的講座」）。

二、已經回答完了 → kind 填 "follow_ups"，options 給 {FOLLOW_UP_COUNT} 個使用者接下來最可能想問的問題：
- 要跟剛才的問答相關、是自然的下一步，不要重複剛剛問過的問題。
- 每題 25 字以內，只能是上面功能做得到的問題。

選項都用使用者的口吻、繁體中文，不要編號。
只輸出 JSON，例如 {{"kind": "answers", "options": ["要", "不用了"]}}。"""


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


def parse_suggestion(text: str) -> Optional[tuple[str, list[str]]]:
    """從 LLM 回覆裡抓出 {"kind": ..., "options": [...]}；格式不對回傳 None。"""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("kind") not in ("answers", "follow_ups"):
        return None
    options = data.get("options")
    if not isinstance(options, list):
        return None
    return data["kind"], [o.strip() for o in options if isinstance(o, str) and o.strip()]


def _clean_questions(questions: list[str], user_message: str, max_length: int = MAX_QUESTION_LENGTH) -> list[str]:
    asked = user_message.strip()
    cleaned: list[str] = []
    for q in questions:
        if q != asked and q not in cleaned and len(q) <= max_length:
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


async def generate_suggestions(
    user_message: str,
    answer: Any,
    called_tools: list[str],
    logged_in: bool,
    rng: Optional[random.Random] = None,
) -> tuple[str, list[str]]:
    """回答後要顯示的選項，回傳 (種類, 選項)：
    "answers" 是回答系統反問的選項（可能是空的），"follow_ups" 是「你可能還想問」。
    """
    rng = rng or random.Random()
    used_labels = [TOOL_SUGGESTIONS[t].label for t in called_tools if t in TOOL_SUGGESTIONS]

    question = extract_question_to_user(answer)
    parsed = None
    try:
        prompt = _build_suggestion_prompt(user_message, describe_answer(answer), question, used_labels, logged_in)
        reply = await _get_suggestion_llm().ainvoke(prompt)
        parsed = parse_suggestion(reply.content)
    except Exception as exc:
        print(f"[Suggestions] 產生選項失敗，改用備案：{exc}")

    model_kind, options = parsed if parsed else (None, [])

    # 看得出來的情況用規則決定，模型只負責判斷「沒呼叫工具的直接回覆」是不是在請使用者補充
    # （例如「請告訴我你想找哪一門課」）。全部交給模型時，同一題跑幾次偶爾會判錯。
    if not isinstance(answer, str):
        kind = "follow_ups"  # 課表、時數這類卡片是已經查好的資料
    elif question:
        kind = "answers"  # 結尾有問句就是在反問
    elif called_tools:
        kind = "follow_ups"  # 工具已經回答完了（法規回答、找不到活動……）
    else:
        kind = model_kind or "follow_ups"

    if model_kind != kind:
        # 模型給的是另一種選項，不能拿來用：反問下面寧可不顯示，也不要放跟它無關的追問
        options = []

    if kind == "answers":
        return kind, _clean_questions(options, user_message, MAX_OPTION_LENGTH)[:MAX_REPLY_OPTIONS]

    questions = _clean_questions(options, user_message)
    if len(questions) < FOLLOW_UP_COUNT:
        for q in _fallback_follow_ups(user_message, called_tools, logged_in, rng):
            if q not in questions:
                questions.append(q)
    return kind, questions[:FOLLOW_UP_COUNT]
