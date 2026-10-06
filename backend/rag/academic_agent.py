"""校園法規 RAG 的查詢流程（文件的解析與目錄卡片在 rag_documents.py）。

每個問題通常只有兩次 LLM 呼叫：
0. 初篩（不呼叫 LLM）：文件有上千份，目錄沒辦法整份交給模型，先用卡片向量跟關鍵字各排一次名、
   合併之後取前 ROUTER_CANDIDATES 份（見 retrieve_candidates），問題本身跟加上對話紀錄各算一次；
   提到系所時，那個系所跟學院資料夾裡的文件再另外排一次。
1. 挑文件：把使用者的問題、對話歷史、初篩出來的「目錄卡片」一起交給模型，
   請它把問題改寫成完整的一句話，判斷要回答、反問還是查無資料，並挑出最多
   MAX_DOCUMENTS 份相關文件。判斷成查無資料、但關鍵字比對找得到候選文件時，
   會帶著候選再確認一次（見 choose_documents）。
2. 回答：把挑中文件的「全文」交給模型，照規則回答並標注引用 [1]、[2]（只用來決定要列哪些參考資料，
   送給使用者之前會拿掉，見 AnswerCleaner）。

為什麼不再把文件切成 chunk 做向量 + BM25 檢索：舊版找錯檔案，大多是因為各學院、
各年度的表單內容長得很像，切成片段之後就分不出是哪個學院、哪一年的，片段也常把
表頭或適用對象切掉，模型拿到的是缺了前提的數字。改成看目錄挑文件、讀整份文件之後，
這兩個問題都不存在了，也少了 query 改寫、多組檢索、rerank 這幾輪呼叫。

文件量：2026-10 第一次用 crawler.py 抓了 7 個單位、321 份，整份目錄約 17 萬 token，還能直接交給
模型（挑文件一次 3～9 秒）。之後擴充到全校各系所、行政單位，文件多了好幾倍，整份目錄放不進去，
才加上第 0 步的初篩。初篩漏掉的文件模型就看不到，改初篩之後要看評估裡「挑對文件」的比例。

改這裡之前先跑 `python rag_eval/run_eval.py` 記下分數，改完再跑一次比較。
"""

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

from backend.rag import rag_documents
from backend.logging_config import make_print_logger

print = make_print_logger(__name__)

load_dotenv()

# 挑文件用 gpt-5.4、不推理：2026-10 文件擴充到全校之後，gpt-5.4-mini（medium）常挑到別的系的同類文件
# （問資工系的資格考，卻挑了通訊系的資格考公告），挑對的比例開發題 96～99%、保留題 96～98%；
# gpt-5.4 不推理兩邊都 100%，單次也比較快（約 2 秒對 4.5 秒），只是 token 單價比較高。
# 以前以為 mini 推理太少會亂判查無資料，其實是模型把編號連標題一起寫、解析不到（見 plan_query）。
ROUTER_MODEL = os.getenv("RAG_ROUTER_MODEL", "gpt-5.4")
ROUTER_REASONING = os.getenv("RAG_ROUTER_REASONING", "none")
ANSWER_MODEL = os.getenv("RAG_ANSWER_MODEL", "gpt-5.4")
ANSWER_REASONING = os.getenv("RAG_ANSWER_REASONING", "low")

MAX_DOCUMENTS = 4
# 初篩留給挑文件模型看的卡片數。文件有上千份，整份目錄交給模型太慢也太貴，先用卡片向量跟
# 關鍵字各排一次名、用 RRF 合併，取前面這麼多份（見 retrieve_candidates）。
ROUTER_CANDIDATES = int(os.getenv("RAG_ROUTER_CANDIDATES", "60"))
# 卡片的「可回答問題」每張約 12 題，佔了挑文件 prompt 六成的 token，每張只列前面這麼多題（0 是全部列出）。
# 2026-10 比過：全部、前 6／4／3 題，開發題跟保留題都全對（挑跟問題最相關的幾題也沒有比較好），
# 完全不列的話保留題錯 2 題。列 4 題時每次挑文件約 2.2 萬 token，全部列出約 3.8 萬。
ROUTER_ANSWERS_PER_CARD = int(os.getenv("RAG_ROUTER_ANSWERS", "4"))
RRF_K = 60
UNIT_BOOST = os.getenv("RAG_UNIT_BOOST", "1") != "0"
# 關鍵字二次確認的門檻：用評估題目看過，真的相關的文件分數大多在 10 以上，只因為
# 「申請」「學生」這類常見字而比對到的在 8 以下（例如問宿舍、停車證）。
MIN_KEYWORD_SCORE = 8.0
# 單一文件超過這個長度時，只留開頭跟跟問題最相關的段落（2026-10 爬蟲抓進來的文件裡，
# 中英對照的宿舍管理辦法約四萬四千字、學位論文撰寫體例參考約三萬字會用到）。
MAX_DOCUMENT_CHARS = 20000

