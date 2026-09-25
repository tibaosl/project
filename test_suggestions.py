"""建議問題的挑選/解析/備案邏輯測試，不需要 OpenAI key：
    python -m pytest test_suggestions.py
"""

import asyncio
import random

import suggestions
from suggestions import (
    TOOL_SUGGESTIONS,
    generate_follow_up_questions,
    parse_question_list,
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


class _FakeLLM:
    def __init__(self, content=None, error=None):
        self.content, self.error = content, error

    async def ainvoke(self, prompt):
        if self.error:
            raise self.error
        return type("Reply", (), {"content": self.content})()


def _run(monkeypatch, llm, **kwargs):
    monkeypatch.setattr(suggestions, "_get_follow_up_llm", lambda: llm)
    return asyncio.run(generate_follow_up_questions(rng=random.Random(0), **kwargs))


def test_follow_ups_use_llm_output_but_drop_repeats_of_the_question(monkeypatch):
    llm = _FakeLLM('["我還差幾學分畢業？", "我的累計排名是多少？", "我有沒有需要重修的課？", "我的必修修完了嗎？"]')
    questions = _run(
        monkeypatch, llm,
        user_message="我還差幾學分畢業？", answer={"kind": "academic_analysis", "focus": "credits"},
        called_tools=["get_my_academic_analysis"], logged_in=True,
    )

    assert questions == ["我的累計排名是多少？", "我有沒有需要重修的課？", "我的必修修完了嗎？"]


def test_follow_ups_fall_back_to_related_examples_when_llm_fails(monkeypatch):
    questions = _run(
        monkeypatch, _FakeLLM(error=RuntimeError("no api key")),
        user_message="我還差幾學分畢業？", answer="", called_tools=["get_my_academic_analysis"], logged_in=True,
    )
    academic = TOOL_SUGGESTIONS["get_my_academic_analysis"].examples

    assert len(questions) == 3
    assert "我還差幾學分畢業？" not in questions
    # 先從這輪用到的功能挑
    assert all(q in academic for q in questions)


def test_guest_follow_up_fallback_never_suggests_login_features(monkeypatch):
    questions = _run(
        monkeypatch, _FakeLLM(content="格式錯誤"),
        user_message="獎學金要怎麼申請？", answer="要準備...", called_tools=["search_campus_regulations"],
        logged_in=False,
    )
    public_examples = {
        q for f in TOOL_SUGGESTIONS.values() if not f.requires_login for q in f.examples
    }

    assert len(questions) == 3
    assert set(questions) <= public_examples
