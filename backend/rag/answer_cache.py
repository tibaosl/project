"""法規問答的回答快取：同一個問題（連同對話紀錄）再被問到時，直接用之前整理好的回答，不再呼叫模型。

為什麼要快取：法規問答一題約 US$0.09（挑文件 0.055、回答 0.031，2026-10-13 用 usage_log 量的），是只經過
選工具的功能的二十倍，學生問的問題又很集中（補辦學生證、休學、畢業學分）。用到快取時這一題只剩選工具約
US$0.004，回答也是馬上出來，不用等十幾秒。

規則（查詢流程在 academic_agent.query_academic_knowledge_stream）：
- 鍵：對話紀錄（含這一次的問題，去掉空白、標點、「請問」，全形半形視為一樣）、回答語言、學年度學期，
  加上挑文件跟回答的 prompt、模型（academic_agent.answer_cache_key 一起算），改了 prompt 或模型舊的快取自然用不到。
  問題提到「今天」「什麼時候」「截止」這類跟日期有關的字時再加上日期，回答可能會依今天的日期寫「已經截止」。
- 兩次挑到一樣的文件才開始用：第一次問的回答先存起來但不用，第二次照樣問模型，兩次的決定（回答、反問、
  查無資料）一樣，回答的話兩次最相關的那份文件都在對方挑的文件裡，才算確認，第三次起直接用。模型偶爾會
  挑錯文件或誤判查無資料（同一題跑十幾次錯一次），只問一次就存下來的話，錯的回答會一直給每個人看。
- 文件變了就重問：存的時候記下挑到的文件的指紋（內容、卡片、新舊版標記）跟當時整份目錄的版本。目錄換了
  （每週更新文件）之後第一次用到時，挑到的文件改了或不見了，或重新初篩的候選裡有新增、改版的文件，就當作
  沒有快取（見 academic_agent.cached_documents）。
- 超過 MAX_AGE_DAYS 天的重新問一次，兩次挑的文件一樣就直接確認。
- 使用者按「重新查詢」時不用快取，新的回答存回去，要再被確認一次才會給別人用。
- 回答下面的「你可能還想問」（或反問的回答選項）也跟著回答存（remember_suggestions），用到快取時一起沿用。
- 同一段對話被選工具的模型導到法規問答兩次以上（remember_route，登入跟沒登入分開），回答也確認過的話，
  supervisor 直接用快取回答，連選工具的模型都不用問（academic_agent.cached_answer_ready）。

存在 storage/rag/answers/：一個鍵一個 JSON（裡面有原本的問題跟對話紀錄，「重新查詢」要用，只存在這台電腦），
catalogs/ 是每個目錄版本的文件指紋清單。評估（rag_eval/run_eval.py）會關掉快取，量的才是每次重新回答的分數。

看統計、清快取，在專案根目錄執行：

    python -m backend.rag.answer_cache                # 快取幾題、用了幾次、最常被問的問題
    python -m backend.rag.answer_cache --delete 學生證  # 刪掉對話裡有這個字的快取（發現回答有錯時）
    python -m backend.rag.answer_cache --clear        # 全部刪掉
"""

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from backend import paths
from backend.rag import rag_documents

CACHE_DIR = paths.STORAGE_DIR / "rag" / "answers"
ENABLED = os.getenv("RAG_ANSWER_CACHE", "1") != "0"
# 改了法規問答的流程而且會影響回答時加一（prompt、模型已經算進鍵裡，不用改這個）
CACHE_VERSION = "1"
FORMAT = 1
MAX_AGE_DAYS = 30
MAX_ENTRIES = 5000
KEEP_CATALOG_VERSIONS = 20

