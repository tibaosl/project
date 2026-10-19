"""常見問題的標準答案（standard_answers.py 跟 academic_agent 裡用到標準答案的部分），用假的向量跟假的 OpenAI client，
不會呼叫 API，標準答案檔跟快取都寫在暫存資料夾：
    python -m pytest tests/test_standard_answers.py
"""

import json
import os
import re
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from backend import usage_log  # noqa: E402
from backend.rag import academic_agent as agent  # noqa: E402
from backend.rag import rag_documents as rd  # noqa: E402
from backend.rag import standard_answers as sa  # noqa: E402

# 假的向量：看文字裡出現哪些詞，同一個主題的問法向量一樣
TOPICS = ["學生證", "宿舍", "電器", "休學", "書卷獎"]


def _vector(text: str) -> np.ndarray:
    v = np.array([1.0 if word in text else 0.0 for word in TOPICS] + [0.1])
    return v / np.linalg.norm(v)


def _fake_embed(texts, dimensions=None, purpose=""):
    return np.stack([_vector(t) for t in texts])


def _doc(doc_id, title, text, **kw):
    card = {"title": title, "doc_type": "法規辦法", "scope": "全校", "applies_to": "學生", "version": "",
            "summary": f"{title}的摘要", "answers": [f"{title}的問題？"]}
    return rd.CatalogDocument(doc_id=doc_id, file_name=f"{title}.pdf", text=text, card=card, **kw)


def _catalog():
    return [_doc("D01", "學生證遺失補發申請辦法", "補發費用 200 元"),
            _doc("D02", "學生宿舍管理辦法", "宿舍只能用檯燈、吹風機"),
            _doc("D03", "學則", "休學累計以二學年為原則"),
            _doc("D04", "資訊工程學系書卷獎辦法", "資工系書卷獎")]


def _entry(answer_id, questions, answer, docs, **kw):
    return {"id": answer_id, "questions": questions, "answer": answer,
            "sources": [{"file": d.file_name, "text_sha256": sa.text_hash(d.text)} for d in docs],
            "reviewed": "2026-10-19", **kw}


def _entries(docs):
    return [
        _entry("student_id", ["補辦學生證要多少錢？", "學生證遺失怎麼辦？"], "補發學生證每張 200 元。", [docs[0]], any_unit=True),
        _entry("dorm_appliance", ["宿舍可以用哪些電器？"], "宿舍只能用檯燈、吹風機等電器。", [docs[1]], any_unit=True),
        _entry("suspend", ["休學最多可以休多久？"], "大學部休學累計以二學年為原則。", [docs[2]], degrees=["大學部"]),
        _entry("cs_award", ["資工系書卷獎怎麼選？"], "資工系書卷獎依系上辦法。", [docs[3]], units=["資訊工程學系"]),
    ]


class FakeVerifier:
    """標準答案的模型確認：回傳 choose(候選編號) 的結果，記下每次看到哪些候選。"""

    def __init__(self, choose=lambda ids: ids[0] if ids else "none"):
        self.choose, self.seen = choose, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        ids = re.findall(r"^\[(\w+)\]", prompt, re.M)
        self.seen.append(ids)
        content = json.dumps({"match": self.choose(ids), "reason": "測試"})
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=20)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