ACADEMIC_MAPPING_FILE = Path(__file__).resolve().parent / "academic_hierarchy.json"

NOT_FOUND_ANSWER = (
    "目前收錄的校園法規文件裡，找不到可以回答這個問題的規定，所以沒辦法給你可靠的答案。"
    "建議直接洽詢承辦單位或所屬系辦確認。"
)

_client: Optional[OpenAI] = None
_catalog_cache: dict = {"signature": None, "catalog": []}
_catalog_lock = threading.Lock()


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def get_catalog() -> list[rag_documents.CatalogDocument]:
    """data/ 沒變就沿用記憶體裡的目錄；有新增、刪除、修改就重新載入（有快取，很快）。
    加鎖是因為新文件要呼叫 LLM 產生卡片，同時進來的查詢不該各自重做一次。
    """
    with _catalog_lock:
        signature = rag_documents.data_signature()
        if signature != _catalog_cache["signature"]:
            _catalog_cache["catalog"] = rag_documents.load_catalog()
            _catalog_cache["signature"] = signature
            print(f"[Academic Agent] 法規文件目錄已載入，共 {len(_catalog_cache['catalog'])} 份文件")
        return _catalog_cache["catalog"]


def department_directory() -> str:
    """系所 → 學院對照（可人工審核的 academic_hierarchy.json），給模型判斷使用者屬於哪個學院。"""
    try:
        data = json.loads(ACADEMIC_MAPPING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[Academic Agent] 讀不到系所對照表：{e}")
        return "（沒有對照表）"

    by_college: dict[str, list[str]] = {}
    for department, entry in data.get("departments", {}).items():
        aliases = [a for a in entry.get("aliases", []) if a != department]
        label = f"{department}（{'、'.join(aliases)}）" if aliases else department
        by_college.setdefault(entry.get("college", "其他"), []).append(label)
    return "\n".join(f"- {college}：{'、'.join(items)}" for college, items in by_college.items())


def format_catalog(catalog: list[rag_documents.CatalogDocument], answers_limit: int = 0) -> str:
    """answers_limit 大於 0 時，每張卡片的「可回答問題」只列前面這麼多題，並註明省略了幾題。"""
    lines = []
    for doc in catalog:
        card = doc.card
        meta = "｜".join(
            part for part in (
                card["doc_type"],
                f"範圍：{card['scope']}" if card["scope"] else "",
                f"適用：{card['applies_to']}" if card["applies_to"] else "",
                f"版本：{card['version']}" if card["version"] else "",
                f"檔名：{doc.file_name}",
            ) if part
        )
        old = f"（舊版，最新版是 [{doc.superseded_by}]）" if doc.superseded_by else ""
        lines.append(f"[{doc.doc_id}] {card['title']}{old}\n{meta}\n摘要：{card['summary']}")
        answers = card["answers"]
        if answers_limit and len(answers) > answers_limit:
            lines.append("可回答：" + "／".join(answers[:answers_limit]) + f"（另有 {len(answers) - answers_limit} 題）")
        elif answers:
            lines.append("可回答：" + "／".join(answers))
        lines.append("")
    return "\n".join(lines)


# ============================================================
# 第 1 步：挑文件
# ============================================================
ROUTER_INSTRUCTIONS = """你是中央大學校園法規問答系統的「文件挑選」步驟。系統收錄了全校各單位的文件，
使用者訊息裡的「文件目錄」是先用關鍵字跟語意比對、從全部文件篩出來可能相關的那些（大致依相關程度排序）。
你要根據使用者的問題挑出回答需要的文件（之後會把整份文件交給另一個模型回答）。

步驟：
1. 把使用者這一次的問題結合對話紀錄，改寫成一句完整、不需要上下文也看得懂的問題
   （例如先問「文學院的外文門檻」再問「那地科學院呢」，要改寫成「地科學院的外文畢業門檻是多少？」）。
   只補回上下文裡的資訊，不要加入使用者沒說的條件。
2. 從目錄挑出回答需要的文件，最相關的放前面，最多 {max_documents} 份，寧缺勿濫：
   - 注意「適用範圍」：規定因學院、系所、學制不同時，挑使用者所屬單位的那份（用系所對照表
     判斷系所屬於哪個學院）。所屬單位沒有專屬文件時，挑可能涵蓋它的全校性文件，不要拿
     別的學院、別的系的文件代替。
   - 注意「適用對象」：教師的規定不能拿來回答學生的問題，學生的也不能拿來回答教師的。
   - 同一種文件有多個年度或版本時，挑最新的（目錄裡標了「舊版」的不要挑，改挑它指向的最新版）；
     使用者指定了年度就挑那個年度；新舊版本依入學年度等條件都可能適用時，兩份都挑。
   - 同一個單位有好幾份文件都講到這件事（例如辦法跟它的申請表、說明），版本日期又不一樣時，
     都挑，回答時會以日期較新的為準。
3. 決定 decision：
   - answer：目錄裡有能回答、或能部分回答的文件。
   - not_found：目錄裡沒有任何跟問題相關的文件（例如問宿舍，但目錄裡沒有宿舍的規定）。
     不要硬挑主題不同的文件湊數。
   - clarify：目錄裡有好幾份適用不同學院、系所或身分的文件，答案差很多、沒辦法簡短分開列出，
     而使用者沒說、對話紀錄裡也看不出他屬於哪一種；或是根本看不出在問什麼。
     目錄裡只有一份相關文件時（即使它只適用某個系），用 answer，回答時會說明適用範圍；
     答案只差在身分別、學制等幾種情況時，也用 answer，回答時分情況列出。
     clarify_question 用一句話反問使用者需要補充什麼，不要反問不影響答案的細節。
   decision 不是 answer 時 documents 留空。
4. question 跟 clarify_question 用使用者的語言寫，中文一律用繁體中文。

系所與學院對照：
{departments}"""

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "decision": {"type": "string", "enum": ["answer", "not_found", "clarify"]},
        "documents": {"type": "array", "items": {"type": "string"}},
        "clarify_question": {"type": "string"},
    },
    "required": ["question", "decision", "documents", "clarify_question"],
    "additionalProperties": False,
}


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"[\W_]", "", text)  # 只留中文字、英文、數字
    return {text[i:i + 2] for i in range(len(text) - 1)}