_KEY_RE = re.compile(r"[0-9a-f]{32}")
_GREETING_RE = re.compile(r"^(?:(?:想|我想|想要)?請問(?:一下)?|(?:我)?想問(?:一下)?|你好|您好|哈囉|嗨|hi|hello)+")
# 回答可能會用到今天日期的問題（「截止了嗎」「這週」「什麼時候」），鍵要加上日期，隔天就重新回答
_DATE_SENSITIVE_RE = re.compile(
    r"今天|今日|明天|明日|昨天|後天|這週|這周|本週|本周|下週|下周|上週|上周|禮拜|星期|這個月|下個月|上個月|本月|"
    r"今年|明年|去年|最近|近期|即將|快到|來得及|來不及|截止|過了嗎|現在|目前|什麼時候|甚麼時候|何時|幾號|幾月|"
    r"哪一?天|日期|期限|開始|結束|幾天|"
    r"\b(?:today|tomorrow|yesterday|tonight|this (?:week|month|semester|year)|next (?:week|month|semester|year)|"
    r"recent(?:ly)?|now|deadline|due|when|what date|still)\b",
    re.I,
)


# ============================================================
# 鍵
# ============================================================
def canonical_text(text: str) -> str:
    """比對用的問題：全形半形統一、英文小寫、臺／台統一，去掉空白跟標點（數字的小數點留著）。"""
    text = unicodedata.normalize("NFKC", text).lower().replace("臺", "台")
    kept = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if unicodedata.category(ch).startswith("P"):
            if ch == "." and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
                kept.append(ch)
            continue
        kept.append(ch)
    return "".join(kept)


def conversation_turns(query_str: str, history_str: str) -> list[str]:
    """對話紀錄（supervisor 組的「[使用者]: … -> [系統反問]: …」，最後一句是這一次的問題）拆成一句一句，
    沒有對話紀錄（評估、直接呼叫）就只有這一次的問題。

    supervisor 傳進來的 query_str 是選工具的模型改寫過的，同一句話每次改寫得不太一樣，所以有對話紀錄時
    只看使用者自己說的話。"""
    history = (history_str or "").strip()
    if not history or history == "無":
        return [canonical_text(_GREETING_RE.sub("", query_str.strip()))]
    turns = []
    for turn in history.split(" -> "):
        match = re.match(r"\s*\[([^\]]+)\]:\s*(.*)", turn, re.S)
        role, text = (match.group(1), match.group(2)) if match else ("使用者", turn)
        if role == "使用者":
            text = _GREETING_RE.sub("", text.strip())
        turns.append(f"{role}:{canonical_text(text)}")
    return turns


def is_date_sensitive(text: str) -> bool:
    return bool(_DATE_SENSITIVE_RE.search(unicodedata.normalize("NFKC", text)))


def make_key(query_str: str, history_str: str, context: Iterable, now: Optional[datetime] = None) -> str:
    """context 是其他會影響回答的東西（prompt、模型、學年度學期、回答語言），由 academic_agent 給。"""
    now = now or datetime.now()
    turns = conversation_turns(query_str, history_str)
    user_words = f"{query_str} {history_str}"
    payload = {
        "version": CACHE_VERSION,
        "turns": turns,
        "context": list(context),
        "date": f"{now:%Y-%m-%d}" if is_date_sensitive(user_words) else "",
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:32]


def valid_key(key: str) -> bool:
    """鍵會從前端傳回來（「重新查詢」），只接受 32 個十六進位字，不能拿來讀別的檔案。"""
    return isinstance(key, str) and bool(_KEY_RE.fullmatch(key))


