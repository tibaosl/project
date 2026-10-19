"""常見問題的標準答案：對照 data/ 的文件人工整理好的回答（standard_answers.json），使用者問到同一件事時直接用，
不用再讓模型挑文件、讀全文回答。

為什麼：回答快取（answer_cache.py）只認一字不差的同一題，「補辦學生證要多少錢」跟「學生證掉了補辦費用」算兩題。
標準答案用語意比對，換句話說也配得到，而且答案是對照文件看過的，比模型每次重新讀文件回答穩定
（評估分數大約 85～92%，標準答案涵蓋的題目幾乎都對）。

配錯比查不到更糟（問資工系的人拿到電機系的答案），所以配對分三關，都過了才用：
1. 向量：問題跟每一則標準答案的各種問法比相似度，取最像的幾則（只是找候選，門檻不高）。
2. 規則（不呼叫模型）：問題裡有標準問法跟答案都沒寫到的數字（年度、分數）不算；標準答案只適用某些學制（例如大學部），
   問題要講到那個學制；只適用某些系所的，問題要講到那個系所；全校通用、但系所可能另有規定的，
   問題講到系所就不算（any_unit 標成 true 的才不管系所，例如補辦學生證）。
3. 模型確認：gpt-5.4-mini 看使用者的問題跟候選的標準答案，判斷能不能完整回答，不確定就不用（約 US$0.001）。

只用在一句話就問完的問題（對話紀錄只有這一句），英文問題、問「今天」「這週」「還來得及嗎」這類跟今天日期有關的也不用。

文件改了就停用：每則標準答案記下根據的文件跟文件內容的 hash，文件內容變了、找不到、被標成舊版，
那一則就先不用（回到一般流程），每週更新文件的紀錄會列出要重看的（--check）。

    python -m backend.rag.standard_answers                  # 幾則標準答案、哪些因為文件改了要重看
    python -m backend.rag.standard_answers --check          # 只列要重看的
    python -m backend.rag.standard_answers --try "問題"     # 看一個問題會配到哪一則（會呼叫模型確認）

新增或修改標準答案：改 standard_answers.json，sources 的 text_sha256 用 --hash 檔名 算，
改完跑 rag_eval/run_standard.py（配對測試跟答案評分）。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
from openai import OpenAI

from backend import usage_log
from backend.rag import rag_documents

ANSWERS_FILE = Path(__file__).resolve().parent / "standard_answers.json"
ENABLED = os.getenv("RAG_STANDARD_ANSWERS", "1") != "0"
VERIFY_MODEL = os.getenv("RAG_STANDARD_VERIFY_MODEL", "gpt-5.4-mini")
VERIFY_REASONING = os.getenv("RAG_STANDARD_VERIFY_REASONING", "low")
# 向量相似度只是找候選，真正決定的是規則跟模型確認，門檻放低一點，換句話說的問法才不會被擋掉
MIN_SIMILARITY = float(os.getenv("RAG_STANDARD_MIN_SIMILARITY", "0.5"))
CANDIDATES = 3
# 用到標準答案時，用量紀錄記的「省下」：2026-10-13 量的法規問答一題的平均花費（估計值）
TYPICAL_COST = 0.09

# 問題講到的學制。「研究生」「研究所」碩博都有可能
_DEGREE_WORDS = {
    "大學部": r"大學部|學士|大學生|大[一二三四五](?!英文|國文)|本科",
    "碩士在職專班": r"在職專班|碩專|EMBA",
    "碩士班": r"碩士(?!在職)|碩班|碩[一二三]",
    "博士班": r"博士|博班|博[一二三四五六七]",
}
_GRADUATE_WORDS = r"研究生|研究所"
# 跟今天的日期有關的問法，標準答案寫的是規定本身，回答不了「今天還能不能」
_RELATIVE_DATE = re.compile(
    r"今天|今日|明天|昨天|這週|這周|本週|本周|下週|下周|這禮拜|下禮拜|這個月|下個月|本月|今年|明年|這學期|下學期|"
    r"最近|現在|目前|還來得及|來不及|過了嗎|截止了|還能|還可以|剩幾天|還有幾天"
)

VERIFY_INSTRUCTIONS = """你是中央大學校園法規問答系統的「標準答案配對」步驟。系統有一些人工對照文件整理好的標準答案，
你要判斷使用者的問題能不能直接用其中一則回答。