@dataclass
class SearchIndex:
    """初篩用的索引，目錄載入後建一次：字元二元組 → 出現在哪幾份文件（反向索引），跟卡片向量疊成的矩陣。
    反向索引比每份文件各存一個二元組集合省記憶體（1,800 份文件約 24 MB 對 143 MB），比對也快幾十倍。"""
    postings: dict[str, np.ndarray]  # 值是 catalog 的索引
    size: int
    vectors: Optional[np.ndarray]  # 沒有任何向量時是 None；個別算不出來的文件是零向量


_index_cache: dict = {"catalog": None, "index": None}
_index_lock = threading.Lock()


def search_index(catalog: list[rag_documents.CatalogDocument]) -> SearchIndex:
    with _index_lock:
        if _index_cache["catalog"] is not catalog:
            # 卡片的「可回答問題」也要算進去：每張卡片都寫滿「要怎麼」「可以嗎」這類問句用字，
            # 這些字的 IDF 會因此變得很低，不會讓全篇都是問句的常見問答被過度加權。
            postings: dict[str, list[int]] = {}
            for i, doc in enumerate(catalog):
                for gram in _bigrams(rag_documents.normalize_text(
                    f"{doc.card['title']} {doc.card['summary']} {' '.join(doc.card['answers'])} {doc.file_name} {doc.text}"
                )):
                    postings.setdefault(gram, []).append(i)
            vectors = None
            dimensions = next((len(doc.embedding) for doc in catalog if doc.embedding is not None), 0)
            if dimensions:
                vectors = np.zeros((len(catalog), dimensions), dtype=np.float32)
                for i, doc in enumerate(catalog):
                    if doc.embedding is not None:
                        vectors[i] = doc.embedding
            arrays = {gram: np.array(ids, dtype=np.int32) for gram, ids in postings.items()}
            _index_cache.update(catalog=catalog, index=SearchIndex(arrays, len(catalog), vectors))
        return _index_cache["index"]


