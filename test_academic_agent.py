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
    plan = agent.plan_query("學生證不見了", "", CATALOG, CATALOG)
    assert [d.doc_id for d in plan["documents"]] == ["D02", "D01"]


def test_plan_query_accepts_ids_written_with_their_titles(monkeypatch):
    # 模型有時候會照目錄的樣子連標題一起寫，不能因此變成查無資料
    _use_fakes(monkeypatch, _plan(documents=["[D02] 學生證遺失補發申請作業", "D01：學雜費收費標準"]))
    plan = agent.plan_query("學生證不見了", "", CATALOG, CATALOG)
    assert plan["decision"] == "answer"
    assert [d.doc_id for d in plan["documents"]] == ["D02", "D01"]


def test_plan_query_without_valid_documents_becomes_not_found(monkeypatch):
    _use_fakes(monkeypatch, _plan(documents=["D99"]))
    assert agent.plan_query("宿舍", "", CATALOG, CATALOG)["decision"] == "not_found"


def test_router_prompt_lists_candidates_and_marks_old_versions(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(decision="not_found"))
    agent.plan_query("問題", "[使用者]: 問題", CATALOG, [CATALOG[0], CATALOG[2]])
    system, request = (m["content"] for m in fake.calls[0]["messages"])
    assert "[D01] 學雜費收費標準" in request
    assert "[D03] 舊版表單（舊版，最新版是 [D02]）" in request
    assert "D02" not in request.replace("最新版是 [D02]", "")  # 沒通過初篩的卡片不會給模型看
    assert request.rstrip().endswith("這一次的問題：問題")
    assert "資訊電機學院" in system  # 系所對照表有放進去


def test_router_cards_list_only_the_first_few_answerable_questions(monkeypatch):
    # 「可回答問題」佔了挑文件 prompt 大部分的 token，每張卡片只列前幾題
    doc = _doc("D20", "學生請假規則")
    doc.card["answers"] = [f"請假問題{i}？" for i in range(1, 7)]
    fake = _use_fakes(monkeypatch, _plan(decision="not_found"))
    monkeypatch.setattr(agent, "ROUTER_ANSWERS_PER_CARD", 4)
    agent.plan_query("怎麼請假", "", CATALOG + [doc], [doc])
    request = fake.calls[0]["messages"][1]["content"]
    assert "可回答：請假問題1？／請假問題2？／請假問題3？／請假問題4？（另有 2 題）" in request
    assert "請假問題5" not in request


def test_router_can_pick_the_latest_version_outside_the_candidates(monkeypatch):
    _use_fakes(monkeypatch, _plan(documents=["D02"]))
    plan = agent.plan_query("問題", "", CATALOG, [CATALOG[2]])
    assert [d.doc_id for d in plan["documents"]] == ["D02"]


def test_picking_a_page_snapshot_brings_its_poster_images(monkeypatch):
    page = _doc("D10", "大學部英外文畢業門檻", "送件方式…\n\n（這個頁面的圖片內容另外存在「大學部英外文畢業門檻（圖片）.pdf」。）\n")
    page.file_name = "語言中心/大學部英外文畢業門檻.md"
    poster = _doc("D11", "大學部英文畢業門檻", "管理學院 多益 700 分")
    poster.file_name = "語言中心/大學部英外文畢業門檻（圖片）.pdf"
    catalog = CATALOG + [page, poster]
    _use_fakes(monkeypatch, _plan(documents=["D10", "D01", "D02"]))
    plan = agent.plan_query("企管系多益要幾分", "", catalog, catalog)
    assert [d.doc_id for d in plan["documents"]] == ["D10", "D01", "D02", "D11"]


def test_stream_answers_with_full_documents_and_cited_sources(monkeypatch):
    fake = _use_fakes(monkeypatch, _plan(documents=["D01", "D02"]), answer_chunks=("學費 17,490 元", "[1]"))
    events = list(agent.query_academic_knowledge_stream("資工系學費多少", "[使用者]: 資工系學費多少"))

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "學費 17,490 元"  # 引用編號不顯示，但照樣用來決定參考資料
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


def _vector(*weights):
    import numpy as np

    v = np.array(weights + (0.0,) * (4 - len(weights)), dtype=np.float32)
    return v / np.linalg.norm(v)


def _retrieval_catalog():
    docs = [
        _doc("D01", "學業優良獎學金實施要點", "每學期各班成績前三名發給獎學金。"),
        _doc("D02", "學生宿舍管理辦法", "住宿生應遵守宿舍規定。"),
        _doc("D03", "物理學系碩士班修業規定", "物理學系碩士班畢業學分為二十四學分。"),
        _doc("D04", "教室借用要點", "借用教室請事先登記。"),
        _doc("D05", "113學年度課程地圖", "必修科目一覽。"),
        _doc("D06", "114學年度課程地圖", "必修科目一覽。"),
    ]
    docs[4].superseded_by = "D06"
    for doc, vector in zip(docs, [_vector(1), _vector(0, 1), _vector(0, 0, 1), _vector(0, 0, 0, 1),
                                  _vector(0, 1, 1), _vector(0, 0, 0, 1)]):
        doc.embedding = vector
    return docs


def _fake_embeddings(monkeypatch, by_keyword):
    """問題裡有哪個關鍵字就回傳哪個向量，記錄送去算向量的文字。"""
    import numpy as np

    sent = []

    def embed(texts):
        sent.extend(texts)
        return np.stack([next((v for k, v in by_keyword.items() if k in t), _vector(1, 1, 1, 1)) for t in texts])

    monkeypatch.setattr(rd, "embed_texts", embed)
    return sent


