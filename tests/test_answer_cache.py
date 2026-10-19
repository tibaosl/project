"""法規問答的回答快取（answer_cache.py 跟 academic_agent 裡讀寫快取的部分），用假的 OpenAI client，
不會呼叫 API，快取寫在暫存資料夾（conftest.py）：
    python -m pytest tests/test_answer_cache.py
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")  # 匯入 main、supervisor_agent 時建立模型物件要有值

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from backend import main, usage_log  # noqa: E402
from backend.agent import supervisor_agent  # noqa: E402
from backend.rag import academic_agent as agent  # noqa: E402
from backend.rag import answer_cache  # noqa: E402
from backend.rag import rag_documents as rd  # noqa: E402

LONG_AGO = datetime(2000, 1, 1)


def _doc(doc_id, title, text=""):
    card = {"title": title, "doc_type": "法規辦法", "scope": "全校", "applies_to": "學生", "version": "",
            "summary": f"{title}的摘要", "answers": [f"{title}的問題？"]}
    return rd.CatalogDocument(doc_id=doc_id, file_name=f"{title}.pdf", text=text or f"{title}全文", card=card)


def _catalog():
    return [_doc("D01", "學生證遺失補發申請辦法", "補發費用 200 元"), _doc("D02", "學雜費收費標準"),
            _doc("D03", "學生請假規則")]


class FakeOpenAI:
    """挑文件依序回傳 plans 裡的結果（用完就一直用最後一個），回答回傳 answer 的串流，最後一片帶 usage。"""

    def __init__(self, plans, answer="補發學生證要 200 元 [1]"):
        self.plans, self.answer, self.calls = list(plans), answer, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(prompt_tokens=20000, completion_tokens=500)
        if kwargs.get("stream"):
            text = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=self.answer))], usage=None)
            return [text, SimpleNamespace(choices=[], usage=usage, model="gpt-5.4")]
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        message = SimpleNamespace(content=json.dumps(plan, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model="gpt-5.4")


def _plan(decision="answer", documents=("D01",), clarify=""):
    return {"question": "補辦學生證要多少錢？", "decision": decision, "documents": list(documents),
            "clarify_question": clarify}


@pytest.fixture
def catalog(monkeypatch):
    docs = _catalog()
    monkeypatch.setattr(agent, "get_catalog", lambda: docs)
    agent._question_vector_cache.clear()
    return docs


def _fake(monkeypatch, *plans, answer="補發學生證要 200 元 [1]"):
    fake = FakeOpenAI(plans or [_plan()], answer)
    monkeypatch.setattr(agent, "_openai", lambda: fake)
    return fake


def _ask(question="補辦學生證要多少錢？", history=None, refresh=False):
    history = f"[使用者]: {question}" if history is None else history
    return list(agent.query_academic_knowledge_stream(question, history, refresh))


def _text(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def _model_calls(fake):
    return len(fake.calls)


# ============================================================
# 鍵
# ============================================================
def test_the_same_question_written_differently_gets_the_same_key():
    key = answer_cache.make_key("補辦學生證要多少錢？", "[使用者]: 補辦學生證要多少錢？", ["設定"])
    for question in ("補辦學生證要多少錢?", "請問 補辦學生證要多少錢", "請問一下，補辦學生證要多少錢？？", "補辦學生證要多少錢 "):
        assert answer_cache.make_key("改寫過的問題", f"[使用者]: {question}", ["設定"]) == key
    # 有對話紀錄時只看使用者說的話（選工具的模型每次改寫的 query 不一樣）
    assert answer_cache.make_key("學生證補發費用", "[使用者]: 補辦學生證要多少錢？", ["設定"]) == key
    # 沒有對話紀錄（評估、直接呼叫）就看 query
    assert answer_cache.make_key("補辦學生證要多少錢?", "", ["設定"]) == answer_cache.make_key("補辦學生證要多少錢", "無", ["設定"])


def test_different_questions_context_or_settings_get_different_keys():
    base = answer_cache.make_key("q", "[使用者]: 資工系畢業要幾學分", ["設定"])
    assert answer_cache.make_key("q", "[使用者]: 電機系畢業要幾學分", ["設定"]) != base
    assert answer_cache.make_key("q", "[使用者]: 我是碩士班的 -> [使用者]: 資工系畢業要幾學分", ["設定"]) != base
    assert answer_cache.make_key("q", "[使用者]: 資工系畢業要幾學分", ["改過的 prompt"]) != base
    # 小數點不能拿掉（3.5 跟 35 不一樣）
    assert answer_cache.canonical_text("GPA 3.5 以上") != answer_cache.canonical_text("GPA 35 以上")
    assert answer_cache.canonical_text("臺北") == answer_cache.canonical_text("台北")


def test_questions_about_dates_are_cached_only_for_the_day():
    history = "[使用者]: 停修的期限是什麼時候？"
    monday, tuesday = datetime(2026, 10, 19, 9), datetime(2026, 10, 20, 9)
    assert answer_cache.make_key("q", history, [], monday) != answer_cache.make_key("q", history, [], tuesday)
    history = "[使用者]: 補辦學生證要多少錢？"
    assert answer_cache.make_key("q", history, [], monday) == answer_cache.make_key("q", history, [], tuesday)
    assert answer_cache.is_date_sensitive("When is the add/drop deadline?")
    assert not answer_cache.is_date_sensitive("How much is the tuition?")


def test_agent_key_includes_the_term_and_the_answer_language():
    history = "[使用者]: 補辦學生證要多少錢？"
    first_term, second_term = datetime(2026, 10, 19), datetime(2027, 3, 1)
    assert agent.answer_cache_key("q", history, first_term) != agent.answer_cache_key("q", history, second_term)
    assert agent.answer_cache_key("學生證", history, first_term) != agent.answer_cache_key("student ID", history, first_term)


def test_keys_from_outside_cannot_point_at_other_files():
    assert answer_cache.valid_key("0123456789abcdef0123456789abcdef")
    for key in ("../../.env", "0123456789ABCDEF0123456789ABCDEF", "abc", "", None):
        assert not answer_cache.valid_key(key)
        assert answer_cache.load(key) is None


def test_top_documents_agree_when_each_top_pick_is_in_the_other_list():
    assert answer_cache.top_documents_agree(["a", "b"], ["b", "a"])  # 順序對調
    assert answer_cache.top_documents_agree(["a"], ["a", "c"])  # 多挑一份補充文件
    assert not answer_cache.top_documents_agree(["a", "b"], ["c", "a"])
    assert not answer_cache.top_documents_agree([], [])


# ============================================================
# 問答流程
# ============================================================
def test_an_answer_is_used_only_after_two_runs_pick_the_same_documents(monkeypatch, catalog):
    fake = _fake(monkeypatch, _plan(documents=("D01", "D02")), _plan(documents=("D01",)))

    first = _ask()
    assert _model_calls(fake) == 2  # 挑文件、回答
    assert not any(e["type"] == "cached" for e in first)
    second = _ask("請問補辦學生證要多少錢")  # 寫法不同也是同一題，第二次照樣問模型
    assert _model_calls(fake) == 4

    third = _ask()
    assert _model_calls(fake) == 4  # 第三次起不再呼叫模型
    assert _text(third) == _text(first) == _text(second) == "補發學生證要 200 元"  # 引用編號照樣拿掉
    assert third[-1] == {"type": "sources", "sources": ["學生證遺失補發申請辦法.pdf"]}
    cached = next(e for e in third if e["type"] == "cached")
    assert answer_cache.valid_key(cached["key"]) and cached["created"]
    assert next(e for e in third if e["type"] == "plan")["cached"] is True

    entry = answer_cache.load(cached["key"])
    assert (entry["confirmed"], entry["computed"], entry["hits"]) == (True, 2, 1)
    # 用量紀錄：快取命中記一筆 0 token，saved 是第二次整理時花的錢（挑文件、回答各一次）
    records = usage_log.load_records(LONG_AGO)
    [hit] = [r for r in records if r["purpose"] == usage_log.CACHE_HIT]
    one_call = (20000 * 2.5 + 500 * 15) / 1e6
    assert hit["saved"] == pytest.approx(2 * one_call)
    assert sum(r["purpose"] != usage_log.CACHE_HIT for r in records) == 4


def test_runs_that_pick_different_documents_are_not_used(monkeypatch, catalog):
    fake = _fake(monkeypatch, _plan(documents=("D01",)), _plan(documents=("D02",)), _plan(documents=("D02",)))
    _ask()
    _ask()
    _ask()  # 第二次跟第一次不一樣，第三次要再問；第三次跟第二次一樣，第四次才用快取
    assert _model_calls(fake) == 6
    _ask()
    assert _model_calls(fake) == 6


def test_different_conversations_do_not_share_answers(monkeypatch, catalog):
    fake = _fake(monkeypatch)
    _ask()
    _ask()
    _ask(history="[使用者]: 我是研究生 -> [使用者]: 補辦學生證要多少錢？")
    assert _model_calls(fake) == 6


def test_refresh_skips_the_cache_and_needs_a_new_confirmation(monkeypatch, catalog):
    fake = _fake(monkeypatch)
    _ask()
    _ask()
    refreshed = _ask(refresh=True)
    assert _model_calls(fake) == 6
    assert not any(e["type"] == "cached" for e in refreshed)
    _ask()  # 重新查詢的回答還沒確認，下一個人照樣問模型（挑到一樣的文件就確認）
    assert _model_calls(fake) == 8
    _ask()
    assert _model_calls(fake) == 8


def test_clarify_questions_are_cached_without_a_refresh_note(monkeypatch, catalog):
    fake = _fake(monkeypatch, _plan(decision="clarify", documents=(), clarify="請問你是哪個系所的學生？"))
    _ask("畢業要修幾學分？")
    _ask("畢業要修幾學分？")
    events = _ask("畢業要修幾學分？")
    assert _model_calls(fake) == 2  # 反問只呼叫挑文件
    assert _text(events) == "請問你是哪個系所的學生？"
    assert not any(e["type"] == "cached" for e in events)


def test_not_found_is_cached_with_a_refresh_note(monkeypatch, catalog):
    fake = _fake(monkeypatch, _plan(decision="not_found", documents=()))
    monkeypatch.setattr(agent, "keyword_candidates", lambda question, catalog: [])
    _ask("學校有停機坪嗎？")
    _ask("學校有停機坪嗎？")
    events = _ask("學校有停機坪嗎？")
    assert _model_calls(fake) == 2
    assert _text(events) == agent.NOT_FOUND_ANSWER
    assert any(e["type"] == "cached" for e in events)


def test_failed_answers_are_not_cached(monkeypatch, catalog):
    fake = _fake(monkeypatch, answer="")
    _ask()
    _ask()
    _ask()
    assert _model_calls(fake) == 6  # 回答是空的，不存

    def broken(**kwargs):
        raise RuntimeError("OpenAI 掛了")

    monkeypatch.setattr(agent, "_openai", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=broken))))
    assert _ask()[-1]["type"] == "error"
    assert answer_cache.all_entries() == []


def test_the_cache_can_be_turned_off(monkeypatch, catalog):
    monkeypatch.setattr(answer_cache, "ENABLED", False)
    fake = _fake(monkeypatch)
    for _ in range(3):
        _ask()
    assert _model_calls(fake) == 6
    assert answer_cache.all_entries() == []


def test_a_broken_cache_file_just_means_asking_the_model(monkeypatch, catalog):
    fake = _fake(monkeypatch)
    _ask()
    _ask()
    [path] = answer_cache.CACHE_DIR.glob("*.json")
    path.write_text("{寫到一半", encoding="utf-8")
    assert _text(_ask()) == "補發學生證要 200 元"
    assert _model_calls(fake) == 6


def test_old_answers_are_asked_again_and_reconfirmed_right_away(monkeypatch, catalog):
    fake = _fake(monkeypatch)
    _ask()
    _ask()
    [entry] = answer_cache.all_entries()
    entry["created"] = (datetime.now() - timedelta(days=answer_cache.MAX_AGE_DAYS + 1)).isoformat(timespec="seconds")
    answer_cache.save(entry)
    _ask()  # 太舊，重新問一次。挑到的文件一樣，馬上算確認
    assert _model_calls(fake) == 6
    _ask()
    assert _model_calls(fake) == 6


# ============================================================
# 文件更新之後
# ============================================================
def _confirmed(monkeypatch, catalog):
    fake = _fake(monkeypatch)
    _ask()
    _ask()
    return fake


def _new_catalog(monkeypatch, docs):
    monkeypatch.setattr(agent, "get_catalog", lambda: docs)
    return docs


def test_a_changed_document_means_asking_again(monkeypatch, catalog):
    fake = _confirmed(monkeypatch, catalog)
    docs = _catalog()
    docs[0].text = "補發費用 300 元"  # 每週更新抓到新版
    _new_catalog(monkeypatch, docs)
    _ask()
    assert _model_calls(fake) == 6


def test_a_document_marked_as_an_old_version_means_asking_again(monkeypatch, catalog):
    fake = _confirmed(monkeypatch, catalog)
    docs = _catalog() + [_doc("D04", "學生證遺失補發申請辦法（115 年修正）")]
    docs[0].superseded_by = "D04"
    _new_catalog(monkeypatch, docs)
    _ask()
    assert _model_calls(fake) == 6


def test_new_documents_matter_only_if_they_reach_the_candidates(monkeypatch, catalog):
    fake = _confirmed(monkeypatch, catalog)
    docs = _catalog() + [_doc("D04", "宿舍管理辦法")]
    _new_catalog(monkeypatch, docs)
    candidates = {"list": docs[:3]}
    monkeypatch.setattr(agent, "retrieve_candidates", lambda query, history, catalog: list(candidates["list"]))
    monkeypatch.setattr(agent, "keyword_candidates", lambda question, catalog: [])

    events = _ask()  # 新增的宿舍辦法跟這題無關（沒進初篩的候選），照樣用快取
    assert _model_calls(fake) == 4
    assert any(e["type"] == "cached" for e in events)

    docs = _catalog() + [_doc("D04", "學生證補發常見問答")]
    _new_catalog(monkeypatch, docs)
    candidates["list"] = docs
    _ask()  # 新文件進了候選，模型可能會挑它，重新問
    assert _model_calls(fake) == 6


def test_cache_entries_remember_the_catalog_version_after_checking(monkeypatch, catalog):
    _confirmed(monkeypatch, catalog)
    docs = _new_catalog(monkeypatch, _catalog() + [_doc("D04", "宿舍管理辦法")])
    calls = []
    monkeypatch.setattr(agent, "retrieve_candidates", lambda query, history, catalog: calls.append(1) or docs[:3])
    _ask()
    _ask()
    assert calls == [1]  # 同一個目錄只重新初篩一次


# ============================================================
# 統計、清除
# ============================================================
def test_summary_lists_the_most_asked_questions(monkeypatch, catalog):
    _fake(monkeypatch)
    for _ in range(3):
        _ask()
    _ask("學費多少？")
    text = "\n".join(answer_cache.summary_lines(answer_cache.all_entries()))
    assert "快取 2 題（確認過、會直接用的 1 題），這些問題被問了 4 次，其中 1 次直接用快取" in text
    assert "     3 次／   1 次  補辦學生證要多少錢？" in text
    assert "學費多少？（還沒確認）" in text
    assert "；" not in text
    assert answer_cache.summary_lines([]) == ["目前沒有快取的回答。"]


def test_cli_deletes_matching_entries(monkeypatch, catalog, capsys):
    _fake(monkeypatch)
    _ask()
    _ask("學費多少？")
    monkeypatch.setattr("sys.argv", ["answer_cache", "--delete", "學生證"])
    answer_cache.main()
    assert [e["query"] for e in answer_cache.all_entries()] == ["學費多少？"]
    assert "共刪掉 1 題快取" in capsys.readouterr().out


def test_prune_drops_the_least_recently_used_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(answer_cache, "CACHE_DIR", tmp_path)
    for i in range(12):
        path = tmp_path / f"{i:032x}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1000 + i, 1000 + i))
    assert answer_cache.prune(limit=10) == 3
    assert sorted(p.stem for p in tmp_path.glob("*.json"))[0] == f"{3:032x}"


def test_usage_summary_shows_cache_savings_separately():
    def entry(purpose, cost, request, saved=None):
        record = {"time": "2026-10-19T10:00:00", "source": "網站", "request": request, "purpose": purpose,
                  "model": "gpt-5.4", "input": 1000, "cached": 0, "output": 100, "reasoning": 0, "cost": cost}
        if saved is not None:
            record.update(model="", input=0, output=0, saved=saved)
        return record

    records = [
        entry("選工具", 0.004, "a"), entry("挑文件", 0.055, "a"), entry("回答", 0.031, "a"),
        entry("選工具", 0.004, "b"), entry(usage_log.CACHE_HIT, 0.0, "b", saved=0.086),
    ]
    text = "\n".join(usage_log.summary_lines(records))
    assert "呼叫 4 次" in text  # 快取命中不算呼叫
    assert "法規問答快取：1 次直接用了之前整理的回答，約省下 US$0.0860" in text
    assert "法規問答  1 次，平均 US$0.09" in text
    assert "法規問答（快取）  1 次，平均 US$0.0040" in text
    assert "快取命中" not in text.split("法規問答快取")[0]  # 依用途的統計裡沒有它


# ============================================================
# supervisor、API
# ============================================================
class _ToolCallingLLM:
    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        return AIMessage(content="", tool_calls=[
            {"name": "search_campus_regulations", "args": {"query": "補辦學生證費用"}, "id": "call_1"},
        ])


def test_supervisor_passes_the_cached_event_to_the_frontend(monkeypatch):
    def fake_stream(query, history):
        yield {"type": "cache_key", "key": "0" * 32, "hit": True}
        yield {"type": "cached", "key": "0" * 32, "created": "2026-10-18T10:00:00"}
        yield {"type": "token", "text": "200 元"}
        yield {"type": "sources", "sources": ["學生證遺失補發申請辦法.pdf"]}

    monkeypatch.setattr(supervisor_agent, "llm_smart", _ToolCallingLLM())
    monkeypatch.setattr(supervisor_agent.academic_agent, "query_academic_knowledge_stream", fake_stream)

    async def collect():
        return [e async for e in supervisor_agent._agent_turn_events("補辦學生證要多少錢", "", "", {}, "無")]

    events = asyncio.run(collect())
    assert {"type": "cached", "key": "0" * 32, "created": "2026-10-18T10:00:00"} in events
    assert not any(e["type"] == "cache_key" for e in events)  # 快取鍵只給 supervisor 自己用，不送給前端
    assert events[-1]["agent_results"] == ["200 元"]
    assert events[-1]["answer_cache"] == {"key": "0" * 32, "hit": True}


def _client():
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def test_refresh_api_answers_the_original_question_again(monkeypatch):
    key = "a" * 32
    answer_cache.save({"format": answer_cache.FORMAT, "key": key, "query": "補辦學生證費用",
                       "history": "[使用者]: 補辦學生證要多少錢？"})
    calls = []

    def fake_stream(query, history, refresh=False):
        calls.append((query, history, refresh))
        yield {"type": "status", "text": "正在挑選相關文件..."}
        yield {"type": "plan", "question": "q", "decision": "answer", "documents": []}
        yield {"type": "token", "text": "200 元"}
        yield {"type": "sources", "sources": []}

    monkeypatch.setattr(main.academic_agent, "query_academic_knowledge_stream", fake_stream)
    response = _client().post("/api/regulations/refresh", json={"key": key})
    events = [json.loads(line[5:]) for line in response.text.split("\n\n") if line.startswith("data:")]

    assert calls == [("補辦學生證費用", "[使用者]: 補辦學生證要多少錢？", True)]
    assert [e["type"] for e in events] == ["status", "token", "sources", "done"]  # plan 是除錯用，不送給前端


def test_refresh_api_rejects_unknown_keys():
    response = _client().post("/api/regulations/refresh", json={"key": "../../.env"})
    assert response.status_code == 404
    assert _client().post("/api/regulations/refresh", json={"key": "b" * 32}).status_code == 404


# ============================================================
# 回答下面的選項跟著快取
# ============================================================
def test_answers_report_their_cache_key_to_the_supervisor(monkeypatch, catalog):
    _fake(monkeypatch)
    first = _ask()
    key = next(e for e in first if e["type"] == "cache_key")
    assert key["hit"] is False and answer_cache.load(key["key"]) is not None
    _ask()
    third = _ask()
    assert {"type": "cache_key", "key": key["key"], "hit": True} in third

    monkeypatch.setattr(answer_cache, "ENABLED", False)
    assert not any(e["type"] == "cache_key" for e in _ask())


def test_suggestions_are_kept_per_audience_and_dropped_with_a_new_answer(monkeypatch, catalog):
    _fake(monkeypatch)
    key = next(e for e in _ask() if e["type"] == "cache_key")["key"]
    answer_cache.remember_suggestions(key, True, "follow_ups", ["我的學生證什麼時候可以領？"])
    answer_cache.remember_suggestions(key, False, "follow_ups", [])  # 空的不存
    assert answer_cache.cached_suggestions(key, True) == ("follow_ups", ["我的學生證什麼時候可以領？"])
    assert answer_cache.cached_suggestions(key, False) is None

    _ask()  # 重新整理的回答存進去，舊回答的選項跟著清掉
    assert answer_cache.cached_suggestions(key, True) is None


class _SuggestionCalls:
    def __init__(self, result=("follow_ups", ["補辦要多久？"])):
        self.result, self.calls = result, []

    async def __call__(self, user_message, answer, called_tools, logged_in):
        self.calls.append((user_message, answer, logged_in))
        return self.result


def _final(cache):
    return {"agent_results": ["補發要 200 元"], "called_tools": ["search_campus_regulations"], "answer_cache": cache}


def test_cached_answers_reuse_their_suggestions(monkeypatch, catalog):
    _fake(monkeypatch)
    key = next(e for e in _ask() if e["type"] == "cache_key")["key"]
    generate = _SuggestionCalls()
    monkeypatch.setattr(supervisor_agent, "generate_suggestions", generate)

    # 剛整理好的回答：產生選項，存進快取
    result = asyncio.run(supervisor_agent._turn_suggestions(_final({"key": key, "hit": False}), "補辦學生證", True))
    assert result == ("follow_ups", ["補辦要多久？"]) and len(generate.calls) == 1
    # 用到快取：直接用存好的，不呼叫模型
    result = asyncio.run(supervisor_agent._turn_suggestions(_final({"key": key, "hit": True}), "補辦學生證", True))
    assert result == ("follow_ups", ["補辦要多久？"]) and len(generate.calls) == 1
    # 沒登入的人推薦的功能不一樣，要另外產生一次
    asyncio.run(supervisor_agent._turn_suggestions(_final({"key": key, "hit": True}), "補辦學生證", False))
    assert len(generate.calls) == 2 and answer_cache.cached_suggestions(key, False) is not None
    # 不是法規問答（沒有快取鍵）照平常產生
    asyncio.run(supervisor_agent._turn_suggestions(_final(None), "我的課表", True))
    assert len(generate.calls) == 3


# ============================================================
# 不問選工具的模型，直接用快取
# ============================================================
class _CountingToolLLM(_ToolCallingLLM):
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return await super().ainvoke(messages)


def _turn(user_input, username="", pending=None):
    async def collect():
        return [e async for e in supervisor_agent._agent_turn_events(
            user_input, username, "", pending or {}, f"[使用者]: {user_input}")]
    return asyncio.run(collect())


@pytest.fixture
def router(monkeypatch):
    llm = _CountingToolLLM()
    monkeypatch.setattr(supervisor_agent, "llm_smart", llm)
    monkeypatch.setattr(supervisor_agent, "_route_fingerprint", {})
    return llm


def test_conversations_routed_to_regulations_twice_skip_the_tool_choice(monkeypatch, catalog, router):
    fake = _fake(monkeypatch)
    _turn("補辦學生證要多少錢？")
    _turn("請問補辦學生證要多少錢")  # 第二次：回答確認、選工具的模型第二次選了法規問答
    assert (router.calls, _model_calls(fake)) == (2, 4)

    events = _turn("補辦學生證要多少錢?")
    assert (router.calls, _model_calls(fake)) == (2, 4)  # 選工具、挑文件、回答都不用再問模型
    assert any(e["type"] == "cached" for e in events)
    assert not any(e.get("text") == "正在理解你的問題..." for e in events)
    final = events[-1]
    assert final["called_tools"] == ["search_campus_regulations"]
    assert final["agent_results"] == ["補發學生證要 200 元"]
    assert final["answer_cache"]["hit"] is True


def test_logged_in_users_are_routed_separately(monkeypatch, catalog, router):
    # 登入後「畢業要修幾學分」這類問題可能會被導到學業分析，訪客的紀錄不能拿來用
    _fake(monkeypatch)
    _turn("補辦學生證要多少錢？")
    _turn("補辦學生證要多少錢？")
    _turn("補辦學生證要多少錢？", username="113000000")
    assert router.calls == 3
    _turn("補辦學生證要多少錢？", username="113000000")
    assert router.calls == 4
    _turn("補辦學生證要多少錢？", username="113000000")
    assert router.calls == 4  # 登入的人也被導到法規問答兩次之後才直接用


def test_pending_actions_and_changed_prompts_go_through_the_tool_choice(monkeypatch, catalog, router):
    _fake(monkeypatch)
    _turn("補辦學生證要多少錢？")
    _turn("補辦學生證要多少錢？")
    _turn("補辦學生證要多少錢？", pending={"type": "ACTIVITY_RECOMMEND", "tag_names": ["國際視野"]})
    assert router.calls == 3  # 有待接續的動作，照平常讓模型判斷

    monkeypatch.setattr(supervisor_agent, "AGENT_SYSTEM_PROMPT", supervisor_agent.AGENT_SYSTEM_PROMPT + "新規則\n")
    monkeypatch.setattr(supervisor_agent, "_route_fingerprint", {})
    _turn("補辦學生證要多少錢？")
    assert router.calls == 4  # 選工具的 prompt 改了，之前記的不算


def test_routes_survive_a_new_answer_but_not_without_a_confirmed_one(monkeypatch, catalog, router):
    fake = _fake(monkeypatch, _plan(documents=("D01",)), _plan(documents=("D02",)), _plan(documents=("D02",)))
    _turn("補辦學生證要多少錢？")
    _turn("補辦學生證要多少錢？")  # 兩次挑的文件不一樣，還沒確認
    entry = answer_cache.all_entries()[0]
    assert (entry["confirmed"], sum(entry["routes"].values())) == (False, 2)  # 選工具的次數跟著留下來
    _turn("補辦學生證要多少錢？")
    assert router.calls == 3 and _model_calls(fake) == 6
    _turn("補辦學生證要多少錢？")
    assert router.calls == 3  # 確認之後就直接用