def keyword_scores(text: str, index: SearchIndex) -> np.ndarray:
    """用字元二元組比對問題跟每份文件的標題、卡片與全文，依詞的稀有程度（IDF）加權。"""
    scores = np.zeros(index.size)
    for gram in _bigrams(rag_documents.normalize_text(text)):
        hits = index.postings.get(gram)
        if hits is not None:
            scores[hits] += math.log((index.size + 1) / (len(hits) + 1))
    return scores


def keyword_candidates(
    question: str, catalog: list[rag_documents.CatalogDocument], limit: int = 6, min_score: float = MIN_KEYWORD_SCORE,
) -> list:
    """關鍵字分數夠高的文件。挑文件步驟判斷「查無資料」時，拿來帶著候選再確認一次。"""
    scores = keyword_scores(question, search_index(catalog))
    order = [i for i in np.argsort(-scores, kind="stable") if scores[i] >= min_score]
    return [catalog[i] for i in order[:limit]]


_unit_cache: dict = {"mtime": None, "aliases": []}


def unit_aliases() -> list[tuple[str, str, str]]:
    """系所對照表的（別名, 正式名稱, 學院），長的排前面（「化學工程與材料工程學系」要比「化學」先比對）。
    兩個字的別名（物理、中文、機械）太容易誤判（「中文版」「物理治療」），不拿來比對。"""
    try:
        mtime = ACADEMIC_MAPPING_FILE.stat().st_mtime
    except OSError:
        return []
    if _unit_cache["mtime"] != mtime:
        data = json.loads(ACADEMIC_MAPPING_FILE.read_text(encoding="utf-8"))
        pairs = []
        for name, entry in data.get("departments", {}).items():
            for alias in {name, *entry.get("aliases", [])}:
                if len(alias) >= 3:
                    pairs.append((alias, name, entry.get("college", "")))
        _unit_cache.update(mtime=mtime, aliases=sorted(pairs, key=lambda pair: -len(pair[0])))
    return _unit_cache["aliases"]


def unit_folders(text: str, catalog: list[rag_documents.CatalogDocument]) -> set[str]:
    """問題、對話紀錄提到的系所（跟它的學院）對應到 data/ 的哪些資料夾。"""
    units: dict[str, set[str]] = {}  # 正式名稱或學院 → 可以用來比對資料夾的名字
    for alias, name, college in unit_aliases():
        if alias in text:
            text = text.replace(alias, " ")
            units.setdefault(name, set()).add(alias)
            if college:
                units.setdefault(college, set()).add(college)
    if not units:
        return set()
    for alias, name, _ in unit_aliases():
        if name in units:
            units[name].add(alias)
    folders = {doc.folder for doc in catalog if doc.folder}
    return {folder for folder in folders for name, names in units.items() if folder in names or name in folder}


def retrieve_candidates(
    query_str: str, history_str: str, catalog: list[rag_documents.CatalogDocument], limit: int = ROUTER_CANDIDATES,
) -> list:
    """初篩：問題本身、問題加上對話紀錄，各用卡片向量跟關鍵字排一次名，用 RRF 合併取前 limit 份。

    對話紀錄要一起算：「那物理系呢？」「我是化學系的，那抵免呢？」只看這一句找不到該找的文件。
    向量抓得到換句話說（書卷獎 → 學業優良獎學金），關鍵字抓得到向量容易漏的專有名詞跟系所名稱。
    挑到舊版時會把最新版也放進來，挑文件的模型才能照規則改挑最新版。
    """
    if len(catalog) <= limit:
        return list(catalog)
    index = search_index(catalog)
    texts = [query_str]
    if history_str and history_str.strip() != f"[使用者]: {query_str}".strip():
        texts.append(history_str[-800:])

    rankings: list[np.ndarray] = []
    similarity = None
    if index.vectors is not None:
        try:
            similarity = index.vectors @ rag_documents.embed_texts(texts).T
            rankings.extend(np.argsort(-similarity[:, j], kind="stable") for j in range(similarity.shape[1]))
        except Exception as e:  # 向量服務連不上就只用關鍵字，不要整個查詢失敗
            print(f"[Academic Agent] 問題的向量算不出來，初篩只用關鍵字：{e}")
    keyword = [keyword_scores(text, index) for text in texts]
    for scores in keyword:
        order = np.argsort(-scores, kind="stable")
        rankings.append(order[scores[order] > 0])
    # 問題提到系所時，那個系所（跟學院）資料夾裡的文件另外排一次名：各系都有「碩士班修業辦法」，
    # 只靠相似度常常被別系的同名文件擠出候選
    folders = unit_folders(" ".join(texts), catalog) if UNIT_BOOST else set()
    members = np.array([i for i, doc in enumerate(catalog) if doc.folder in folders], dtype=int)
    if len(members):
        relevance = similarity[:, 0] if similarity is not None else keyword[0]
        rankings.append(members[np.argsort(-relevance[members], kind="stable")])

    fused = np.zeros(len(catalog))
    for order in rankings:
        top = order[: limit * 2]
        fused[top] += 1.0 / (RRF_K + np.arange(len(top)))
    chosen = [catalog[i] for i in np.argsort(-fused, kind="stable")[:limit] if fused[i] > 0]
    by_id = {doc.doc_id: doc for doc in catalog}
    for doc in list(chosen):
        latest = by_id.get(doc.superseded_by)
        if latest is not None and latest not in chosen:
            chosen.append(latest)
    return chosen


