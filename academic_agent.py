"""校園法規 RAG 的查詢流程（文件的解析與目錄卡片在 rag_documents.py）。

每個問題通常只有兩次 LLM 呼叫：
1. 挑文件：把使用者的問題、對話歷史、全部文件的「目錄卡片」一起交給模型，
   請它把問題改寫成完整的一句話，判斷要回答、反問還是查無資料，並挑出最多
   MAX_DOCUMENTS 份相關文件。判斷成查無資料、但關鍵字比對找得到候選文件時，
   會帶著候選再確認一次（見 choose_documents）。
2. 回答：把挑中文件的「全文」交給模型，照規則回答並標注引用 [1]、[2]。

為什麼不再把文件切成 chunk 做向量 + BM25 檢索：舊版找錯檔案，大多是因為各學院、
各年度的表單內容長得很像，切成片段之後就分不出是哪個學院、哪一年的，片段也常把
表頭或適用對象切掉，模型拿到的是缺了前提的數字。改成看目錄挑文件、讀整份文件之後，
這兩個問題都不存在了，也少了 query 改寫、多組檢索、rerank 這幾輪呼叫。

文件量（2026-10，crawler.py 從各單位網站抓完之後）：321 份、約 87 萬字，目錄約 17 萬
token。目錄放在固定的 system 開頭，OpenAI 會快取，挑文件一次約 3～9 秒（75 份時約 3 秒）。
文件再多下去、或想讓挑文件更快，在第 1 步前面加一層關鍵字或向量初篩、只把候選文件的
卡片交給模型就好，第 2 步不用改。

改這裡之前先跑 `python rag_eval/run_eval.py` 記下分數，改完再跑一次比較。
"""

import json
import math
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from dotenv import load_dotenv
from openai import OpenAI

import rag_documents
from logging_config import make_print_logger

print = make_print_logger(__name__)

load_dotenv()

ROUTER_MODEL = os.getenv("RAG_ROUTER_MODEL", "gpt-5.4-mini")
# low 偶爾會把「文件裡有、但卡片寫得不夠明顯」的問題判成查無資料（每次錯的題目還不一樣），
# medium 在評估題目上穩定很多，只多約 0.6 秒。
ROUTER_REASONING = os.getenv("RAG_ROUTER_REASONING", "medium")
ANSWER_MODEL = os.getenv("RAG_ANSWER_MODEL", "gpt-5.4")
ANSWER_REASONING = os.getenv("RAG_ANSWER_REASONING", "low")

MAX_DOCUMENTS = 4
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


def format_catalog(catalog: list[rag_documents.CatalogDocument]) -> str:
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
        if card["answers"]:
            lines.append("可回答：" + "／".join(card["answers"]))
        lines.append("")
    return "\n".join(lines)


# ============================================================
# 第 1 步：挑文件
# ============================================================
ROUTER_INSTRUCTIONS = """你是中央大學校園法規問答系統的「文件挑選」步驟。系統收錄的文件都列在下面的目錄裡，
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
{departments}

文件目錄：
{catalog}"""

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


def keyword_candidates(
    question: str, catalog: list[rag_documents.CatalogDocument], limit: int = 6, min_score: float = MIN_KEYWORD_SCORE,
) -> list:
    """用字元二元組比對問題跟每份文件的標題、卡片與全文，依詞的稀有程度（IDF）加權，
    找出可能相關的文件。只拿來在挑文件步驟判斷「查無資料」時二次確認，不參與一般排序。

    卡片的「可回答問題」也要算進去：每張卡片都寫滿「要怎麼」「可以嗎」這類問句用字，
    這些字的 IDF 會因此變得很低，不會讓全篇都是問句的常見問答被過度加權。
    """
    grams = {doc.doc_id: _bigrams(rag_documents.normalize_text(
        f"{doc.card['title']} {doc.card['summary']} {' '.join(doc.card['answers'])} {doc.text}"
    )) for doc in catalog}
    wanted = _bigrams(rag_documents.normalize_text(question))
    doc_freq = {g: sum(1 for doc_grams in grams.values() if g in doc_grams) for g in wanted}
    idf = {g: math.log((len(catalog) + 1) / (df + 1)) for g, df in doc_freq.items() if df}

    scored = [(sum(idf.get(g, 0) for g in wanted & grams[doc.doc_id]), doc) for doc in catalog]
    scored = [(score, doc) for score, doc in scored if score >= min_score]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in scored[:limit]]


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


def plan_query(query_str: str, history_str: str, catalog: list[rag_documents.CatalogDocument], hints: list = ()) -> dict:
    instructions = ROUTER_INSTRUCTIONS.format(
        max_documents=MAX_DOCUMENTS, departments=department_directory(), catalog=format_catalog(catalog),
    )
    # 日期放在 user 訊息，不放 system：system 開頭的目錄每天都一樣，OpenAI 才能一直快取
    request = (
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
        # 目錄放在固定不變的 system 開頭，OpenAI 會自動快取這段前綴，之後的問題就不用重算。
        messages=[{"role": "system", "content": instructions}, {"role": "user", "content": request}],
        response_format={"type": "json_schema", "json_schema": {"name": "document_plan", "schema": ROUTER_SCHEMA, "strict": True}},
    )
    plan = json.loads(response.choices[0].message.content)

    by_id = {doc.doc_id: doc for doc in catalog}
    selected = []
    for doc_id in plan["documents"]:
        doc = by_id.get(doc_id.strip().strip("[]"))
        if doc is not None and doc not in selected:
            selected.append(doc)
    plan["documents"] = selected[:MAX_DOCUMENTS]
    if plan["decision"] == "answer" and not plan["documents"]:
        plan["decision"] = "not_found"
    return plan


def choose_documents(query_str: str, history_str: str, catalog: list[rag_documents.CatalogDocument]) -> dict:
    """挑文件；判斷成查無資料時，帶著關鍵字比對的候選文件再確認一次。
    「明明有卻說沒有」是最傷使用者的錯誤，而且模型偶爾會發生（同一題跑幾次才錯一次），
    真的沒有相關文件的問題多花一次呼叫的時間是值得的。
    """
    plan = plan_query(query_str, history_str, catalog)
    if plan["decision"] != "not_found":
        return plan
    hints = keyword_candidates(f"{plan['question']} {query_str}", catalog)
    if not hints:
        return plan
    print(f"[Academic Agent] 判斷為查無資料，帶關鍵字候選再確認：{[doc.doc_id for doc in hints]}")
    return plan_query(query_str, history_str, catalog, hints=hints)


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
            yield {"type": "token", "text": plan["clarify_question"] or "可以再說明一下你想查的是哪個學院、系所或學制的規定嗎？"}
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
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                answer_parts.append(delta)
                yield {"type": "token", "text": delta}

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
