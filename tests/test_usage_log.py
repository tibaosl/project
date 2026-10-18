"""usage_log.py 的測試，用假的 usage 物件，不會呼叫 API：
    python -m pytest tests/test_usage_log.py
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend import usage_log
from backend.rag import academic_agent as agent
from backend.rag import update_documents

LONG_AGO = datetime(2000, 1, 1)


def _chat_usage(prompt, completion, cached=0, reasoning=0):
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )


def test_usage_shapes_from_every_api_are_normalized():
    assert usage_log.normalize_usage(_chat_usage(1000, 200, cached=300, reasoning=50)) == {
        "input": 1000, "cached": 300, "output": 200, "reasoning": 50,
    }
    # LangChain 的 usage_metadata（Responses API 也是這個形狀）
    langchain = {"input_tokens": 500, "output_tokens": 40, "input_token_details": {"cache_read": 128},
                 "output_token_details": {"reasoning": 16}}
    assert usage_log.normalize_usage(langchain) == {"input": 500, "cached": 128, "output": 40, "reasoning": 16}
    # 向量只有輸入
    assert usage_log.normalize_usage(SimpleNamespace(prompt_tokens=80, total_tokens=80)) == {
        "input": 80, "cached": 0, "output": 0, "reasoning": 0,
    }
    # 測試的假回應、沒有 usage 的串流片段
    assert usage_log.normalize_usage(None) is None
    assert usage_log.normalize_usage(SimpleNamespace()) is None


def test_prices_match_dated_model_names_but_not_other_models():
    assert usage_log.price_of("gpt-5.4-mini-2026-03-17") == usage_log.PRICES["gpt-5.4-mini"]
    assert usage_log.price_of("gpt-5.4-2026-03-05") == usage_log.PRICES["gpt-5.4"]
    assert usage_log.price_of("gpt-5.4-nano") is None  # 不能套到 gpt-5.4 的價錢

    tokens = {"input": 1_000_000, "cached": 200_000, "output": 100_000, "reasoning": 0}
    # 80 萬一般輸入 x 2.5 + 20 萬快取 x 0.25 + 10 萬輸出 x 15
    assert usage_log.estimate_cost("gpt-5.4", tokens) == pytest.approx(2.0 + 0.05 + 1.5)
    assert usage_log.estimate_cost("gpt-4o-mini", tokens) is None


def test_records_are_appended_with_source_and_request(monkeypatch):
    monkeypatch.setenv(usage_log.SOURCE_ENV, "測試")
    request_id = usage_log.start_request()
    usage_log.record_response("挑文件", SimpleNamespace(model="gpt-5.4-2026-03-05", usage=_chat_usage(20000, 100)))
    usage_log.record_message("選工具", SimpleNamespace(
        response_metadata={"model_name": "gpt-5.4-mini"}, usage_metadata={"input_tokens": 6000, "output_tokens": 50},
    ))
    usage_log.record_response("回答", SimpleNamespace(), "gpt-5.4")  # 沒有 usage 不記

    records = usage_log.load_records(LONG_AGO)
    assert [(r["purpose"], r["model"], r["source"], r["request"]) for r in records] == [
        ("挑文件", "gpt-5.4-2026-03-05", "測試", request_id),
        ("選工具", "gpt-5.4-mini", "測試", request_id),
    ]
    assert records[0]["cost"] == pytest.approx(20000 * 2.5 / 1e6 + 100 * 15 / 1e6)


def test_a_failed_write_never_breaks_the_caller(monkeypatch, tmp_path):
    blocker = tmp_path / "不是資料夾"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(usage_log, "LOG_DIR", blocker)
    usage_log.record("回答", "gpt-5.4", _chat_usage(10, 10))  # 寫不進去只印警告


def test_request_id_follows_work_moved_to_a_background_thread():
    # 網站的法規問答是在 asyncio.to_thread 的背景 thread 裡呼叫模型的
    async def ask():
        request_id = usage_log.start_request()
        seen = await asyncio.to_thread(usage_log._request.get)
        return request_id, seen

    request_id, seen = asyncio.run(ask())
    assert seen == request_id


def _catalog():
    from backend.rag.rag_documents import CatalogDocument

    card = {"title": "學雜費收費標準", "doc_type": "辦法", "issuer": "", "scope": "", "applies_to": "", "version": "",
            "summary": "", "answers": []}
    return [CatalogDocument("D01", "學雜費收費標準.pdf", "學士班學費 17,490 元", card)]


def test_streamed_answer_records_usage_from_the_last_chunk(monkeypatch):
    docs = _catalog()
    plan = {"question": "學費多少？", "decision": "answer", "documents": docs, "clarify_question": ""}
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        text = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="學費 17,490 元"))], usage=None)]
        last = SimpleNamespace(choices=[], usage=_chat_usage(9000, 700, reasoning=300), model="gpt-5.4-2026-03-05")
        return text + [last]

    monkeypatch.setattr(agent, "get_catalog", lambda: docs)
    monkeypatch.setattr(agent, "choose_documents", lambda *args: plan)
    monkeypatch.setattr(agent, "_openai", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    assert agent.query_academic_knowledge("學費多少？")["answer"] == "學費 17,490 元"
    assert calls[0]["stream_options"] == {"include_usage": True}
    [entry] = usage_log.load_records(LONG_AGO)
    assert (entry["purpose"], entry["output"], entry["reasoning"]) == ("回答", 700, 300)


def _entry(purpose, cost, request="", source="網站", model="gpt-5.4", time="2026-10-13T10:00:00"):
    return {"time": time, "source": source, "request": request, "purpose": purpose, "model": model,
            "input": 1000, "cached": 0, "output": 100, "reasoning": 0, "cost": cost}


def test_summary_splits_regulation_questions_from_other_requests():
    records = [
        _entry("選工具", 0.01, "a", model="gpt-5.4-mini"), _entry("挑文件", 0.06, "a"), _entry("回答", 0.04, "a"),
        _entry("選工具", 0.01, "b", model="gpt-5.4-mini"),
        _entry("文件卡片", 0.5, source="每週更新文件", time="2026-10-11T03:20:00"),
        _entry("選工具", None, model="gpt-4o-mini", source="選工具評估"),
    ]
    text = "\n".join(usage_log.summary_lines(records))
    assert "合計約 US$0.62，呼叫 6 次" in text
    assert "沒有單價" in text and "gpt-4o-mini" in text
    assert "網站的每次提問" in text
    assert "法規問答  1 次，平均 US$0.11" in text
    assert "其他  1 次，平均 US$0.0100" in text
    assert "2026-10-11" in text and "2026-10-13" in text
    assert "；" not in text  # 使用者不喜歡分號
    assert usage_log.summary_lines([]) == ["這段時間沒有用量紀錄。"]


def test_load_records_skips_broken_lines_and_filters_by_time():
    path = usage_log.LOG_DIR / "2026-10.jsonl"
    lines = [json.dumps(_entry("回答", 0.1, time="2026-10-01T09:00:00")), "{寫到一半", json.dumps(_entry("回答", 0.2))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert [r["cost"] for r in usage_log.load_records(datetime(2026, 10, 5))] == [0.2]
    assert [r["cost"] for r in usage_log.load_records(datetime(2026, 9, 1), datetime(2026, 10, 5))] == [0.1]


def test_weekly_update_log_reports_only_its_own_usage(monkeypatch):
    started = datetime.now() - timedelta(minutes=1)
    monkeypatch.setenv(usage_log.SOURCE_ENV, update_documents.USAGE_SOURCE)
    usage_log.record("文件卡片", "gpt-5.4", _chat_usage(100_000, 10_000))
    monkeypatch.setenv(usage_log.SOURCE_ENV, "網站")
    usage_log.record("回答", "gpt-5.4", _chat_usage(100_000, 10_000))
    assert update_documents.usage_line(started) == "OpenAI 用量約 US$0.40（1 次呼叫）"
