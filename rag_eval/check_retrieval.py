"""只檢查初篩（academic_agent.retrieve_candidates）：標準文件有沒有排進前 K 份。不呼叫 LLM，
只花算問題向量的一點點錢，用來調 ROUTER_CANDIDATES 或改初篩的排序方式：

    python rag_eval/check_retrieval.py                  # 開發題，列出標準文件沒進前 60 的題目
    python rag_eval/check_retrieval.py --file holdout.json --k 40 60 100
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.rag import academic_agent as agent  # noqa: E402
from run_eval import _norm, history_string  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="questions.json")
    parser.add_argument("--k", type=int, nargs="*", default=[20, 40, 60, 100])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    items = [q for q in json.loads((Path(__file__).parent / args.file).read_text(encoding="utf-8")) if q.get("gold")]
    catalog = agent.get_catalog()
    by_name = {}
    for doc in catalog:
        for name in [doc.file_name, *doc.duplicates]:
            by_name[_norm(name)] = doc.doc_id

    largest = max(args.k)
    ranks = []
    for item in items:
        candidates = agent.retrieve_candidates(item["question"], history_string(item.get("history", []), item["question"]),
                                               catalog, limit=largest)
        order = [doc.doc_id for doc in candidates]
        gold = {by_name.get(_norm(g)) for g in item["gold"]} - {None}
        rank = min((order.index(g) + 1 for g in gold if g in order), default=None)
        ranks.append((item["id"], rank, len(gold)))
        if rank is None or rank > 60:
            print(f"{item['id']:<30} 第 {rank or '>' + str(largest)} 名  標準文件 {len(gold)} 份 {'（目錄裡找不到標準文件）' if not gold else ''}")
    for k in args.k:
        hit = sum(1 for _, rank, _ in ranks if rank is not None and rank <= k)
        print(f"前 {k:>3} 份含標準文件：{hit}/{len(ranks)}")


if __name__ == "__main__":
    main()
