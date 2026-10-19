"""常見問題標準答案（backend/rag/standard_answers.py）的評估，兩部分：

1. 配對測試（standard_match.json）：每題標了應該配到哪一則標準答案，或不該配到任何一則（null，多半是故意找的
   近似題，例如問碩士生的國科會獎學金、問物理系自己的書卷獎）。最重要的數字是「配錯」：配到別則、或不該配卻配了，
   要是 0 才能用。「漏配」只是改成一般流程重新查文件回答，沒有壞處。
2. 答案評分（--judge）：標準答案標了 eval 的（對應 questions*.json 的題目），用 run_eval.py 同一套評分，
   看標準答案本身對不對。

    python rag_eval/run_standard.py            # 配對測試（會呼叫小模型確認，很便宜）
    python rag_eval/run_standard.py --judge    # 再加上答案評分
    python rag_eval/run_standard.py --only 學生證  # 只跑題目裡有這個字的

改了 standard_answers.json（新增、修改標準答案或問法）或配對的規則、prompt，都要跑一次。
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RAG_ANSWER_CACHE"] = "0"  # 只測配對，不要寫進網站的回答快取

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from backend import usage_log  # noqa: E402
from backend.rag import academic_agent as agent  # noqa: E402
from backend.rag import standard_answers  # noqa: E402

load_dotenv(ROOT / ".env")
HERE = Path(__file__).resolve().parent


def run_match(case: dict, catalog: list) -> dict:
    usage_log.start_request()
    question = case["question"]
    steps: list[str] = []
    if standard_answers.asks_about_today(question):
        chosen = None
        steps.append("問到今天的日期，不配標準答案")
    else:
        vector = agent.question_vectors([question])[0]
        chosen = standard_answers.match(question, vector, catalog, agent.mentioned_units(question), trace=steps.append)
    got = chosen.id if chosen else None
    expect = case.get("expect")
    # 兩則都答得完整的題目（例如當兵跟休學年限，休學那則跟兵役那則都有寫），expect 寫成清單，配到其中一則都對
    if got == expect or (isinstance(expect, list) and got in expect):
        verdict = "ok"
    elif got is None:
        verdict = "漏配"
    else:
        verdict = "配錯"
    return {**case, "got": got, "verdict": verdict, "steps": steps}


def judge_answers(entries: list, workers: int) -> list[dict]:
    sys.path.insert(0, str(HERE))
    from run_eval import SCORES, judge  # noqa: E402

    items = {}
    for name in ("questions.json", "questions_campus.json", "questions_units.json", "questions_dept.json"):
        for item in json.loads((HERE / name).read_text(encoding="utf-8")):
            items[f"{name}:{item['id']}"] = item
    client = OpenAI()
    tasks = [(entry, items[ref]) for entry in entries for ref in entry.get("eval", []) if ref in items]

    def one(task):
        entry, item = task
        usage_log.start_request()
        result = judge(client, item, entry["answer"])
        return {"id": entry["id"], "eval": item["id"], "verdict": result["verdict"], "reason": result["reason"],
                "score": SCORES[result["verdict"]]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, tasks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", action="store_true", help="另外用評分模型檢查標準答案本身")
    parser.add_argument("--only", help="只跑題目裡有這個字的")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    catalog = agent.get_catalog()
    entries = standard_answers.load(catalog)
    stale = standard_answers.stale_lines(entries)
    if stale:
        print("文件改了、這次測不到的標準答案：\n" + "\n".join(stale))
    cases = json.loads((HERE / "standard_match.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if args.only in c["question"]]
    standard_answers._question_vectors(entries)  # 先算好問法的向量，平行跑的時候才不會各算一次
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda case: run_match(case, catalog), cases))

    for r in results:
        if r["verdict"] != "ok":
            print(f"{r['verdict']}｜{r['question']}｜應該 {r['expect']}，配到 {r['got']}")
            for step in r["steps"]:
                print(f"    {step}")
    positives = [r for r in results if r["expect"]]
    wrong = [r for r in results if r["verdict"] == "配錯"]
    print(f"\n配對：{len(results)} 題，配錯 {len(wrong)} 題（要是 0），"
          f"應該配到的 {len(positives)} 題配到 {sum(r['verdict'] == 'ok' for r in positives)} 題，"
          f"不該配的 {len(results) - len(positives)} 題都沒配到的有 "
          f"{sum(r['verdict'] == 'ok' for r in results if not r['expect'])} 題")

    if args.judge:
        raw = json.loads(standard_answers.ANSWERS_FILE.read_text(encoding="utf-8"))
        judged = judge_answers(raw, args.workers)
        for j in judged:
            if j["verdict"] != "correct":
                print(f"{j['verdict']}｜{j['id']}（{j['eval']}）｜{j['reason'][:120]}")
        if judged:
            score = sum(j["score"] for j in judged)
            print(f"\n標準答案評分：{score:.1f} / {len(judged)}（{score / len(judged):.0%}）")
    print(usage_log.process_summary())


if __name__ == "__main__":
    main()
