"""校園法規 RAG 的評估：用 questions.json 的題目逐題問 RAG，再請模型依照
「標準答案要點」跟「不可以出現」評分，並檢查有沒有引用到正確的文件。

改 RAG 之前先跑一次、改完再跑一次，分數沒變差才算改好（不然很容易修好一題、
弄壞另一題）。需要 data/ 裡的文件跟 .env 的 OPENAI_API_KEY：

    python rag_eval/run_eval.py                    # 全部題目
    python rag_eval/run_eval.py --only eng_ fee_   # 只跑 id 開頭符合的題目
    python rag_eval/run_eval.py --module 舊版.py   # 評估另一個實作（例如舊版）
    python rag_eval/run_eval.py --file holdout.json  # 保留測試題

questions.json 是開發時一邊看一邊調整用的；holdout.json 是保留測試題，
平常不要照著它調 prompt，只在改完之後跑一次確認沒有只對 questions.json 過度擬合。

結果會存到 rag_eval/results/，逐題的答案跟評分理由都在裡面。
"""

import argparse
import importlib
import importlib.util
import json
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

load_dotenv(ROOT / ".env")

JUDGE_MODEL = "gpt-5.4"
EXPECT_DESCRIPTIONS = {
    "answer": "文件裡有答案，應該根據文件正確回答。",
    "partial": "文件只有部分資訊，應該回答查得到的部分，並明確說明查不到的部分，不能拿其他文件（例如別的學院）的資料硬答。",
    "not_found": "文件庫裡沒有相關規定，應該明確說查不到，不能編造答案，也不能拿不相關的文件硬套。",
    "clarify_or_variants": "答案會因條件（例如學院、學制）不同，應該反問使用者，或分情況列出，不能只給單一答案。",
}