def today_context(now: Optional[datetime] = None) -> str:
    """今天的日期跟學年度學期。校曆這類文件收進來之後，「這學期加退選」「今年畢業典禮」
    要知道今天是哪一學期才答得出來（不然只能反問）。第 1 學期是 8 月到隔年 1 月。
    """
    now = now or datetime.now()
    roc_year = now.year - 1911
    if now.month >= 8:
        academic_year, term = roc_year, 1
    elif now.month == 1:
        academic_year, term = roc_year - 1, 1
    else:
        academic_year, term = roc_year - 1, 2
    return f"今天是 {now.year} 年 {now.month} 月 {now.day} 日（{academic_year} 學年度第 {term} 學期）"


def plan_query(
    query_str: str, history_str: str, catalog: list[rag_documents.CatalogDocument],
    candidates: list[rag_documents.CatalogDocument], hints: list = (),
) -> dict:
    """candidates 是初篩後要給模型看的卡片。挑回來的編號用整份 catalog 對，模型照「舊版」
    標記改挑的最新版就算不在 candidates 裡也找得到。

    卡片照初篩的相關程度排序：2026-10 試過改成依來源單位排列（像以前整份目錄那樣），
    開發題挑對文件的比例從九成一掉到八成一。
    """
    instructions = ROUTER_INSTRUCTIONS.format(max_documents=MAX_DOCUMENTS, departments=department_directory())
    # 每次都一樣的規則放 system（OpenAI 會快取），每題不同的目錄、日期、問題放 user，問題放最後
    request = (
        f"文件目錄：\n{format_catalog(candidates, ROUTER_ANSWERS_PER_CARD)}\n"
        f"{today_context()}\n"
        f"對話紀錄（使用者說過的話，以及系統反問或請使用者補充的話，最後一句是這一次的問題）：{history_str or '無'}\n"
        f"這一次的問題：{query_str}"
    )
    if hints:
        request += (
            "\n\n上一次判斷是目錄裡沒有相關文件，但關鍵字比對找到下面這些文件，請再確認一次"
            "（它們不一定相關，真的無關就維持 not_found）：\n"
            + "\n".join(f"[{doc.doc_id}] {doc.card['title']}" for doc in hints)
        )
    response = _openai().chat.completions.create(
        model=ROUTER_MODEL,
        reasoning_effort=ROUTER_REASONING,
        messages=[{"role": "system", "content": instructions}, {"role": "user", "content": request}],
        response_format={"type": "json_schema", "json_schema": {"name": "document_plan", "schema": ROUTER_SCHEMA, "strict": True}},
    )
    plan = json.loads(response.choices[0].message.content)

    by_id = {doc.doc_id: doc for doc in catalog}
    selected = []
    for doc_id in plan["documents"]:
        # 模型有時候會把標題一起寫進來（「[D506] 國立中央大學學生請假規則」），只取編號
        match = re.search(r"D\d+", doc_id)
        doc = by_id.get(match.group(0)) if match else None
        if doc is not None and doc not in selected:
            selected.append(doc)
    plan["documents"] = with_page_images(selected[:MAX_DOCUMENTS], catalog)
    if plan["decision"] == "answer" and not plan["documents"]:
        plan["decision"] = "not_found"
    return plan


