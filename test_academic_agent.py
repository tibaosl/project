"""academic_agent.py 的查詢流程測試，用假的 OpenAI client，不需要 data/ 也不會真的呼叫 API：
    python -m pytest test_academic_agent.py
"""

import json
from types import SimpleNamespace

import academic_agent as agent
import rag_documents as rd


def _doc(doc_id, title, text="", superseded_by=""):
    card = {"title": title, "doc_type": "法規辦法", "scope": "全校", "applies_to": "學生", "version": "",
            "summary": f"{title}的摘要", "answers": [f"{title}的問題？"]}
    return rd.CatalogDocument(doc_id=doc_id, file_name=f"{title}.pdf", text=text or f"{title}全文",
                              card=card, superseded_by=superseded_by)


CATALOG = [_doc("D01", "學雜費收費標準", "學士班資電學院學費 17,490 元"), _doc("D02", "學生證遺失補發申請作業"),
           _doc("D03", "舊版表單", superseded_by="D02")]


class FakeOpenAI:
    """plan_query 拿 JSON、回答步驟拿串流片段，記錄收到的 messages 方便檢查。
    plan 可以給一個 list，依序當成每一次挑文件的結果。
    """

    def __init__(self, plan, answer_chunks=("答案",)):
        self.plans = plan if isinstance(plan, list) else [plan]
        self.answer_chunks, self.calls = answer_chunks, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))]) for c in self.answer_chunks]
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        message = SimpleNamespace(content=json.dumps(plan, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _use_fakes(monkeypatch, plan, answer_chunks=("答案",)):
    fake = FakeOpenAI(plan, answer_chunks)
    monkeypatch.setattr(agent, "_openai", lambda: fake)
    monkeypatch.setattr(agent, "get_catalog", lambda: CATALOG)
    return fake


def _plan(decision="answer", documents=(), question="完整問題", clarify=""):
    return {"question": question, "decision": decision, "documents": list(documents), "clarify_question": clarify}


def test_plan_query_ignores_unknown_and_duplicate_document_ids(monkeypatch):
    _use_fakes(monkeypatch, _plan(documents=["[D02]", "D99", "D02", "D01"]))
    plan = agent.plan_query("學生證不見了", "", CATALOG)
    assert [d.doc_id for d in plan["documents"]] == ["D02", "D01"]


def test_plan_query_without_valid_documents_becomes_not_found(monkeypatch):
    _use_fakes(monkeypatch, _plan(documents=["D99"]))
    assert agent.plan_query("宿舍", "", CATALOG)["decision"] == "not_found"


def test_router_prompt_lists_catalog_and_marks_old_versions(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(decision="not_found"))
    agent.plan_query("問題", "[使用者]: 問題", CATALOG)
    system = fake.calls[0]["messages"][0]["content"]
    assert "[D01] 學雜費收費標準" in system
    assert "[D03] 舊版表單（舊版，最新版是 [D02]）" in system
    assert "資訊電機學院" in system  # 系所對照表有放進去


def test_stream_answers_with_full_documents_and_cited_sources(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(documents=["D01", "D02"]), answer_chunks=("學費 17,490 元", "[1]"))
    events = list(agent.query_academic_knowledge_stream("資工系學費多少", "[使用者]: 資工系學費多少"))

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "學費 17,490 元[1]"
    assert events[-1] == {"type": "sources", "sources": ["學雜費收費標準.pdf"]}
    answer_request = fake.calls[1]["messages"][1]["content"]
    assert "學士班資電學院學費 17,490 元" in answer_request  # 回答步驟拿到的是文件全文


def test_stream_clarify_and_not_found_skip_the_answer_step(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(decision="clarify", clarify="請問你是哪個學院？"))
    events = list(agent.query_academic_knowledge_stream("英文門檻", ""))
    assert [e["text"] for e in events if e["type"] == "token"] == ["請問你是哪個學院？"]
    assert len(fake.calls) == 1

    _use_fakes(monkeypatch, _plan(decision="not_found"))
    result = agent.query_academic_knowledge("宿舍怎麼申請", "")
    assert result["answer"] == agent.NOT_FOUND_ANSWER
    assert result["sources"] == []


def test_stream_reports_errors_instead_of_raising(monkeypatch):
    def broken():
        raise RuntimeError("沒有 API key")

    monkeypatch.setattr(agent, "get_catalog", lambda: CATALOG)
    monkeypatch.setattr(agent, "_openai", broken)
    events = list(agent.query_academic_knowledge_stream("學費", ""))
    assert events[-1]["type"] == "error"
    assert "沒有 API key" in events[-1]["message"]


def test_not_found_is_rechecked_with_keyword_hints(monkeypatch):
    fake = _use_fakes(monkeypatch, [_plan(decision="not_found"), _plan(documents=["D02"])])
    monkeypatch.setattr(agent, "keyword_candidates", lambda question, catalog: [CATALOG[1]])

    plan = agent.choose_documents("學生證找到了要怎麼恢復", "", CATALOG)
    assert plan["decision"] == "answer"
    assert [d.doc_id for d in plan["documents"]] == ["D02"]
    second_request = fake.calls[1]["messages"][1]["content"]
    assert "關鍵字比對找到" in second_request and "[D02] 學生證遺失補發申請作業" in second_request


def test_not_found_without_keyword_hints_is_not_rechecked(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(decision="not_found"))
    monkeypatch.setattr(agent, "keyword_candidates", lambda question, catalog: [])
    assert agent.choose_documents("宿舍怎麼申請", "", CATALOG)["decision"] == "not_found"
    assert len(fake.calls) == 1


def test_keyword_candidates_weights_rare_words():
    catalog = [
        _doc("D01", "學生證遺失補發申請作業", "學生證掛失後找到卡片，可以在掛失系統申請復卡。"),
        _doc("D02", "學雜費收費標準", "學生申請繳費。"),
        _doc("D03", "教室借用申請", "學生申請借用教室。"),
    ]
    found = agent.keyword_candidates("學生證找到了，要怎麼復卡？", catalog, min_score=0.5)
    assert found[0].doc_id == "D01"
    assert agent.keyword_candidates("學生證找到了，要怎麼復卡？", catalog, min_score=100) == []


def test_cited_sources_only_lists_cited_documents():
    docs = [_doc("D01", "甲"), _doc("D02", "乙"), _doc("D03", "丙")]
    assert agent.cited_sources("重點 [1]，另外 [3]", docs) == ["甲.pdf", "丙.pdf"]
    assert agent.cited_sources("重點 [1、2]", docs) == ["甲.pdf", "乙.pdf"]
    assert agent.cited_sources("沒有標引用", docs) == ["甲.pdf", "乙.pdf", "丙.pdf"]


def test_fit_document_keeps_head_and_relevant_sections():
    head = "國立中央大學學則"
    sections = [f"第{i}條 其他規定內容" + "填充" * 200 for i in range(30)]
    relevant = "第99條 研究生修讀學士班課程之學分不列入畢業學分"
    text = "\n\n".join([head] + sections[:15] + [relevant] + sections[15:])

    fitted = agent.fit_document(text, "研究生修學士班課程可以列入畢業學分嗎", limit=3000)
    assert len(fitted) <= 3000 + 200
    assert fitted.startswith(head)
    assert relevant in fitted
    assert "中間省略" in fitted
    assert agent.fit_document("短文件", "問題") == "短文件"


def test_today_context_names_the_academic_year_and_term():
    from datetime import datetime

    # 第 1 學期是 8 月到隔年 1 月，第 2 學期是 2 月到 7 月（民國年 = 西元年 - 1911）
    assert agent.today_context(datetime(2026, 10, 4)) == "今天是 2026 年 10 月 4 日（115 學年度第 1 學期）"
    assert agent.today_context(datetime(2027, 1, 20)).endswith("（115 學年度第 1 學期）")
    assert agent.today_context(datetime(2027, 3, 1)).endswith("（115 學年度第 2 學期）")
    assert agent.today_context(datetime(2027, 8, 1)).endswith("（116 學年度第 1 學期）")