class FakeAgentOpenAI:
    """一般流程（挑文件、回答）的假 client，記下有沒有被呼叫。"""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(prompt_tokens=20000, completion_tokens=500)
        if kwargs.get("stream"):
            text = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="一般流程的回答 [1]"))], usage=None)
            return [text, SimpleNamespace(choices=[], usage=usage, model="gpt-5.4")]
        plan = {"question": "改寫的問題", "decision": "answer", "documents": ["D01"], "clarify_question": ""}
        message = SimpleNamespace(content=json.dumps(plan, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model="gpt-5.4")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    docs = _catalog()
    answers_file = tmp_path / "standard_answers.json"
    answers_file.write_text(json.dumps(_entries(docs), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sa, "ANSWERS_FILE", answers_file)
    monkeypatch.setattr(sa, "ENABLED", True)
    monkeypatch.setattr(rd, "CACHE_DIR", tmp_path / "rag")
    monkeypatch.setattr(rd, "embed_texts", _fake_embed)
    monkeypatch.setattr(agent, "get_catalog", lambda: docs)
    monkeypatch.setattr(agent, "unit_aliases", lambda: [("資工系", "資訊工程學系", "資訊電機學院")])
    agent._question_vector_cache.clear()
    verifier = FakeVerifier()
    monkeypatch.setattr(sa, "_openai", lambda: verifier)
    fake = FakeAgentOpenAI()
    monkeypatch.setattr(agent, "_openai", lambda: fake)
    return SimpleNamespace(docs=docs, file=answers_file, verifier=verifier, agent=fake)


def _ask(question, history=None, refresh=False):
    history = f"[使用者]: {question}" if history is None else history
    return list(agent.query_academic_knowledge_stream(question, history, refresh))


def _text(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def _cached(events):
    return next((e for e in events if e["type"] == "cached"), None)


# ============================================================
# 規則
# ============================================================
def _answer(**kw):
    base = {"id": "x", "questions": ["休學最多幾年？"], "answer": "休學累計以二學年為原則，可延長 2 年。", "sources": []}
    return sa.StandardAnswer(**{**base, **kw})


def test_degrees_numbers_and_dates_in_questions():
    assert sa.degrees_in("大二可以申請雙主修嗎") == {"大學部"}
    assert sa.degrees_in("大一英文可以免修嗎") == set()  # 講課程，不是講學制
    assert sa.degrees_in("研究生最多讀幾年") == {"碩士班", "博士班"}
    assert sa.degrees_in("碩專生要繳學分費嗎") == {"碩士在職專班"}
    assert sa.numbers_in("多益７８５分、113 學年度") == {"785", "113"}
    assert sa.asks_about_today("這學期的停修截止了嗎")
    assert not sa.asks_about_today("停修的期限是什麼時候")


def test_numbers_in_the_question_must_be_covered():
    entry = _answer()
    assert sa.passes_rules("休學最多可以休幾年", entry, set())
    assert sa.passes_rules("休學可以延長2年嗎", entry, set())
    assert not sa.passes_rules("113學年度入學的休學最多幾年", entry, set())


def test_degree_limited_answers_need_the_degree_in_the_question():
    entry = _answer(degrees=["大學部"])
    assert sa.passes_rules("大學生休學最多幾年", entry, set())
    assert not sa.passes_rules("休學最多幾年", entry, set())
    assert not sa.passes_rules("博士生休學最多幾年", entry, set())


def test_unit_rules():
    campus = _answer()
    assert sa.passes_rules("休學最多幾年", campus, set())
    # 全校的規定、系所可能另訂：問題講到系所就不用
    assert not sa.passes_rules("資工系休學最多幾年", campus, {"資訊工程學系"})
    # 系所不能另訂的（any_unit）照樣用
    assert sa.passes_rules("資工系學生證掉了怎麼辦", _answer(any_unit=True), {"資訊工程學系"})
    # 只適用某系的：問題要講到那個系
    cs = _answer(units=["資訊工程學系"])
    assert sa.passes_rules("資工系書卷獎", cs, {"資訊工程學系", "資訊電機學院"})
    assert not sa.passes_rules("物理系書卷獎", cs, {"物理學系"})
    assert not sa.passes_rules("書卷獎怎麼選", cs, set())


# ============================================================
# 文件改了就停用
# ============================================================
def test_answers_are_disabled_when_their_documents_change(setup):
    docs = setup.docs
    assert [e.stale for e in sa.load(docs)] == ["", "", "", ""]

    changed = [_doc("D01", "學生證遺失補發申請辦法", "補發費用 300 元"), *docs[1:]]
    stale = {e.id: e.stale for e in sa.load(changed)}
    assert "內容改了" in stale["student_id"] and stale["dorm_appliance"] == ""

    missing = {e.id: e.stale for e in sa.load(docs[1:])}
    assert "找不到" in missing["student_id"]

    old = [_doc("D01", "學生證遺失補發申請辦法", "補發費用 200 元", superseded_by="D09"), *docs[1:]]
    assert "有新版" in {e.id: e.stale for e in sa.load(old)}["student_id"]
    # 給較早入學年度的版本不是作廢的舊版
    cohort = [_doc("D01", "學生證遺失補發申請辦法", "補發費用 200 元", superseded_by="D09", cohort_edition=True), *docs[1:]]
    assert {e.id: e.stale for e in sa.load(cohort)}["student_id"] == ""
    # 內容一樣、合併掉的檔名也認得
    merged = [_doc("D01", "另一個檔名", "補發費用 200 元", duplicates=["學生證遺失補發申請辦法.pdf"]), *docs[1:]]
    assert {e.id: e.stale for e in sa.load(merged)}["student_id"] == ""


# ============================================================
# 配對
# ============================================================
def test_match_checks_vectors_then_rules_then_the_model(setup):
    docs = setup.docs
    vector = _vector("學生證掉了")
    chosen = sa.match("學生證掉了要怎麼辦", vector, docs, set())
    assert chosen.id == "student_id"
    assert setup.verifier.seen[-1][0] == "student_id"

    # 規則擋掉的不給模型看：休學那則只適用大學部
    setup.verifier.seen.clear()
    assert sa.match("休學最多可以休多久", _vector("休學"), docs, set()) is None
    assert setup.verifier.seen == []

    # 模型照候選的寫法回「[編號]」也認得
    setup.verifier.choose = lambda ids: f"[{ids[0]}]"
    assert sa.match("學生證掉了要怎麼辦", vector, docs, set()).id == "student_id"

    # 模型說不行就不用
    setup.verifier.choose = lambda ids: "none"
    assert sa.match("學生證可以當悠遊卡用嗎", vector, docs, set()) is None


def test_question_vectors_are_cached_on_disk(setup, monkeypatch):
    answers = sa.load(setup.docs)
    sa._question_vectors(answers)
    calls = []
    monkeypatch.setattr(rd, "embed_texts", lambda texts, **kw: calls.append(texts) or _fake_embed(texts))
    monkeypatch.setattr(sa, "_loaded", {"key": None, "answers": [], "vectors": None, "owners": None})
    vectors, owners = sa._question_vectors(sa.load(setup.docs))
    assert calls == [] and len(vectors) == len(owners) == 5


# ============================================================
# 法規問答用到標準答案
# ============================================================
def test_a_matching_question_gets_the_standard_answer_without_choosing_documents(setup):
    events = _ask("學生證不見了要怎麼補辦？")
    assert _text(events) == "補發學生證每張 200 元。"
    cached = _cached(events)
    assert cached["standard"] is True and cached["created"] == "2026-10-19"
    assert next(e for e in events if e["type"] == "sources")["sources"] == ["學生證遺失補發申請辦法.pdf"]
    assert setup.agent.calls == []  # 沒有挑文件、回答
    assert any(r["purpose"] == usage_log.STANDARD_HIT for r in usage_log.load_records(datetime(2000, 1, 1)))

    # 一模一樣的問題下次直接用存下來的，不再確認
    setup.verifier.seen.clear()
    again = _ask("學生證不見了要怎麼補辦？")
    assert _text(again) == "補發學生證每張 200 元。" and _cached(again)["standard"] is True
    assert setup.verifier.seen == [] and setup.agent.calls == []


def test_a_changed_standard_answer_is_not_reused_from_the_cache(setup):
    _ask("學生證不見了要怎麼補辦？")
    entries = _entries(setup.docs)
    entries[0]["answer"] = "補發學生證每張 250 元。"
    setup.file.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    os.utime(setup.file, ns=(1, 1))  # 確定修改時間不一樣，重新讀檔
    events = _ask("學生證不見了要怎麼補辦？")
    assert _text(events) == "補發學生證每張 250 元。"


def test_unmatched_questions_go_through_the_normal_flow(setup):
    setup.verifier.choose = lambda ids: "none"
    events = _ask("學生證可以拿來搭公車嗎？")
    assert _cached(events) is None and "一般流程的回答" in _text(events)
    assert setup.agent.calls


@pytest.mark.parametrize("question, history", [
    ("那宿舍呢？", "[使用者]: 學生證不見了怎麼辦 -> [助理]: 補發 200 元 -> [使用者]: 那宿舍呢？"),  # 要看前文
    ("How do I replace my student ID card?", None),  # 英文問題
    ("學生證今天還能補辦嗎？", None),  # 問今天
])
def test_follow_ups_english_and_date_questions_skip_standard_answers(setup, question, history):
    events = _ask(question, history)
    assert setup.verifier.seen == []
    assert not (_cached(events) or {}).get("standard")


def test_refresh_skips_the_standard_answer(setup):
    _ask("學生證不見了要怎麼補辦？")
    events = _ask("學生證不見了要怎麼補辦？", refresh=True)
    assert _cached(events) is None and "一般流程的回答" in _text(events)


def test_standard_answers_can_be_turned_off(setup, monkeypatch):
    monkeypatch.setattr(sa, "ENABLED", False)
    events = _ask("學生證不見了要怎麼補辦？")
    assert _cached(events) is None and setup.verifier.seen == []


def test_usage_summary_counts_standard_answers_with_cache_hits():
    records = [
        {"time": "2026-10-19T10:00:00", "source": "網站", "request": "a", "purpose": usage_log.STANDARD_HIT,
         "model": "", "input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost": 0.0, "saved": 0.09},
        {"time": "2026-10-19T10:01:00", "source": "網站", "request": "b", "purpose": usage_log.CACHE_HIT,
         "model": "", "input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost": 0.0, "saved": 0.05},
    ]
    text = "\n".join(usage_log.summary_lines(records))
    assert "2 次直接用了之前整理的回答（其中標準答案 1 次）" in text


# ============================================================
# 專案裡的標準答案檔
# ============================================================
def test_the_shipped_standard_answers_file_is_well_formed():
    answers = sa.read_file()
    ids = [a.id for a in answers]
    assert answers and len(ids) == len(set(ids))
    for a in answers:
        assert a.questions and all(q.strip() for q in a.questions), a.id
        assert a.answer.strip() and "；" not in a.answer, a.id
        assert a.sources and all(re.fullmatch(r"[0-9a-f]{64}", s["text_sha256"]) for s in a.sources), a.id
        assert set(a.degrees) <= set(sa._DEGREE_WORDS), a.id
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.reviewed), a.id