# ============================================================
# 文件指紋跟目錄版本
# ============================================================
def document_fingerprint(doc: rag_documents.CatalogDocument, by_id: dict) -> str:
    """挑文件跟回答的模型看得到的東西：檔名、全文、卡片、是不是舊版（指到哪份新版）。任何一樣改了指紋就不同。"""
    latest = by_id.get(doc.superseded_by)
    payload = [
        doc.file_name, hashlib.sha256(doc.text.encode("utf-8")).hexdigest(), doc.card,
        latest.file_name if latest else "", doc.cohort_edition,
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


@dataclass
class CatalogState:
    version: str
    fingerprints: dict  # doc_id → 指紋
    by_fingerprint: dict  # 指紋 → CatalogDocument


_state_lock = threading.Lock()
_state: dict = {"catalog": None, "state": None}
_version_cache: dict[str, frozenset] = {}


def catalog_state(catalog: list) -> CatalogState:
    """目錄的版本跟每份文件的指紋。同一個目錄只算一次（約兩千份文件，不到一秒），新的版本順便存一份指紋清單。"""
    with _state_lock:
        if _state["catalog"] is not catalog:
            by_id = {doc.doc_id: doc for doc in catalog}
            fingerprints = {doc.doc_id: document_fingerprint(doc, by_id) for doc in catalog}
            version = hashlib.sha256("\n".join(sorted(fingerprints.values())).encode("utf-8")).hexdigest()[:16]
            by_fingerprint = {fp: by_id[doc_id] for doc_id, fp in fingerprints.items()}
            _save_version(version, fingerprints.values())
            _state.update(catalog=catalog, state=CatalogState(version, fingerprints, by_fingerprint))
        return _state["state"]


def _versions_dir() -> Path:
    return CACHE_DIR / "catalogs"


def _save_version(version: str, fingerprints: Iterable[str]) -> None:
    _version_cache[version] = frozenset(fingerprints)
    path = _versions_dir() / f"{version}.json"
    if path.exists():
        return
    rag_documents.write_json_cache(path, {"created": _now(), "fingerprints": sorted(_version_cache[version])})
    for old in sorted(_versions_dir().glob("*.json"), key=_mtime)[:-KEEP_CATALOG_VERSIONS]:
        old.unlink(missing_ok=True)
        _version_cache.pop(old.stem, None)


def version_fingerprints(version: str) -> Optional[frozenset]:
    """以前某個目錄版本有哪些文件指紋。版本清單被清掉了（太舊）回傳 None。"""
    if version not in _version_cache:
        data = _read(_versions_dir() / f"{version}.json")
        if not data:
            return None
        _version_cache[version] = frozenset(data.get("fingerprints", []))
    return _version_cache[version]


# ============================================================
# 讀寫
# ============================================================
_save_counter = itertools.count(1)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _entry_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load(key: str) -> Optional[dict]:
    if not valid_key(key):
        return None
    entry = _read(_entry_path(key))
    if not entry or entry.get("format") != FORMAT or entry.get("key") != key:
        return None
    return entry


def save(entry: dict) -> None:
    """寫不進去不影響這次的回答，只印警告（write_json_cache）。每存一兩百次清一次太舊的。"""
    if not valid_key(entry.get("key", "")):
        return
    rag_documents.write_json_cache(_entry_path(entry["key"]), entry)
    if next(_save_counter) % 200 == 0:
        prune()


def prune(limit: int = MAX_ENTRIES) -> int:
    """超過 limit 題時，刪掉最久沒用到的（檔案修改時間，用到快取時會更新），留九成。回傳刪了幾題。"""
    files = list(CACHE_DIR.glob("*.json"))
    if len(files) <= limit:
        return 0
    files.sort(key=_mtime)
    removed = files[: len(files) - int(limit * 0.9)]
    for path in removed:
        path.unlink(missing_ok=True)
    return len(removed)


def is_expired(entry: dict, now: Optional[datetime] = None) -> bool:
    try:
        created = datetime.fromisoformat(entry["created"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now or datetime.now()) - created > timedelta(days=MAX_AGE_DAYS)


def top_documents_agree(before: list[str], after: list[str]) -> bool:
    """兩次挑的文件（指紋，最相關的在前面）算不算一樣：兩邊最相關的那份都在對方挑的文件裡。
    順序對調（辦法跟它的申請說明）、多挑或少挑一份補充文件都算一樣。"""
    return bool(before and after) and before[0] in after and after[0] in before


def _audience(logged_in: bool) -> str:
    return "login" if logged_in else "guest"


def remember_suggestions(key: str, logged_in: bool, kind: str, questions: list[str]) -> None:
    """回答下面的選項（「你可能還想問」或反問的回答選項，supervisor 在回答完後產生）跟著回答一起存，
    下次用到這個快取時直接用，不用再呼叫模型（一次約 US$0.003）。登入跟沒登入推薦的功能不一樣，分開存。
    回答重新整理（_store_answer 存新的回答）時會清掉，跟著新的回答重新產生。"""
    entry = load(key)
    if entry is None or not questions:
        return
    entry.setdefault("suggestions", {})[_audience(logged_in)] = {"kind": kind, "questions": list(questions)}
    save(entry)


def cached_suggestions(key: str, logged_in: bool) -> Optional[tuple[str, list[str]]]:
    """存好的選項 (種類, 選項)，沒有的話回傳 None。"""
    stored = ((load(key) or {}).get("suggestions") or {}).get(_audience(logged_in))
    if not stored or not stored.get("questions"):
        return None
    return stored.get("kind", "follow_ups"), list(stored["questions"])


def remember_route(key: str, route: str) -> None:
    """記一次「這段對話被選工具的模型導到法規問答」。route 是登入或訪客加上選工具設定的指紋
    （supervisor_agent.regulation_route），改了選工具的 prompt 或工具說明，之前記的次數就不算。
    跟回答本身無關，回答重新整理時次數照樣留著。"""
    entry = load(key)
    if entry is None:
        return
    routes = entry.setdefault("routes", {})
    routes[route] = routes.get(route, 0) + 1
    save(entry)


def all_entries() -> list[dict]:
    entries = []
    for path in CACHE_DIR.glob("*.json"):
        entry = _read(path)
        if entry and entry.get("format") == FORMAT:
            entries.append(entry)
    return entries


def delete(entries: list[dict]) -> None:
    for entry in entries:
        if valid_key(entry.get("key", "")):
            _entry_path(entry["key"]).unlink(missing_ok=True)


# ============================================================
# 統計
# ============================================================
def _question_of(entry: dict) -> str:
    """使用者這一次的問題（對話紀錄的最後一句），列統計用。"""
    history = entry.get("history") or ""
    if history.strip() and history.strip() != "無":
        last = history.split(" -> ")[-1]
        return re.sub(r"^\s*\[[^\]]+\]:\s*", "", last)
    return entry.get("query", "")


def summary_lines(entries: list[dict], top: int = 15) -> list[str]:
    if not entries:
        return ["目前沒有快取的回答。"]
    confirmed = [e for e in entries if e.get("confirmed")]
    hits = sum(e.get("hits", 0) for e in entries)
    saved = sum(e.get("hits", 0) * (e.get("cost") or 0) for e in entries)
    computed = sum(e.get("computed", 0) for e in entries)
    lines = [
        f"快取 {len(entries)} 題（確認過、會直接用的 {len(confirmed)} 題），"
        f"這些問題被問了 {computed + hits} 次，其中 {hits} 次直接用快取，約省下 US${saved:.2f}",
        "",
        "最常被問的問題（問了幾次／用快取幾次）",
    ]
    for entry in sorted(entries, key=lambda e: -(e.get("computed", 0) + e.get("hits", 0)))[:top]:
        mark = "" if entry.get("confirmed") else "（還沒確認）"
        asked = entry.get("computed", 0) + entry.get("hits", 0)
        question = " ".join(_question_of(entry).split())[:60]
        lines.append(f"  {asked:>4} 次／{entry.get('hits', 0):>4} 次  {question}{mark}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="法規問答的回答快取（storage/rag/answers/）")
    parser.add_argument("--delete", metavar="關鍵字", help="刪掉對話紀錄裡有這個字的快取")
    parser.add_argument("--clear", action="store_true", help="刪掉全部的快取")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    entries = all_entries()
    if args.clear:
        delete(entries)
        print(f"刪掉了 {len(entries)} 題快取。")
        return
    if args.delete:
        matched = [e for e in entries if args.delete in f"{e.get('query', '')} {e.get('history', '')}"]
        for entry in matched:
            print(f"刪掉：{_question_of(entry)}")
        delete(matched)
        print(f"共刪掉 {len(matched)} 題快取。")
        return
    print("\n".join(summary_lines(entries)))


if __name__ == "__main__":
    main()