規則：
1. 使用者問的事情要跟標準答案回答的事情相同。只是相關、或使用者問的比標準答案更細（例如標準答案講申請方式，
   使用者問的是被拒絕之後怎麼辦），都不算。
2. 使用者提到的條件（系所、學制、身分、入學年度、金額、分數、時間）標準答案都要有涵蓋，而且答案不會因為這個條件而不同。
   標準答案沒寫到的條件不算涵蓋。反過來，標準答案已經分情況列出（例如依入學年度、轉入年級、學制各寫一種規定），
   使用者沒說自己是哪一種也可以用，因為每種情況答案都寫了。
3. 使用者問好幾件事時，標準答案要每一件都回答到。
4. 不確定就選 none。用錯標準答案會讓使用者拿到錯的資訊，選 none 只是改成重新查文件回答，沒有壞處。

回答 match：可以用的那一則的編號（只寫編號，不加括號），都不行就是 none。reason 用一句話說明理由。"""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"match": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["match", "reason"],
    "additionalProperties": False,
}


@dataclass
class StandardAnswer:
    id: str
    questions: list[str]
    answer: str
    sources: list[dict]
    units: list[str] = field(default_factory=list)    # 只適用這些系所，空的是全校
    degrees: list[str] = field(default_factory=list)  # 只適用這些學制，空的是不分學制（答案有分學制寫清楚）
    any_unit: bool = False                             # 全校一致、系所不能另訂（例如補辦學生證），問題講到系所也能用
    reviewed: str = ""
    documents: list = field(default_factory=list)      # 現在目錄裡對應的文件（CatalogDocument）
    stale: str = ""                                    # 不能用的原因（文件改了、找不到、有新版），空字串是能用

    @property
    def answer_hash(self) -> str:
        return hashlib.sha256(self.answer.encode("utf-8")).hexdigest()[:16]


_client: Optional[OpenAI] = None
_lock = threading.Lock()
_loaded: dict = {"key": None, "answers": [], "vectors": None, "owners": None}


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _by_name(catalog: list) -> dict:
    """檔名（含內容一樣、合併掉的檔名）→ 目錄裡的文件。"""
    names = {}
    for doc in catalog:
        for name in [doc.file_name, *doc.duplicates]:
            names[name] = doc
    return names


def _resolve(entry: StandardAnswer, names: dict) -> None:
    entry.documents, entry.stale = [], ""
    for source in entry.sources:
        doc = names.get(source.get("file", ""))
        if doc is None:
            entry.stale = f"找不到 {source.get('file')}"
            return
        if text_hash(doc.text) != source.get("text_sha256"):
            entry.stale = f"{source['file']} 的內容改了"
            return
        if doc.superseded_by and not doc.cohort_edition:
            entry.stale = f"{source['file']} 有新版了"
            return
        entry.documents.append(doc)


def read_file(path: Path = None) -> list[StandardAnswer]:
    data = json.loads((path or ANSWERS_FILE).read_text(encoding="utf-8"))
    fields = StandardAnswer.__dataclass_fields__
    return [StandardAnswer(**{k: v for k, v in item.items() if k in fields and k not in ("documents", "stale")})
            for item in data]


def load(catalog: list) -> list[StandardAnswer]:
    """全部的標準答案（含停用的），對應到目錄裡的文件、算好每種問法的向量。目錄或檔案沒變就用記住的。"""
    try:
        mtime = ANSWERS_FILE.stat().st_mtime_ns
    except OSError:
        return []
    with _lock:
        if _loaded["key"] != (id(catalog), mtime):
            answers = read_file()
            names = _by_name(catalog)
            for entry in answers:
                _resolve(entry, names)
            _loaded.update(key=(id(catalog), mtime), answers=answers, vectors=None, owners=None)
        return _loaded["answers"]


def _question_vectors(answers: list[StandardAnswer]) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """每一則能用的標準答案的每一種問法的向量（一列一種問法），owners 是每一列屬於第幾則。
    快取在 storage/rag/embeddings/（key 是問法文字的 hash），問法沒變就不重算。"""
    with _lock:
        if _loaded["vectors"] is not None:
            return _loaded["vectors"], _loaded["owners"]
    rows = [(i, q) for i, entry in enumerate(answers) if not entry.stale for q in entry.questions]
    if not rows:
        return None, None
    cache_path = rag_documents.CACHE_DIR / "embeddings" / f"standard-{rag_documents.EMBEDDING_MODEL}-{rag_documents.EMBEDDING_DIMENSIONS}.npz"
    cache: dict[str, np.ndarray] = {}
    try:
        with np.load(cache_path) as stored:
            cache = dict(zip(stored["keys"].tolist(), stored["vectors"]))
    except (OSError, ValueError, KeyError):
        pass
    keys = [text_hash(q) for _, q in rows]
    missing = [(key, q) for key, (_, q) in zip(keys, rows) if key not in cache]
    if missing:
        unique = dict(missing)
        for key, vector in zip(unique, rag_documents.embed_texts(list(unique.values()), purpose="標準答案向量")):
            cache[key] = vector
        current = {key: cache[key] for key in dict.fromkeys(keys)}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = rag_documents._temp_path(cache_path, ".tmp.npz")
        np.savez(tmp, keys=np.array(list(current)), vectors=np.stack(list(current.values())))
        rag_documents.replace_file(tmp, cache_path)
    vectors = np.stack([cache[key] for key in keys])
    owners = np.array([i for i, _ in rows])
    with _lock:
        _loaded.update(vectors=vectors, owners=owners)
    return vectors, owners


# ============================================================
# 規則
# ============================================================
def degrees_in(text: str) -> set[str]:
    found = {degree for degree, pattern in _DEGREE_WORDS.items() if re.search(pattern, text)}
    if re.search(_GRADUATE_WORDS, text):
        found |= {"碩士班", "博士班"}
    return found


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", text)))


def asks_about_today(text: str) -> bool:
    return bool(_RELATIVE_DATE.search(text))


def passes_rules(question: str, entry: StandardAnswer, user_units: set[str]) -> bool:
    """不呼叫模型的檢查（見檔案開頭的第 2 關）。user_units 是問題講到的系所、學院（正式名稱）。"""
    # 問題裡的數字（年度、分數、學分）標準問法或標準答案裡要有，沒寫到的情況標準答案就沒涵蓋
    if not numbers_in(question) <= numbers_in(" ".join(entry.questions) + " " + entry.answer):
        return False
    asked = degrees_in(question)
    if entry.degrees and not asked & set(entry.degrees):
        return False
    if entry.units:
        return bool(user_units & set(entry.units))
    return entry.any_unit or not user_units


# ============================================================
# 配對
# ============================================================
def candidates(vector: np.ndarray, answers: list[StandardAnswer], limit: int = CANDIDATES) -> list[tuple[StandardAnswer, float]]:
    """跟問題最像的幾則（每則取它最像的那種問法），相似度低於 MIN_SIMILARITY 的不要。"""
    vectors, owners = _question_vectors(answers)
    if vectors is None:
        return []
    similarity = vectors @ vector
    best: dict[int, float] = {}
    for row in np.argsort(-similarity):
        if similarity[row] < MIN_SIMILARITY or len(best) >= limit:
            break
        best.setdefault(int(owners[row]), float(similarity[row]))
    return [(answers[i], score) for i, score in best.items()]


def verify(question: str, entries: list[StandardAnswer],
           trace: Optional[Callable[[str], None]] = None) -> Optional[StandardAnswer]:
    """第 3 關：請小模型判斷能不能用其中一則回答。"""
    blocks = []
    for entry in entries:
        scope = []
        if entry.units:
            scope.append(f"只適用：{'、'.join(entry.units)}")
        if entry.degrees:
            scope.append(f"只適用學制：{'、'.join(entry.degrees)}")
        blocks.append(f"[{entry.id}] 標準問法：{'／'.join(entry.questions[:4])}\n"
                      f"{'｜'.join(scope) or '適用：全校'}\n標準答案：\n{entry.answer}")
    response = _openai().chat.completions.create(
        model=VERIFY_MODEL,
        reasoning_effort=VERIFY_REASONING,
        messages=[
            {"role": "system", "content": VERIFY_INSTRUCTIONS},
            {"role": "user", "content": "候選的標準答案：\n\n" + "\n\n----------\n\n".join(blocks)
             + f"\n\n使用者的問題：{question}"},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "standard_match", "schema": VERIFY_SCHEMA, "strict": True}},
    )
    usage_log.record_response("標準答案確認", response, VERIFY_MODEL)
    result = json.loads(response.choices[0].message.content)
    if trace:
        trace(f"模型確認：{result.get('match')}（{result.get('reason', '')}）")
    # 模型偶爾會照候選的寫法回「[編號]」
    chosen = str(result.get("match", "")).strip().strip("[]【】").strip()
    return next((entry for entry in entries if entry.id == chosen), None)


def match(question: str, vector: np.ndarray, catalog: list, user_units: set[str],
          trace: Optional[Callable[[str], None]] = None) -> Optional[StandardAnswer]:
    """配到的標準答案，沒有就回傳 None。vector 是問題的向量（跟卡片同一種，長度 1）。
    trace 給 --try 跟測試看每一關的結果。"""
    note = trace or (lambda _: None)
    answers = load(catalog)
    found = candidates(vector, answers)
    note(f"向量候選：{[(e.id, round(s, 3)) for e, s in found]}")
    passed = [entry for entry, _ in found if passes_rules(question, entry, user_units)]
    note(f"通過規則：{[e.id for e in passed]}")
    if not passed:
        return None
    return verify(question, passed, note)


def available(catalog: list) -> bool:
    """有沒有能用的標準答案（沒有就不用算問題的向量）。"""
    return any(not entry.stale for entry in load(catalog))


def find(answer_id: str, catalog: list) -> Optional[StandardAnswer]:
    return next((entry for entry in load(catalog) if entry.id == answer_id), None)


# ============================================================
# 指令
# ============================================================
def stale_lines(answers: Iterable[StandardAnswer]) -> list[str]:
    return [f"  {entry.id}：{entry.stale}" for entry in answers if entry.stale]


def main() -> None:
    parser = argparse.ArgumentParser(description="常見問題的標準答案（standard_answers.json）")
    parser.add_argument("--check", action="store_true", help="只列出因為文件改了要重看的標準答案")
    parser.add_argument("--try", dest="question", help="看這個問題會配到哪一則（會呼叫模型確認）")
    parser.add_argument("--hash", metavar="檔名", help="算 data/ 裡這份文件的 text_sha256（寫進 sources 用）")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from backend.rag import academic_agent

    catalog = academic_agent.get_catalog()
    if args.hash:
        doc = _by_name(catalog).get(args.hash)
        print(text_hash(doc.text) if doc else f"目錄裡沒有 {args.hash}")
        return
    answers = load(catalog)
    if args.question:
        vector = academic_agent.question_vectors([args.question])[0]
        chosen = match(args.question, vector, catalog, academic_agent.mentioned_units(args.question), trace=print)
        print(f"\n配到：{chosen.id if chosen else '沒有，交給一般流程'}")
        return
    stale = stale_lines(answers)
    if args.check:
        print("\n".join(stale) if stale else f"標準答案 {len(answers)} 則，根據的文件都沒變。")
        return
    print(f"標準答案 {len(answers)} 則，能用的 {len(answers) - len(stale)} 則，"
          f"問法共 {sum(len(e.questions) for e in answers)} 種")
    if stale:
        print("文件改了、要重看的：\n" + "\n".join(stale))


if __name__ == "__main__":
    main()
