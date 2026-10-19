"""OpenAI 用量紀錄：每次呼叫模型或算向量都記一筆 token 數跟估計金額，存在 storage/usage/（一個月一個 .jsonl）。

看統計，在專案根目錄執行：

    python -m backend.usage_log                  # 最近 7 天
    python -m backend.usage_log --days 30
    python -m backend.usage_log --month 2026-10

金額是用 PRICES 的公開單價估的（不含稅，OpenAI 調價要改 PRICES），實際帳單以 OpenAI 後台為準。
紀錄裡沒有問題或回答的內容，只有時間、來源、用途、模型跟 token 數。記錄失敗只印警告，不影響回答。

新增呼叫 OpenAI 的地方要記得記錄：OpenAI SDK 的回應用 record_response，LangChain 的訊息用 record_message，
串流的回應要加 stream_options={"include_usage": True}，最後一個片段才有 usage（見 academic_agent 的回答）。
法規問答用了回答快取（rag/answer_cache.py）時用 record_cache_hit 記一筆，沒有 token，saved 是當初整理那個回答花的錢。
"""

import argparse
import contextvars
import json
import os
import re
import sys
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from backend import paths
from backend.logging_config import make_print_logger

log = make_print_logger(__name__)

LOG_DIR = paths.STORAGE_DIR / "usage"

# 每百萬 token 的美元單價：(輸入, 快取的輸入, 輸出)，2026-10 OpenAI 官網的價格。
# API 回傳的模型名稱可能帶日期（gpt-5.4-mini-2026-03-17），比對時會去掉。
PRICES = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "text-embedding-3-large": (0.13, 0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}

# 來源預設看執行的是哪個程式（sys.argv[0] 的檔名），每週更新文件用環境變數指定（子程序各自是不同的程式）
SOURCE_ENV = "NCUXPLORE_USAGE_SOURCE"
SOURCE_NAMES = {
    "main": "網站",
    "run_eval": "法規評估",
    "check_retrieval": "法規評估",
    "run_routing": "選工具評估",
    "scholarship_tools": "整理獎學金",
    "update_documents": "每週更新文件",
}

_request: contextvars.ContextVar[str] = contextvars.ContextVar("usage_request", default="")
_write_lock = threading.Lock()
_process_totals = {"calls": 0, "cost": 0.0}


def start_request() -> str:
    """之後在同一個 context 裡的呼叫（同一個 asyncio task，以及 asyncio.to_thread 帶過去的背景 thread）
    都算同一次提問，統計才算得出每題花多少。網站的聊天 API、法規評估的每一題開頭各呼叫一次。"""
    request_id = uuid.uuid4().hex[:12]
    _request.set(request_id)
    return request_id


def current_source() -> str:
    if os.getenv(SOURCE_ENV):
        return os.environ[SOURCE_ENV]
    stem = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] not in ("", "-c") else ""
    return SOURCE_NAMES.get(stem, stem or "其他")


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _count(obj: Any, *names: str) -> int:
    for name in names:
        value = _field(obj, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _details(obj: Any, *names: str) -> Any:
    for name in names:
        value = _field(obj, name)
        if value is not None:
            return value
    return {}


def normalize_usage(usage: Any) -> Optional[dict]:
    """Chat Completions、Responses API、向量跟 LangChain 的 usage_metadata 欄位名稱都不一樣，統一成
    input、cached（輸入裡用到快取的）、output、reasoning（輸出裡推理的）。拿不到（測試的假物件）回傳 None。"""
    if usage is None:
        return None
    input_tokens = _count(usage, "prompt_tokens", "input_tokens")
    output_tokens = _count(usage, "completion_tokens", "output_tokens")
    if not input_tokens and not output_tokens:
        return None
    input_details = _details(usage, "prompt_tokens_details", "input_tokens_details", "input_token_details")
    output_details = _details(usage, "completion_tokens_details", "output_tokens_details", "output_token_details")
    return {
        "input": input_tokens,
        "cached": min(_count(input_details, "cached_tokens", "cache_read"), input_tokens),
        "output": output_tokens,
        "reasoning": _count(output_details, "reasoning_tokens", "reasoning"),
    }


def base_model(model: str) -> str:
    """去掉 API 回傳的模型名稱後面的日期（gpt-5.4-mini-2026-03-17 -> gpt-5.4-mini）。"""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model or "")