# crawler.py 把網頁裡的海報圖片另存成 PDF，並在網頁快照最後註明（見 Crawler._write_snapshot）
_PAGE_IMAGES_NOTE = re.compile(r"（這個頁面的圖片內容另外存在「(.+?)」。）")


def with_page_images(documents: list, catalog: list[rag_documents.CatalogDocument]) -> list:
    """挑到網頁快照時，把它另存的圖片 PDF 一起帶上：兩份本來就是同一個網頁，語言中心的英文畢業門檻
    頁面只寫了送件方式，各學院的分數都在海報上，只讀網頁答不出來。圖片 PDF 不佔 MAX_DOCUMENTS 的名額。"""
    by_name = {doc.file_name: doc for doc in catalog}
    result = list(documents)
    for doc in documents:
        match = _PAGE_IMAGES_NOTE.search(doc.text) if doc.file_name.endswith(".md") else None
        companion = by_name.get(f"{doc.folder}/{match.group(1)}") if match and doc.folder else None
        if companion is not None and companion not in result:
            result.append(companion)
    return result


def choose_documents(query_str: str, history_str: str, catalog: list[rag_documents.CatalogDocument]) -> dict:
    """挑文件；判斷成查無資料時，帶著關鍵字比對的候選文件再確認一次。
    「明明有卻說沒有」是最傷使用者的錯誤，而且模型偶爾會發生（同一題跑幾次才錯一次），
    真的沒有相關文件的問題多花一次呼叫的時間是值得的。
    """
    candidates = retrieve_candidates(query_str, history_str, catalog)
    plan = plan_query(query_str, history_str, catalog, candidates)
    if plan["decision"] != "not_found":
        return plan
    hints = keyword_candidates(f"{plan['question']} {query_str}", catalog)
    if not hints:
        return plan
    print(f"[Academic Agent] 判斷為查無資料，帶關鍵字候選再確認：{[doc.doc_id for doc in hints]}")
    return plan_query(query_str, history_str, catalog, candidates + [d for d in hints if d not in candidates], hints=hints)


# ============================================================
# 第 2 步：讀全文回答
# ============================================================
ANSWER_INSTRUCTIONS = """你是中央大學 NCUXplore 的校園法規助理，只根據提供的文件回答使用者的問題。

規則：
1. 只能用文件裡寫的內容回答，不要用常識或猜測補充。文件沒寫的就說文件沒寫，不要編造數字、日期或流程。
2. 第一句就直接回答問題（數字、日期、可不可以、要去哪裡），再補充必要的條件、流程或注意事項。
   補充的內容以跟問題直接相關為限，不要把文件裡其他學院、身分、學制的資料全部列出來
   （使用者沒說是哪一種、需要分情況回答時例外）。
3. 注意文件的適用範圍與版本：
   - 說明答案適用於哪個學院、系所、學制或年度版本。
   - 文件裡沒有使用者所屬單位的規定時，要明說查不到，不能拿其他單位的規定代替；
     若要順帶提其他單位的規定，要講清楚那不是使用者的規定。
   - 同一種文件有多個年度版本時，以最新版本為主；依入學年度等條件而不同時，分別說明。
   - 好幾份文件都寫到同一件事、但寫法或數字不一樣時（例如辦法跟申請說明、新舊公告），
     以日期較新、或專門講這件事的那份為準，不要把每份的說法都列出來。
4. 規定依條件（學院、身分、學制等）而不同，使用者又沒說是哪一種時，簡短分情況列出，或請使用者補充。
5. 引用來源：只用到一份文件時不要標編號（畫面下方會列出來源檔案）。用到多份文件時，在段落或
   條列項目的結尾標注編號，例如 [1]、[2]，對應下面文件前面的 [編號]，不要編造編號，同一段標一次就好，
   表格的儲存格裡不要標。
6. 文件裡的表格用 | 分隔欄位；第一欄在好幾列重複出現，代表原本是合併儲存格。
7. 使用者用什麼語言問就用什麼語言回答（中文一律用繁體中文）。條列為主、簡潔完整，
   不要重複問題，不要描述你怎麼找資料，結尾不要提議「如果需要我可以再幫你……」。
   需要並列比較好幾項（例如各項考試的門檻、各身分的費用）時用 Markdown 表格，
   表格前後各空一行，不要放在條列項目裡面。
8. 文件完全無法回答時，直接說目前收錄的文件裡找不到，並建議洽詢的單位（文件裡有寫承辦單位就用文件寫的）。

系所與學院對照（判斷使用者屬於哪個學院時使用）：
{departments}"""


