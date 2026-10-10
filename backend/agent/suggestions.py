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

from backend.language import asked_in_english, has_han
from backend.logging_config import make_print_logger

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
    "recommend_scholarships_for_me": FeatureSuggestions("獎學金推薦", True, (
        "我可以申請哪些獎學金？",
        "有什麼獎學金適合我？",
        "我的成績可以拿什麼獎學金？",
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
    "get_my_agenda": FeatureSuggestions("我的行程", True, (
        "我這週有什麼行程？",
        "明天要上什麼課？",
        "下週有哪些事要注意？",
    )),
    "get_campus_calendar": FeatureSuggestions("校曆", False, (
        "期中考是什麼時候？",
        "這學期什麼時候放寒假？",
        "這個月有放假嗎？",
        "停修申請到哪一天？",
    )),
    "export_calendar_file": FeatureSuggestions("匯出行事曆", False, (
        "可以把校曆加到我的手機行事曆嗎？",
        "怎麼把校曆匯入 Google 日曆？",
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
    # 法規查詢只能回答 data/ 裡有的文件，範例要挑文件裡查得到的（停車證這類總務處的文件目前沒有），
    # 而且要是全校學生都可能問的，不要只適用某個系（原本的「資工系的英文畢業門檻」換掉了）
    "search_campus_regulations": FeatureSuggestions("校園法規", False, (
        "導師密碼是什麼？",
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
FOLLOW_UP_CANDIDATES = 6  # 請模型多想幾題，扣掉回覆裡已經有答案的，還夠挑 3 題
MAX_QUESTION_LENGTH = 40
# 英文選項（使用者用英文問的時候）同樣的意思要多好幾倍的字母，字數上限乘上這個倍數
ENGLISH_LENGTH_FACTOR = 3


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


# 卡片上已經有哪些內容。只給模型卡片的類型時，它常常推薦卡片上就有答案的問題
# （2026-10 實測：看完課表推薦「我這學期修了哪些課」，看完學業分析推薦「我還差幾學分畢業？」）。
_CARD_CONTENTS: dict[str, Any] = {
    "academic_analysis": {
        "credits": "已修跟畢業要求的學分、各畢業類別還缺什麼、還沒通過的必修",
        "grades": "各學期平均跟班排名、系排名、累計平均跟累計排名、被當或停修的課",
        "overview": "已修跟畢業要求的學分、各畢業類別還缺什麼、被當或停修的課、各學期平均跟班系排名、累計排名",
    },
    "hours_dashboard": "學習護照每個類別、細項的時數跟畢業門檻，還差幾小時、有沒有達標",
    "scholarship_recommendations": "依成績、系所、年級找出的符合資格跟還要確認條件的獎學金，各自的金額、截止日、申請方式",
    "activity_recommendations": "依時數缺口推薦、還能報名的活動場次（時間、時數、名額）",
    "activity_tag_search": "提供這類時數、還能報名的活動場次（時間、時數、名額）",
    "activity_detail": "活動內容跟每個場次的時間、地點、時數、名額",
    "campus_calendar": "校曆上符合的事件跟日期（開始、結束日），已經過去的會標出來",
    "personal_agenda": "這段期間每天要上的課（時間、教室）、放假停課的日子、已報名的活動場次、校曆上的截止日跟考試",
    "calendar_export": "下載行事曆檔（.ics）的按鈕跟匯入 Google 日曆、手機的步驟，內容是整年校曆，有登入再加上這學期的課跟已報名的活動",
}


# 文字回覆整段給模型看，它才知道哪些已經回答過、不要再推薦。以前只給開頭跟結尾各 400 字，
# 中間寫到的（例如停修的申請期限、學生證復卡）又被推薦成追問（使用者反映）。
# 法規回答大多不到 1,500 字，選項在回答顯示完之後才產生，多看一點字不影響回答速度。
MAX_ANSWER_CHARS = 4000


def describe_answer(content: Any) -> str:
    """把這輪回覆轉成給 LLM 看的描述（卡片類的結構化內容描述類型跟卡片上已經有的內容）。
    特別長的回覆留開頭跟結尾，反問通常放在最後。
    """
    if isinstance(content, str):
        text = content.strip()
        if len(text) <= MAX_ANSWER_CHARS:
            return text
        half = MAX_ANSWER_CHARS // 2
        return f"{text[:half]}\n……\n{text[-half:]}"
    if isinstance(content, dict) and content.get("kind"):
        shown = _CARD_CONTENTS.get(content["kind"], "")
        if isinstance(shown, dict):
            shown = shown.get(content.get("focus"), "")
        extra = f"，卡片上已經有：{shown}" if shown else ""
        return f"（以卡片顯示結構化資料，類型：{content['kind']}{extra}）"
    if isinstance(content, list):
        return "（以表格顯示這學期的課表，卡片上已經有每門課的課號、課名、上課時間、地點）"
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
    # prompt 是中文的，只寫「跟使用者同一種語言」的話，英文問題的選項還是常常變成中文（2026-10-10 實測）
    language = "使用者用英文發問，選項、問題全部用英文寫。" if asked_in_english(user_message) else ""
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
- 每個 {MAX_OPTION_LENGTH} 個中文字以內（英文 {MAX_OPTION_LENGTH * ENGLISH_LENGTH_FACTOR} 個字母以內）。系統看得到剛剛的對話，所以選項可以很短。
- 只有在問「哪個學院」時才用這些學院名稱：{COLLEGES}。
- 問使用者想查什麼時，給具體、系統做得到的事（例如「英文畢業門檻」「最近的講座」）。

二、已經回答完了 → kind 填 "follow_ups"，candidates 列 {FOLLOW_UP_CANDIDATES} 個使用者接下來可能想問、
  而且系統的回覆裡沒有寫到答案的問題：
- 回覆裡寫到的事（例如期限、要帶的文件、費用、流程、找哪個單位）都不要問。
- 文字回答（例如法規）：想使用者看完之後會遇到的下一步，例如辦完之後會怎樣、沒趕上或做不到
  怎麼辦、相關的其他規定（例如問完停修的限制，問「停修會影響獎學金嗎」「停修可以取消嗎」）。
- 卡片：想使用者看完卡片之後想做的下一件事，通常是相關的其他功能（例如看完時數缺口，問
  「有什麼活動可以幫我補時數？」）。
- 不要重複剛剛問過的問題，也不要換個說法再問一次（例如看完獎學金清單又問「我還能申請哪些獎學金」）。
- 寫完每一題都要回頭檢查系統的回覆有沒有回答到：有的話把回覆裡回答它的那一句原文抄在
  answered_by，沒有就填空字串。系統的回覆是卡片時，卡片上已經有的內容也算回答到了
  （answered_by 填「卡片」）。
- 每題 25 個中文字以內（英文 75 個字母以內），只能是上面功能做得到的問題。

選項都用使用者的口吻，跟使用者用同一種語言（中文一律用繁體中文，使用者用英文問就用英文），不要編號。
只輸出 JSON，例如 {{"kind": "answers", "options": ["要", "不用了"]}}，或
{{"kind": "follow_ups", "candidates": [{{"q": "停修會影響獎學金嗎", "answered_by": ""}},
{{"q": "停修期限到什麼時候", "answered_by": "115 學年度第 1 學期的停修申請期間是 115/10/19 到 115/11/27"}}]}}。
{language}"""


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
    """從 LLM 回覆裡抓出 (種類, 選項)，格式不對回傳 None。

    回答選項是 {"kind": "answers", "options": [...]}。追問是 {"kind": "follow_ups", "candidates":
    [{"q": ..., "answered_by": ...}]}，回覆裡已經有答案（answered_by 抄了原文）的候選不採用。
    只叫模型「不要問回答過的事」擋不住（2026-10 實測：它自己列出回答過「查詢路徑」，還是推薦
    「我要怎麼查有沒有完成」），逐題抄出回答的原文它才會真的去對。
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("kind") not in ("answers", "follow_ups"):
        return None
    candidates = data.get("candidates")
    if data["kind"] == "follow_ups" and isinstance(candidates, list):
        options = [
            c.get("q") for c in candidates
            if isinstance(c, dict) and not str(c.get("answered_by") or "").strip()
        ]
    else:
        options = data.get("options")
    if not isinstance(options, list):
        return None
    return data["kind"], [o.strip() for o in options if isinstance(o, str) and o.strip()]


def _compact(text: str) -> str:
    """比對問題用：拿掉空白跟標點（「我的時數夠了嗎」跟「我的時數夠了嗎？」算同一題）。"""
    return re.sub(r"[\W_]+", "", text)


def _clean_questions(questions: list[str], user_message: str, max_length: int = MAX_QUESTION_LENGTH) -> list[str]:
    asked = _compact(user_message)
    cleaned: list[str] = []
    for q in questions:
        limit = max_length if has_han(q) else max_length * ENGLISH_LENGTH_FACTOR
        if _compact(q) != asked and q not in cleaned and len(q) <= limit:
            cleaned.append(q)
    return cleaned


def _answered_examples(answer: Any, called_tools: list[str]) -> set[str]:
    """卡片已經完整回答的功能，它的範例問題（比對用的形式）。

    2026-10 實測：看完獎學金卡片又推薦「有什麼獎學金適合我？」、看完時數又推薦「我的服務學習時數夠了嗎？」，
    跟模型說卡片上有什麼也擋不乾淨，所以直接濾掉。學業分析只有 overview 是全部都顯示，
    只問學分（credits）或成績（grades）時另一半還沒回答，不濾。校曆卡片只回答了查的那件事
    （查期中考，寒假還沒回答），也不濾。
    """
    if isinstance(answer, str):
        return set()
    if isinstance(answer, dict) and answer.get("kind") == "academic_analysis" and answer.get("focus") != "overview":
        return set()
    if isinstance(answer, dict) and answer.get("kind") == "campus_calendar":
        return set()
    return {_compact(q) for t in called_tools if t in TOOL_SUGGESTIONS for q in TOOL_SUGGESTIONS[t].examples}


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

    answered = _answered_examples(answer, called_tools)
    questions = [q for q in _clean_questions(options, user_message) if _compact(q) not in answered]
    if not questions:
        # 模型失敗、或想得到的都已經回答過了，才拿範例問題來補。模型有給一兩題就只放那幾題：
        # 補上跟這輪無關的範例（例如問完停修卻推薦「學生證不見了怎麼補辦」）反而奇怪。
        for q in _fallback_follow_ups(user_message, called_tools, logged_in, rng):
            if q not in questions and _compact(q) not in answered:
                questions.append(q)
    return kind, questions[:FOLLOW_UP_COUNT]