def price_of(model: str) -> Optional[tuple]:
    return PRICES.get(base_model(model))


def estimate_cost(model: str, tokens: dict) -> Optional[float]:
    price = price_of(model)
    if price is None:
        return None
    input_price, cached_price, output_price = price
    uncached = tokens["input"] - tokens["cached"]
    return (uncached * input_price + tokens["cached"] * cached_price + tokens["output"] * output_price) / 1_000_000


def _append(entry: dict, now: datetime) -> None:
    with _write_lock:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record(purpose: str, model: str, usage: Any) -> Optional[float]:
    """記一筆用量。usage 是 API 回應的 .usage，或 LangChain 訊息的 usage_metadata。
    回傳估計的金額（法規問答把整理一題花的錢存進回答快取），沒有 usage 或模型沒有單價時回傳 None。"""
    try:
        tokens = normalize_usage(usage)
        if tokens is None:
            return None
        cost = estimate_cost(model, tokens)
        now = datetime.now()
        entry = {
            "time": now.isoformat(timespec="seconds"), "source": current_source(), "request": _request.get(),
            "purpose": purpose, "model": model, **tokens, "cost": None if cost is None else round(cost, 6),
        }
        _append(entry, now)
        with _write_lock:
            _process_totals["calls"] += 1
            _process_totals["cost"] += cost or 0.0
        return cost
    except Exception as e:
        log(f"[用量紀錄] 記錄失敗（不影響回答）：{e}")
        return None


def record_response(purpose: str, response: Any, model: str = "") -> Optional[float]:
    """OpenAI SDK 的回應（chat.completions、embeddings，或串流最後一個帶 usage 的片段）。"""
    returned = getattr(response, "model", None)
    return record(purpose, returned if isinstance(returned, str) and returned else model, getattr(response, "usage", None))


def record_message(purpose: str, message: Any, model: str = "") -> Optional[float]:
    """LangChain 回傳的 AIMessage（ChatOpenAI 的 ainvoke）。"""
    metadata = getattr(message, "response_metadata", None)
    returned = metadata.get("model_name") if isinstance(metadata, dict) else None
    return record(purpose, returned if isinstance(returned, str) and returned else model, getattr(message, "usage_metadata", None))


CACHE_HIT = "快取命中"


def record_cache_hit(saved: Optional[float]) -> None:
    """法規問答直接用了快取的回答：沒有呼叫模型，記一筆 0 token 的紀錄，saved 是當初整理這個回答花的錢，
    統計時才看得出快取省了多少（summary_lines 另外列，不算進呼叫次數）。"""
    try:
        now = datetime.now()
        _append({
            "time": now.isoformat(timespec="seconds"), "source": current_source(), "request": _request.get(),
            "purpose": CACHE_HIT, "model": "", "input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost": 0.0,
            "saved": round(saved or 0.0, 6),
        }, now)
    except Exception as e:
        log(f"[用量紀錄] 記錄失敗（不影響回答）：{e}")


def process_summary() -> str:
    """這個程序到目前為止記到的用量，評估跑完印一行。"""
    with _write_lock:
        calls, cost = _process_totals["calls"], _process_totals["cost"]
    return f"OpenAI 用量約 US${cost:.2f}（{calls} 次呼叫）"