def fit_document(text: str, question: str, limit: int = MAX_DOCUMENT_CHARS) -> str:
    """文件太長時保留開頭（標題、適用範圍通常在這裡），其餘依跟問題的字詞重疊挑段落，
    再照原本順序排回去，並標出哪裡有省略。
    """
    if len(text) <= limit:
        return text
    sections = [s for s in re.split(r"\n\s*\n", text) if s.strip()]
    head, rest = sections[0], sections[1:]
    wanted = _bigrams(question)
    ranked = sorted(range(len(rest)), key=lambda i: len(wanted & _bigrams(rest[i])), reverse=True)

    budget = limit - len(head)
    keep = set()
    for i in ranked:
        if len(rest[i]) <= budget:
            keep.add(i)
            budget -= len(rest[i])

    parts, skipped = [head], False
    for i, section in enumerate(rest):
        if i in keep:
            parts.append(section)
            skipped = False
        elif not skipped:
            parts.append("（……中間省略跟問題無關的段落……）")
            skipped = True
    return "\n\n".join(parts)


def build_answer_request(plan: dict, query_str: str, history_str: str) -> str:
    blocks = []
    for i, doc in enumerate(plan["documents"], 1):
        card = doc.card
        meta = "｜".join(part for part in (
            card["doc_type"],
            f"範圍：{card['scope']}" if card["scope"] else "",
            f"版本：{card['version']}" if card["version"] else "",
            f"檔名：{doc.file_name}",
            "舊版（系統裡有更新的版本）" if doc.superseded_by else "",
        ) if part)
        blocks.append(f"[{i}] {card['title']}（{meta}）\n{fit_document(doc.text, plan['question'])}")

    return (
        f"{today_context()}\n"
        f"對話紀錄（使用者說過的話，以及系統反問或請使用者補充的話）：{history_str or '無'}\n"
        f"使用者這一次的問題：{query_str}\n"
        f"整理後的完整問題：{plan['question']}\n\n"
        "文件：\n\n" + "\n\n==========\n\n".join(blocks)
    )


# 引用編號（[1]、[1、2]）只拿來決定下方要列哪些參考資料（cited_sources），不顯示給使用者：
# 下方已經列出參考資料，句子後面一串 [1][2] 看起來很雜（使用者反映）。
_CITATION_RE = re.compile(r"[ \t]*\[\d+(?:\s*[,、，]\s*\d+)*\]")
# 片段結尾看起來像還沒寫完的引用編號（「[」「[1」「[1、」），等下一段再決定
_PARTIAL_CITATION_RE = re.compile(r"[ \t]*\[(?:\d+(?:\s*[,、，]\s*\d*)*)?$")


class AnswerCleaner:
    """整理串流出去的答案：拿掉引用編號，分號換成逗號（在行尾就換成句號）。

    使用者不喜歡分號，prompt 已經說了不要用，這裡是保險。引用編號跟分號都可能剛好落在
    兩個串流片段的交界，所以片段結尾看不出結果的部分先留著，等下一段再送出。
    """

    def __init__(self):
        self.pending = ""

    def feed(self, text: str) -> str:
        text = _CITATION_RE.sub("", self.pending + text)
        hold = _PARTIAL_CITATION_RE.search(text)
        cut = hold.start() if hold else len(text)
        # 結尾的分號要看下一段是不是換行，才知道要換成逗號還是句號
        trailing_semicolon = re.search(r"；\s*$", text[:cut])
        if trailing_semicolon:
            cut = trailing_semicolon.start()
        self.pending = text[cut:]
        return self._semicolons(text[:cut], ends_line=False)

    def flush(self) -> str:
        rest, self.pending = self.pending, ""
        return replace_semicolons(rest)

    def _semicolons(self, text: str, ends_line: bool) -> str:
        return replace_semicolons(text, ends_line)


def replace_semicolons(text: str, ends_line: bool = True) -> str:
    """分號換成逗號，在行尾（ends_line 時連整段結尾）的換成句號。使用者不喜歡回答裡有分號。"""
    text = re.sub(r"；(?=\s*\n)", "。", text)
    if ends_line:
        text = re.sub(r"；(\s*)$", r"。\1", text)
    return text.replace("；", "，")


