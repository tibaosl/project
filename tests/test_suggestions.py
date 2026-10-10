"""建議問題的挑選/解析/備案邏輯測試，不需要 OpenAI key：
    python -m pytest tests/test_suggestions.py
"""

import asyncio
import json
import random

from backend.agent import suggestions
from backend.agent.suggestions import (
    TOOL_SUGGESTIONS,
    extract_question_to_user,
    generate_suggestions,
    parse_question_list,
    parse_suggestion,
    pick_starter_questions,
)

PUBLIC_LABELS = {f.label for f in TOOL_SUGGESTIONS.values() if f.examples and not f.requires_login}


def test_guest_only_gets_features_that_do_not_need_login():
    for seed in range(20):
        picks = pick_starter_questions(logged_in=False, rng=random.Random(seed))
        assert picks
        assert {p["label"] for p in picks} <= PUBLIC_LABELS


def test_starter_questions_cover_different_features_before_repeating():
    picks = pick_starter_questions(logged_in=True, count=6, rng=random.Random(1))
    labels = [p["label"] for p in picks]

    assert len(picks) == 6
    assert len(set(labels)) == 6
    assert len({p["question"] for p in picks}) == 6


def test_starter_questions_vary_between_calls():
    sets = {
        tuple(p["question"] for p in pick_starter_questions(logged_in=True, rng=random.Random(seed)))
        for seed in range(10)
    }
    assert len(sets) > 1


def test_refresh_prefers_questions_not_currently_shown():
    for seed in range(20):
        first = pick_starter_questions(logged_in=True, rng=random.Random(seed))
        shown = frozenset(p["question"] for p in first)
        second = pick_starter_questions(logged_in=True, rng=random.Random(seed + 100), exclude=shown)

        # 登入後題庫夠大，換一批應該完全不重複，而且一樣是 6 題不同功能
        assert not shown & {p["question"] for p in second}
        assert len({p["label"] for p in second}) == 6


def test_refresh_reuses_shown_questions_only_when_pool_runs_out():
    all_public = frozenset(q for f in TOOL_SUGGESTIONS.values() if not f.requires_login for q in f.examples)
    picks = pick_starter_questions(logged_in=False, rng=random.Random(0), exclude=all_public)

    assert len(picks) == 6


def test_features_without_examples_are_never_suggested():
    empty = {f.label for f in TOOL_SUGGESTIONS.values() if not f.examples}
    for seed in range(20):
        picks = pick_starter_questions(logged_in=True, count=30, rng=random.Random(seed))
        assert not empty & {p["label"] for p in picks}


def test_parse_question_list_handles_code_fences_and_garbage():
    assert parse_question_list('```json\n["一", "二", " "]\n```') == ["一", "二"]
    assert parse_question_list("好的：[\"A\", 3, \"B\"]") == ["A", "B"]
    assert parse_question_list("沒有 JSON") == []
    assert parse_question_list("[壞掉的 json") == []


def test_parse_suggestion_accepts_only_known_kinds():
    assert parse_suggestion('```json\n{"kind": "answers", "options": ["要", 3, " "]}\n```') == ("answers", ["要"])
    assert parse_suggestion('{"kind": "follow_ups", "options": []}') == ("follow_ups", [])
    assert parse_suggestion('{"kind": "other", "options": ["要"]}') is None
    assert parse_suggestion('{"kind": "answers"}') is None
    assert parse_suggestion("沒有 JSON") is None


def test_extract_question_to_user_takes_the_question_at_the_end():
    assert extract_question_to_user("請問你是哪個學院的學生？") == "請問你是哪個學院的學生？"
    # 反問後面常常會補例子，例子那句不是問句
    assert extract_question_to_user("請問你想查詢什麼呢？例如課表、成績。") == "請問你想查詢什麼呢？"
    assert extract_question_to_user("資工系屬於資電學院。\n\n**你是 114 學年前還是 115 學年後入學的？**") == (
        "你是 114 學年前還是 115 學年後入學的？"
    )
    assert extract_question_to_user("- 要我把各學院的門檻都列出來嗎？") == "要我把各學院的門檻都列出來嗎？"


def test_extract_question_to_user_ignores_answers_and_cards():
    # 法規回答中間引用常見問答的問句，不是在問使用者
    answer = "依選課問答集：\n- 選課是先搶先贏嗎？不是。\n- 初選截止後以亂數籤號分發。\n- 加退選第 3 天起每天分發一次。"
    assert extract_question_to_user(answer) == ""
    # 引號裡的問句是在引用，就算在最後兩行也不算
    assert extract_question_to_user("不是先搶先贏。\n依選課問答集「選課是先登錄先錄取嗎？不是的。」") == ""
    # 問號在引號外就算反問，回傳的句子要保留引號裡的字
    assert extract_question_to_user("你要查「英文門檻」還是「外文門檻」？") == "你要查「英文門檻」還是「外文門檻」？"
    assert extract_question_to_user({"kind": "hours_dashboard"}) == ""
    assert extract_question_to_user("") == ""