JUDGE_PROMPT = """你是校園法規問答系統的評分員，請判斷系統的回答是否正確。

使用者先前問過：{history}
這一題：{question}

期望行為：{expect}
標準答案要點：
{facts}
不可以出現：
{must_not}

系統回答：
{answer}

評分標準（標了「加分項」的要點沒提到不扣分）：
- correct：符合期望行為，要點都有提到（措辭不同沒關係），沒有錯誤資訊，也沒有觸犯「不可以出現」。
- partial：方向正確但漏掉部分要點，或夾帶了多餘但不至於誤導的內容。
- wrong：答錯、數字錯、觸犯「不可以出現」、該說查不到卻編造答案、該回答卻說查不到。

reason 用一兩句話說明扣分原因（correct 可以留空）。"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "wrong"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

SCORES = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}


def _norm(name: str) -> str:
    return unicodedata.normalize("NFKC", name).split(" (第")[0].strip()


def equivalent_names(module, names) -> set:
    """內容相同、在目錄裡合併成一份的文件都算同一份（例如教務章則彙編跟課務組的法規頁
    放了同一個辦法，目錄只留其中一個檔名，題庫寫的是另一個也算挑對）。
    """
    groups = {}
    for doc in module.get_catalog():
        group = {_norm(doc.file_name), *(_norm(name) for name in doc.duplicates)}
        for name in group:
            groups[name] = group
    result = set()
    for name in names:
        result |= groups.get(_norm(name), {_norm(name)})
    return result


def load_module(spec: str):
    if spec.endswith(".py"):
        path = Path(spec).resolve()
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module
    return importlib.import_module(spec)


def history_string(history: list[str], question: str) -> str:
    """跟 supervisor_agent 組歷史的方式一樣：使用者說過的話（含這一輪），以及系統的反問。
    題庫裡的系統反問直接寫成 "[系統反問]: ..."，其他句子當成使用者說的話。
    """
    turns = [q if q.startswith("[") else f"[使用者]: {q}" for q in history + [question]]
    return " -> ".join(turns)


def judge(client: OpenAI, item: dict, answer: str) -> dict:
    prompt = JUDGE_PROMPT.format(
        history="、".join(item.get("history", [])) or "（沒有）",
        question=item["question"],
        expect=EXPECT_DESCRIPTIONS[item["expect"]],
        facts="\n".join(f"- {f}" for f in item["facts"]),
        must_not="\n".join(f"- {m}" for m in item.get("must_not", [])) or "（無）",
        answer=answer,
    )
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        reasoning_effort="medium",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": "verdict", "schema": JUDGE_SCHEMA, "strict": True}},
    )
    return json.loads(response.choices[0].message.content)


def run_one(module, client: OpenAI, item: dict) -> dict:
    started = time.time()
    try:
        result = module.query_academic_knowledge(item["question"], history_string(item.get("history", []), item["question"]))
    except Exception as e:
        result = {"answer": f"（執行失敗：{e}）", "sources": []}
    elapsed = time.time() - started

    answer = result.get("answer", "")
    sources = result.get("sources", [])
    gold = {_norm(g) for g in item.get("gold", [])}
    cited = equivalent_names(module, sources)
    verdict = judge(client, item, answer)
    return {
        "id": item["id"],
        "question": item["question"],
        "expect": item["expect"],
        "answer": answer,
        "sources": sources,
        "source_hit": bool(gold & cited) if gold else None,
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
        "seconds": round(elapsed, 1),
    }


def check_routing(module, item: dict) -> dict:
    """只跑挑文件那一步：該回答的題目要挑到標準文件，查無資料的題目要判成 not_found。"""
    started = time.time()
    plan = module.choose_documents(item["question"], history_string(item.get("history", []), item["question"]), module.get_catalog())
    picked = equivalent_names(module, [doc.file_name for doc in plan["documents"]])
    gold = {_norm(g) for g in item.get("gold", [])}
    if item["expect"] == "not_found":
        ok = plan["decision"] == "not_found"
    elif item["expect"] == "clarify_or_variants":
        ok = plan["decision"] in ("clarify", "answer")
    else:
        ok = plan["decision"] == "answer" and bool(gold & picked)
    return {"id": item["id"], "ok": ok, "decision": plan["decision"], "picked": sorted(picked), "seconds": round(time.time() - started, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="academic_agent", help="要評估的模組名稱或 .py 檔路徑")
    parser.add_argument("--only", nargs="*", help="只跑 id 以這些字串開頭的題目")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--label", default="", help="結果檔名的標籤")
    parser.add_argument("--file", default="questions.json", help="題庫檔（rag_eval/ 底下），holdout.json 是保留測試題")
    parser.add_argument("--router-only", action="store_true", help="只檢查挑文件那一步（快、便宜，不評分答案）")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    items = json.loads((Path(__file__).parent / args.file).read_text(encoding="utf-8"))
    if args.only:
        items = [i for i in items if any(i["id"].startswith(p) for p in args.only)]

    module = load_module(args.module)
    if args.router_only:
        module.get_catalog()  # 先載好目錄，平行的查詢才不會在計時裡排隊等它
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            checks = list(pool.map(lambda item: check_routing(module, item), items))
        for c in checks:
            if not c["ok"]:
                print(f"X {c['id']:<28} {c['decision']:<10} 挑了：{c['picked']}")
        seconds = sorted(c["seconds"] for c in checks)
        print(f"\n挑文件正確 {sum(c['ok'] for c in checks)} / {len(checks)}｜耗時中位數 {seconds[len(seconds) // 2]:.1f}s")
        return

    client = OpenAI()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda item: run_one(module, client, item), items))

    for r in results:
        mark = {"correct": "O", "partial": "△", "wrong": "X"}[r["verdict"]]
        hit = "" if r["source_hit"] is None else ("來源✓" if r["source_hit"] else "來源✗")
        print(f"{mark} {r['id']:<28} {r['seconds']:5.1f}s {hit}  {r['reason'][:80]}")

    score = sum(SCORES[r["verdict"]] for r in results)
    with_gold = [r for r in results if r["source_hit"] is not None]
    hits = sum(1 for r in with_gold if r["source_hit"])
    seconds = sorted(r["seconds"] for r in results)
    print(
        f"\n分數 {score:.1f} / {len(results)}（{score / max(len(results), 1):.0%}）"
        f"｜correct {sum(r['verdict'] == 'correct' for r in results)}"
        f"｜partial {sum(r['verdict'] == 'partial' for r in results)}"
        f"｜wrong {sum(r['verdict'] == 'wrong' for r in results)}"
        f"｜引用到正確文件 {hits}/{len(with_gold)}"
        f"｜耗時中位數 {seconds[len(seconds) // 2]:.1f}s"
    )

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    label = f"_{args.label}" if args.label else ""
    out_path = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}{label}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"逐題結果：{out_path}")


if __name__ == "__main__":
    main()