def cited_sources(answer: str, documents: list[rag_documents.CatalogDocument]) -> list[str]:
    """只列出答案裡真的有引用的文件；完全沒標引用就列出全部挑中的文件。"""
    cited = {int(n) for group in re.findall(r"\[(\d+(?:\s*[,、]\s*\d+)*)\]", answer) for n in re.split(r"[,、]", group)}
    chosen = [doc for i, doc in enumerate(documents, 1) if i in cited] or documents
    return [doc.file_name for doc in chosen]


def query_academic_knowledge_stream(query_str: str, history_str: str = "") -> Iterator[dict]:
    """逐步 yield 事件（不含 SSE 包裝，呼叫端自己序列化）：
    - {"type": "status", "text": "..."}
    - {"type": "plan", "question": ..., "decision": ..., "documents": [檔名...]}  # 除錯用，前端不需要
    - {"type": "token", "text": "..."}          # 逐字答案片段
    - {"type": "sources", "sources": [...]}     # data/ 底下的檔名，前端會做成連結
    - {"type": "error", "message": "..."}
    """
    try:
        yield {"type": "status", "text": "正在載入法規資料庫..."}
        catalog = get_catalog()
        if not catalog:
            yield {"type": "error", "message": "法規資料庫是空的（data/ 底下沒有可讀取的文件）。"}
            return

        yield {"type": "status", "text": "正在挑選相關文件..."}
        plan = choose_documents(query_str, history_str, catalog)
        file_names = [doc.file_name for doc in plan["documents"]]
        print(f"[Academic Agent] 問題：「{query_str}」→「{plan['question']}」｜{plan['decision']}｜{file_names}")
        yield {"type": "plan", "question": plan["question"], "decision": plan["decision"], "documents": file_names}

        if plan["decision"] == "clarify":
            clarify = plan["clarify_question"] or "可以再說明一下你想查的是哪個學院、系所或學制的規定嗎？"
            yield {"type": "token", "text": replace_semicolons(clarify)}
            yield {"type": "sources", "sources": []}
            return
        if plan["decision"] == "not_found":
            yield {"type": "token", "text": NOT_FOUND_ANSWER}
            yield {"type": "sources", "sources": []}
            return

        yield {"type": "status", "text": f"正在閱讀 {len(plan['documents'])} 份文件並整理答案..."}
        stream = _openai().chat.completions.create(
            model=ANSWER_MODEL,
            reasoning_effort=ANSWER_REASONING,
            messages=[
                {"role": "system", "content": ANSWER_INSTRUCTIONS.format(departments=department_directory())},
                {"role": "user", "content": build_answer_request(plan, query_str, history_str)},
            ],
            stream=True,
        )
        answer_parts: list[str] = []
        cleaner = AnswerCleaner()
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                answer_parts.append(delta)
                visible = cleaner.feed(delta)
                if visible:
                    yield {"type": "token", "text": visible}
        rest = cleaner.flush()
        if rest:
            yield {"type": "token", "text": rest}

        # 原始答案裡的引用編號決定要列哪些參考資料，顯示給使用者的版本已經拿掉編號
        yield {"type": "sources", "sources": cited_sources("".join(answer_parts), plan["documents"])}

    except Exception as e:
        print(f"[Academic Agent] 查詢失敗：{type(e).__name__}: {e}")
        yield {"type": "error", "message": f"系統在查詢校園法規時發生錯誤：{e}"}


def query_academic_knowledge(query_str: str, history_str: str = "") -> dict:
    """非串流版本：把串流事件收集成一次回傳。"""
    answer_parts: list[str] = []
    result = {"answer": "", "sources": []}
    for event in query_academic_knowledge_stream(query_str, history_str):
        if event["type"] == "token":
            answer_parts.append(event["text"])
        elif event["type"] == "sources":
            result["sources"] = event["sources"]
        elif event["type"] == "plan":
            result["plan"] = {k: v for k, v in event.items() if k != "type"}
        elif event["type"] == "error":
            answer_parts.append(event["message"])
    result["answer"] = "".join(answer_parts).strip()
    return result


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    question = " ".join(sys.argv[1:]) or "資工系英文畢業門檻"
    result = query_academic_knowledge(question)
    print(f"\n{result['answer']}\n\n來源：{result['sources']}\n{result.get('plan')}")