class _FakeLLM:
    def __init__(self, content=None, error=None):
        self.content, self.error = content, error

    async def ainvoke(self, prompt):
        if self.error:
            raise self.error
        return type("Reply", (), {"content": self.content})()


def _run(monkeypatch, llm, user_message="英文門檻是多少？", answer="", called_tools=("search_campus_regulations",),
         logged_in=False):
    monkeypatch.setattr(suggestions, "_get_suggestion_llm", lambda: llm)
    return asyncio.run(generate_suggestions(
        user_message=user_message, answer=answer, called_tools=list(called_tools), logged_in=logged_in,
        rng=random.Random(0),
    ))


def test_answer_options_keep_the_count_the_model_chose(monkeypatch):
    llm = _FakeLLM('{"kind": "answers", "options": ["要", "不用了", "要", "英文門檻是多少？", '
                   '"這個選項太長了超過二十個字所以應該被拿掉才對"]}')
    kind, options = _run(monkeypatch, llm, answer="要我把各學院的英文畢業門檻都列出來嗎？")

    assert kind == "answers"
    # 重複的、跟使用者原本的問題一樣的、太長的都拿掉，只剩兩個是非選項，不會補成三個
    assert options == ["要", "不用了"]


def test_requests_without_question_marks_can_get_answer_options(monkeypatch):
    # 「請告訴我…」沒有問號，由模型判斷它是在等使用者回答
    llm = _FakeLLM('{"kind": "answers", "options": ["微積分", "程式設計", "日文"]}')
    kind, options = _run(
        monkeypatch, llm, user_message="我要選課", answer="可以，請告訴我你想找哪一門課或關鍵字。",
        called_tools=(), logged_in=True,
    )
    assert (kind, options) == ("answers", ["微積分", "程式設計", "日文"])


def test_model_can_decide_there_are_no_good_options(monkeypatch):
    # 例如問要報名哪一場活動：不知道實際有哪些活動，就不要編
    llm = _FakeLLM('{"kind": "answers", "options": []}')
    kind, options = _run(monkeypatch, llm, user_message="幫我報名講座", answer="請問你想報名哪一場講座？")
    assert (kind, options) == ("answers", [])


def test_tool_answers_without_a_closing_question_always_get_follow_ups(monkeypatch):
    # 法規回答中間引用了問句、或工具說找不到活動：都已經回答完了，就算模型說是反問也不採用
    llm = _FakeLLM('{"kind": "answers", "options": ["要", "不是"]}')
    kind, questions = _run(
        monkeypatch, llm, user_message="選課是先搶先贏嗎？",
        answer="依選課問答集「選課是先登錄先錄取嗎？不是的。」\n- 初選截止後以亂數籤號分發。",
    )
    assert kind == "follow_ups"
    assert "要" not in questions and len(questions) == 3


def test_failed_model_shows_nothing_under_a_question_instead_of_unrelated_follow_ups(monkeypatch):
    kind, options = _run(monkeypatch, _FakeLLM(error=RuntimeError("timeout")), answer="請問你是哪個學院的學生？")
    assert (kind, options) == ("answers", [])


def test_follow_ups_use_model_output_but_drop_repeats_of_the_question(monkeypatch):
    llm = _FakeLLM('{"kind": "follow_ups", "options": ["我還差幾學分畢業？", "我的累計排名是多少？", '
                   '"我有沒有需要重修的課？", "我的必修修完了嗎？"]}')
    kind, questions = _run(
        monkeypatch, llm, user_message="我還差幾學分畢業？", answer={"kind": "academic_analysis", "focus": "credits"},
        called_tools=("get_my_academic_analysis",), logged_in=True,
    )

    assert kind == "follow_ups"
    assert questions == ["我的累計排名是多少？", "我有沒有需要重修的課？", "我的必修修完了嗎？"]


def test_follow_ups_fall_back_to_related_examples_when_model_fails(monkeypatch):
    kind, questions = _run(
        monkeypatch, _FakeLLM(error=RuntimeError("no api key")),
        user_message="我還差幾學分畢業？", called_tools=("get_my_academic_analysis",), logged_in=True,
    )
    academic = TOOL_SUGGESTIONS["get_my_academic_analysis"].examples

    assert kind == "follow_ups"
    assert len(questions) == 3
    assert "我還差幾學分畢業？" not in questions
    # 先從這輪用到的功能挑
    assert all(q in academic for q in questions)