# ============================================================
# 讀紀錄跟統計
# ============================================================
def load_records(since: datetime, until: Optional[datetime] = None) -> list[dict]:
    """since（含）到 until（不含）之間的紀錄。壞掉的行（例如寫到一半被中斷）跳過。"""
    records = []
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        if path.stem < f"{since:%Y-%m}" or (until and path.stem > f"{until:%Y-%m}"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                when = datetime.fromisoformat(entry["time"])
            except (ValueError, KeyError, TypeError):
                continue
            if when >= since and (until is None or when < until):
                records.append(entry)
    return records


def _money(value: float) -> str:
    return f"US${value:.2f}" if value >= 0.1 or value == 0 else f"US${value:.4f}"


def _tokens(value: float) -> str:
    return f"{value / 1_000_000:.2f}M" if value >= 1_000_000 else f"{value:,.0f}"


def summary_lines(all_records: list[dict]) -> list[str]:
    if not all_records:
        return ["這段時間沒有用量紀錄。"]
    # 用到回答快取的紀錄沒有呼叫模型，不算進呼叫次數跟各用途的平均，最後另外列省了多少
    hits = [r for r in all_records if r.get("purpose") == CACHE_HIT]
    records = [r for r in all_records if r.get("purpose") != CACHE_HIT]
    cost = sum(r["cost"] or 0 for r in records)
    lines = [
        f"合計約 {_money(cost)}，呼叫 {len(records)} 次，"
        f"輸入 {_tokens(sum(r['input'] for r in records))} token（快取 {_tokens(sum(r['cached'] for r in records))}），"
        f"輸出 {_tokens(sum(r['output'] for r in records))} token（推理 {_tokens(sum(r['reasoning'] for r in records))}）",
    ]
    unpriced = sorted({r["model"] for r in records if r["cost"] is None})
    if unpriced:
        lines.append(f"另有 {sum(r['cost'] is None for r in records)} 次呼叫的模型沒有單價，沒算進金額：{'、'.join(unpriced)}")

    def grouped(title: str, key) -> None:
        groups = defaultdict(list)
        for r in records:
            groups[key(r)].append(r)
        lines.extend(["", title])
        # 名稱放最後：中文字在終端機佔兩格，放前面對不齊
        for name, items in sorted(groups.items(), key=lambda kv: -sum(r["cost"] or 0 for r in kv[1])):
            n = len(items)
            lines.append(
                f"  {_money(sum(r['cost'] or 0 for r in items)):>10}  {n:>5} 次"
                f"  平均輸入 {_tokens(sum(r['input'] for r in items) / n):>7}"
                f"  平均輸出 {_tokens(sum(r['output'] for r in items) / n):>6}  {name}"
            )

    grouped("依來源", lambda r: r["source"])
    grouped("依用途", lambda r: f"{r['purpose']}（{base_model(r['model'])}）")

    days = defaultdict(float)
    for r in records:
        days[r["time"][:10]] += r["cost"] or 0
    lines.extend(["", "每天"] + [f"  {day}  {_money(days[day]):>10}" for day in sorted(days)])

    if hits:
        saved = sum(r.get("saved") or 0 for r in hits)
        lines.extend(["", f"法規問答快取：{len(hits)} 次直接用了之前整理的回答，約省下 {_money(saved)}"])

    # 有標提問編號的（網站的每次提問、評估的每一題），算每題花多少。法規問答跟其他功能分開算，差了好幾倍，
    # 用到快取的法規問答也分開算（只剩選工具跟建議問題）。
    requests = defaultdict(list)
    for r in all_records:
        if r.get("request"):
            requests[(r["source"], r["request"])].append(r)
    by_source = defaultdict(lambda: {"法規問答": [], "法規問答（快取）": [], "其他": []})
    for (source, _), items in requests.items():
        if any(r["purpose"] in ("挑文件", "回答") for r in items):
            kind = "法規問答"
        elif any(r["purpose"] == CACHE_HIT for r in items):
            kind = "法規問答（快取）"
        else:
            kind = "其他"
        by_source[source][kind].append(sum(r["cost"] or 0 for r in items))
    for source, kinds in by_source.items():
        lines.extend(["", f"{source}的每次提問"])
        for kind, costs in kinds.items():
            if costs:
                costs.sort()
                lines.append(
                    f"  {kind}  {len(costs)} 次，平均 {_money(sum(costs) / len(costs))}，"
                    f"中位數 {_money(costs[len(costs) // 2])}，最多 {_money(costs[-1])}"
                )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI 用量統計（storage/usage/ 的紀錄）")
    parser.add_argument("--days", type=int, default=7, help="最近幾天（預設 7 天）")
    parser.add_argument("--month", help="改看某個月，例如 2026-10")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.month:
        since = datetime.strptime(args.month, "%Y-%m")
        until = (since + timedelta(days=32)).replace(day=1)
        title = f"{args.month} 的 OpenAI 用量"
    else:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        since, until = today - timedelta(days=args.days - 1), None
        title = f"{since:%Y-%m-%d} 到今天的 OpenAI 用量"
    print(f"{title}（金額用公開單價估算，實際以 OpenAI 後台為準）")
    print("\n".join(summary_lines(load_records(since, until))))


if __name__ == "__main__":
    main()