def test_retrieval_uses_vectors_for_paraphrases_and_keywords_from_history(monkeypatch):
    catalog = _retrieval_catalog()
    # 「書卷獎」跟「學業優良獎學金」沒有共同的字，只能靠向量
    sent = _fake_embeddings(monkeypatch, {"物理": _vector(0, 0, 1), "書卷獎": _vector(1)})
    history = "[使用者]: 我是物理學系的 -> [使用者]: 書卷獎可以拿多少？"

    found = agent.retrieve_candidates("書卷獎可以拿多少？", history, catalog, limit=2)
    assert {d.doc_id for d in found} == {"D01", "D03"}
    assert sent == ["書卷獎可以拿多少？", history]  # 問題本身、加上對話紀錄各算一次


def test_retrieval_adds_the_latest_version_of_old_candidates(monkeypatch):
    catalog = _retrieval_catalog()
    _fake_embeddings(monkeypatch, {"課程地圖": _vector(0, 1, 1)})
    found = agent.retrieve_candidates("113學年度課程地圖", "", catalog, limit=1)
    assert [d.doc_id for d in found] == ["D05", "D06"]


def test_retrieval_falls_back_to_keywords_when_embeddings_fail(monkeypatch):
    catalog = _retrieval_catalog()

    def broken(texts):
        raise RuntimeError("沒有網路")

    monkeypatch.setattr(rd, "embed_texts", broken)
    assert [d.doc_id for d in agent.retrieve_candidates("宿舍管理辦法", "", catalog, limit=1)] == ["D02"]


def test_small_catalogs_skip_retrieval(monkeypatch):
    sent = _fake_embeddings(monkeypatch, {})
    assert agent.retrieve_candidates("問題", "", CATALOG) == CATALOG
    assert sent == []


def test_department_mentions_pull_that_departments_documents_into_the_candidates(monkeypatch):
    # 兩個系都有「碩士班修業辦法」，向量分不出來；問題提到物理系，物理系資料夾的就要進候選
    docs = []
    for i, folder in enumerate(["化學學系", "物理學系", "教務處", "教務處註冊組"], 1):
        doc = _doc(f"D0{i}", "碩士班修業辦法" if "系" in folder else f"其他文件{i}")
        doc.file_name = f"{folder}/{doc.card['title']}.pdf"
        doc.embedding = _vector(1) if folder == "化學學系" else _vector(0, 1)
        docs.append(doc)
    _fake_embeddings(monkeypatch, {"": _vector(1)})
    found = agent.retrieve_candidates("物理系碩士班要修幾學分", "", docs, limit=1)
    assert "物理學系/碩士班修業辦法.pdf" in [d.file_name for d in found]
    assert agent.unit_folders("我是物理系的", docs) == {"物理學系"}
    assert agent.unit_folders("中文版成績單", docs) == set()  # 兩個字的「中文」不算系所


def _clean(chunks):
    cleaner = agent.AnswerCleaner()
    return "".join(cleaner.feed(chunk) for chunk in chunks) + cleaner.flush()


def test_answer_cleaner_drops_citation_markers_even_when_split_across_chunks():
    # 引用編號只拿來決定要列哪些參考資料，畫面上不顯示（使用者覺得句子後面一串 [1][2] 很雜）
    assert _clean(["學費 17,490 元", "[", "1", "]。雜費另計 [2、", "3]。"]) == "學費 17,490 元。雜費另計。"
    assert _clean(["- 學士班：700 分[1]\n- 碩士班：750 分[2]"]) == "- 學士班：700 分\n- 碩士班：750 分"
    # 不是引用編號的方括號照留
    assert _clean(["[注意]請", "看[辦法](https://example.com)第[", "三條]"]) == "[注意]請看[辦法](https://example.com)第[三條]"


def test_answer_cleaner_replaces_semicolons_even_when_split_across_chunks():
    assert _clean(["要先填申請表；", "再交到註冊組"]) == "要先填申請表，再交到註冊組"
    assert _clean(["- 學士班 700 分；", "\n- 碩士班 750 分；"]) == "- 學士班 700 分。\n- 碩士班 750 分。"
    assert _clean(["逾期不受理；[1]", "\n下一段"]) == "逾期不受理。\n下一段"


def test_streamed_answer_hides_citations_but_sources_still_follow_them(monkeypatch):
    docs = [CATALOG[0], CATALOG[1]]
    plan = {"question": "學費多少？", "decision": "answer", "documents": docs, "clarify_question": ""}
    monkeypatch.setattr(agent, "get_catalog", lambda: CATALOG)
    monkeypatch.setattr(agent, "choose_documents", lambda *args: plan)
    fake = FakeOpenAI({}, answer_chunks=["學費 17,490 元", "[1", "]。"])  # 挑文件換成上面的 plan，只用到回答的串流
    monkeypatch.setattr(agent, "_openai", lambda: fake)

    result = agent.query_academic_knowledge("學費多少？")
    assert result["answer"] == "學費 17,490 元。"
    assert result["sources"] == [docs[0].file_name]  # 只標了 [1]，只列第一份


def test_replace_semicolons_uses_commas_inside_a_line_and_full_stops_at_the_end():
    assert agent.replace_semicolons("先填表；再送件；") == "先填表，再送件。"
    assert agent.replace_semicolons("- 學士班；\n- 碩士班") == "- 學士班。\n- 碩士班"