def test_follow_ups_skip_questions_the_card_already_answers(monkeypatch):
    # 時數卡片已經列出每個類別夠不夠，模型（或備案）又推薦同一個功能的範例就濾掉；標點不同也算同一題
    llm = _FakeLLM('{"kind": "follow_ups", "options": ["我的服務學習時數夠了嗎", "我的時數達到畢業門檻了嗎？", '
                   '"有什麼活動可以幫我補時數？"]}')
    kind, questions = _run(
        monkeypatch, llm, user_message="我的學習護照時數還差多少", answer={"kind": "hours_dashboard"},
        called_tools=("get_my_hours_dashboard",), logged_in=True,
    )
    assert kind == "follow_ups"
    # 模型還剩一題能用就只放這一題，不拿無關的範例問題補滿三題
    assert questions == ["有什麼活動可以幫我補時數？"]


def test_follow_ups_drop_candidates_the_answer_already_covers(monkeypatch):
    # 模型逐題抄出回覆裡回答它的原文，抄得出來的就是已經回答過了，不推薦
    llm = _FakeLLM(json.dumps({"kind": "follow_ups", "candidates": [
        {"q": "停修申請到什麼時候", "answered_by": "停修申請期間是 115/10/19 到 115/11/27"},
        {"q": "停修會影響獎學金嗎", "answered_by": ""},
        {"q": "停修後會退學分費嗎", "answered_by": "不退學分費。"},
        {"q": "停修可以取消嗎", "answered_by": ""},
    ]}, ensure_ascii=False))
    kind, questions = _run(monkeypatch, llm, user_message="停修有什麼限制？", answer="一學期只能停修 1 科……")
    assert (kind, questions) == ("follow_ups", ["停修會影響獎學金嗎？", "停修可以取消嗎？"])


def test_follow_ups_fall_back_to_examples_when_every_candidate_is_already_answered(monkeypatch):
    llm = _FakeLLM(json.dumps({"kind": "follow_ups", "candidates": [
        {"q": "停修申請到什麼時候", "answered_by": "停修申請期間是 115/10/19 到 115/11/27"},
    ]}, ensure_ascii=False))
    kind, questions = _run(monkeypatch, llm, user_message="停修有什麼限制？", answer="一學期只能停修 1 科……")
    assert kind == "follow_ups" and len(questions) == 3
    assert "停修申請到什麼時候" not in questions


def test_guest_follow_up_fallback_never_suggests_login_features(monkeypatch):
    kind, questions = _run(
        monkeypatch, _FakeLLM(content="格式錯誤"),
        user_message="獎學金要怎麼申請？", answer="要準備...", logged_in=False,
    )
    public_examples = {
        q for f in TOOL_SUGGESTIONS.values() if not f.requires_login for q in f.examples
    }

    assert kind == "follow_ups"
    assert len(questions) == 3
    assert set(questions) <= public_examples


def test_calendar_cards_only_answer_what_was_asked(monkeypatch):
    # 查期中考的校曆卡片沒有回答寒假，「這學期什麼時候放寒假？」還是可以推薦
    llm = _FakeLLM('{"kind": "follow_ups", "options": ["這學期什麼時候放寒假？", "停修申請到哪一天？"]}')
    kind, questions = _run(
        monkeypatch, llm, user_message="期中考是什麼時候？", answer={"kind": "campus_calendar", "events": []},
        called_tools=("get_campus_calendar",),
    )
    assert (kind, questions) == ("follow_ups", ["這學期什麼時候放寒假？", "停修申請到哪一天？"])


def test_english_follow_ups_are_not_cut_by_the_chinese_length_limit(monkeypatch):
    llm = _FakeLLM('{"kind": "follow_ups", "options": ["How do I apply for an enrollment certificate in English?", '
                   '"Is the library open on national holidays?"]}')
    kind, questions = _run(monkeypatch, llm, user_message="Is the main library open on Sunday?",
                           answer="Yes. The Main Library is open on Sunday from 10:00 to 19:00.")
    assert kind == "follow_ups"
    assert questions == ["How do I apply for an enrollment certificate in English?", "Is the library open on national holidays?"]


def test_english_questions_ask_for_english_options():
    prompt = suggestions._build_suggestion_prompt("Is the library open on Sunday?", "Yes.", "", ["校園法規"], False)
    assert prompt.rstrip().endswith("使用者用英文發問，選項、問題全部用英文寫。")
    assert "全部用英文" not in suggestions._build_suggestion_prompt("圖書館星期日有開嗎？", "有。", "", [], False)


def test_follow_ups_missing_a_question_mark_get_one(monkeypatch):
    llm = _FakeLLM('{"kind": "follow_ups", "options": ["這個月有放假嗎", "幫我查我有沒有被當的課", "期中考是什麼時候？"]}')
    kind, questions = _run(monkeypatch, llm, user_message="寒假什麼時候開始？", answer={"kind": "campus_calendar"},
                           called_tools=("get_campus_calendar",))
    assert kind == "follow_ups"
    assert questions == ["這個月有放假嗎？", "幫我查我有沒有被當的課", "期中考是什麼時候？"]
