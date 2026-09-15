"""校園法規 RAG。

這個檔案負責兩件事：
1. Ingestion（load_documents / get_or_create_index）：把 data/ 底下的 PDF、
   DOCX 轉成一份份「結構完整」的 chunk（一個表格 = 一個 chunk、一個條文/
   Q&A = 一個 chunk），再建立向量索引 + BM25 索引。
2. Retrieval（query_academic_knowledge）：deterministic 的系所/學院 mapping
   + FAQ/正式法規意圖判斷 + dense/BM25 hybrid 檢索 + scope 過濾 + LLM
   rerank，最後餵給 LLM 生成有引用來源的答案。

跟舊版最大的差異在 ingestion：
- PDF 優先用 pdfplumber 原生抽取文字/表格（零成本、零幻覺、表格欄位結構
  100% 準確）；只有整份 PDF 判定為掃描圖檔（幾乎沒有文字層）才退回
  Vision OCR。曾經改成「一律用 Vision OCR」，但實測發現 GPT-4o Vision
  在密集版面上會整段幻覺、唸錯字，所以能用原生解析就不要交給 LLM「用看的」
  去猜（詳見 extract_pdf_pages_structured 的說明）。
- DOCX 改用 python-docx 直接走 paragraphs/tables，原生保留表格結構。
- 舊版二進位 .doc 透過 LibreOffice 無頭轉檔成 .docx 再解析（見
  convert_doc_to_docx），不需要手動一個個另存新檔。
- 不管哪種來源，最後都依 Markdown 自身的結構（標題／條文／表格）切
  chunk，表格永遠不被從中間切開；跨頁表格會偵測並合併回同一個 chunk；
  每個 chunk 前面加一段身份前綴（檔名／類型／所屬段落）再拿去
  embedding，避免不同文件的內容在檢索/生成時被混在一起。

Retrieval 這一側（系所/學院 mapping、FAQ 優先、scope 過濾、hybrid 融合、
LLM rerank、附引用來源的回答 prompt）延續既有版本已經驗證過的設計，
只把手刻的線性掃描 lexical scorer 換成真正的 BM25。
"""

import os
import io
import re
import json
import base64
import hashlib
import pickle
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pdf2image import convert_from_path
from openai import OpenAI
import pdfplumber
import docx as python_docx
from rank_bm25 import BM25Okapi

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    load_index_from_storage,
    Settings,
    QueryBundle,
)
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from llama_index.core.postprocessor import LLMRerank
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.embeddings.openai import OpenAIEmbedding

from logging_config import make_print_logger

print = make_print_logger(__name__)

load_dotenv()

Settings.llm = LlamaOpenAI(
    model=os.getenv("RAG_BASE_MODEL", "gpt-4o-mini"),
    temperature=0,
    max_tokens=1000,
)
llm_smart = LlamaOpenAI(
    model=os.getenv("RAG_RERANK_MODEL", "gpt-4o-mini"),
    temperature=0,
    max_tokens=1200,
)
Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small", dimensions=1536)
openai_client = OpenAI()

CACHE_MD_DIR = "./parsed_markdown_cache"
PERSIST_DIR = "./storage"
DATA_DIR = "data"
POPPLER_PATH = os.getenv("POPPLER_PATH", None)
BM25_INDEX_PATH = os.path.join(PERSIST_DIR, "bm25.pkl")

VECTOR_TOP_K = int(os.getenv("VECTOR_TOP_K", "15"))
LEXICAL_TOP_K = int(os.getenv("LEXICAL_TOP_K", "15"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))

# rerank_pool 最多 max(VECTOR_TOP_K, LEXICAL_TOP_K) 筆，batch_size=3 的話
# 等於要切成好幾批、一批一次 LLM 呼叫，全部跑完才能算完 rerank 分數——
# 實測一次查詢光 rerank 這階段就疊了 5 次序列呼叫，是整體延遲的大宗。
# 改成跟 top_k 同量級，正常情況下一次 LLM 呼叫就能把整個候選池排完。
RERANK_BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "15"))
RERANK_MAX_CHARS = int(os.getenv("RERANK_MAX_CHARS", "1800"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "12000"))
MAX_SECTION_CHUNK_CHARS = int(os.getenv("MAX_SECTION_CHUNK_CHARS", "1000"))
# 保底用的硬上限：OpenAI embedding API 單筆輸入上限是 8192 tokens，中文
# 抓保守一點的字數換算，避免真的撞到那條線（已用真實文件實測撞過一次）。
MAX_HARD_CHUNK_CHARS = int(os.getenv("MAX_HARD_CHUNK_CHARS", "4000"))

SCOPE_UNKNOWN_PENALTY = float(os.getenv("SCOPE_UNKNOWN_PENALTY", "0.15"))

FAQ_BOOST = float(os.getenv("FAQ_BOOST", "0.30"))
FAQ_ONLY_IF_MATCH = os.getenv("FAQ_ONLY_IF_MATCH", "1") == "1"
FAQ_FILENAME_PATTERNS = ("常見問題", "faq", "問答", "q&a", "qa", "常見問答")

FAQ_INTENT_PATTERNS = (
    "常見問題", "常見問答", "faq", "可不可以", "能不能", "可以不用",
    "是否可以", "可以嗎", "能嗎", "行不行", "怎麼辦", "如果", "那麼我可以",
    "抵免", "抵掉", "免修", "跨系", "修別系", "別系的", "本系的",
)
ACADEMIC_INTENT_PATTERNS = (
    "畢業門檻", "畢業資格", "畢業學分", "必修", "選修", "學分規定",
    "英文能力", "外文門檻", "修業規定", "申請資格", "申請期限", "截止日",
    "學位", "畢業規定", "課程規定",
)

TABLE_CONTINUES_MARKER = "<!--TABLE_CONTINUES-->"


# ============================================================
# 系所 / 學院 mapping（可審核的 JSON，不讓 LLM 自己猜 hierarchy）
# ============================================================
_BASE_DIR = Path(__file__).resolve().parent
_ACADEMIC_MAPPING_ENV = os.getenv("ACADEMIC_MAPPING_FILE", "academic_hierarchy.json")
ACADEMIC_MAPPING_FILE = str(
    Path(_ACADEMIC_MAPPING_ENV)
    if Path(_ACADEMIC_MAPPING_ENV).is_absolute()
    else (_BASE_DIR / _ACADEMIC_MAPPING_ENV)
)
ACADEMIC_MAPPING_SCHEMA_VERSION = "1"
DEFAULT_ACADEMIC_HIERARCHY = {"schema_version": ACADEMIC_MAPPING_SCHEMA_VERSION, "departments": {}}


def load_academic_hierarchy() -> dict:
    """載入可審核的系所 -> 學院 mapping；失敗時安全退回空 mapping。"""
    if not os.path.exists(ACADEMIC_MAPPING_FILE):
        print(f"academic mapping 不存在：{ACADEMIC_MAPPING_FILE}")
        return dict(DEFAULT_ACADEMIC_HIERARCHY)

    try:
        with open(ACADEMIC_MAPPING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or data.get("schema_version") != ACADEMIC_MAPPING_SCHEMA_VERSION:
            raise ValueError("mapping 格式或 schema version 不符")

        departments = data.get("departments", {})
        if not isinstance(departments, dict):
            raise ValueError("departments 必須是 object")

        clean = {"schema_version": ACADEMIC_MAPPING_SCHEMA_VERSION, "departments": {}}
        for canonical, entry in departments.items():
            if not isinstance(canonical, str) or not canonical.strip() or not isinstance(entry, dict):
                continue
            college = entry.get("college", "")
            aliases = entry.get("aliases", [])
            clean["departments"][canonical] = {
                "college": college.strip() if isinstance(college, str) else "",
                "aliases": [str(x).strip() for x in aliases if str(x).strip()] if isinstance(aliases, list) else [],
            }

        print(f"academic mapping loaded: {len(clean['departments'])} departments")
        return clean
    except Exception as e:
        print(f"academic mapping 載入失敗，停用 hierarchy expansion：{e}")
        return dict(DEFAULT_ACADEMIC_HIERARCHY)


def normalize_for_search(text: str) -> str:
    text = (text or "").lower()
    return re.sub(r"\s+", "", text)


def resolve_academic_entities(query: str, hierarchy: dict) -> list[dict]:
    """只做人工 mapping 的 exact/alias match，不讓 LLM 猜 hierarchy。"""
    query_norm = normalize_for_search(query)
    matches = []
    for canonical, entry in hierarchy.get("departments", {}).items():
        names = [canonical] + entry.get("aliases", [])
        for name in sorted(names, key=len, reverse=True):
            name_norm = normalize_for_search(name)
            if name_norm and name_norm in query_norm:
                matches.append({"department": canonical, "matched_alias": name, "college": entry.get("college", "")})
                break
    unique = {}
    for item in matches:
        unique[item["department"]] = item
    return list(unique.values())


def detect_query_intent(query: str) -> dict:
    """FAQ 類問題即使提到多個系所，也不能把提及的系所當成使用者的 scope
    （例如「我是電機的，如果我修了資管的演算法...」這類跨系 FAQ）。
    """
    q = normalize_for_search(query)
    faq_hits = [p for p in FAQ_INTENT_PATTERNS if normalize_for_search(p) in q]
    academic_hits = [p for p in ACADEMIC_INTENT_PATTERNS if normalize_for_search(p) in q]

    if faq_hits and academic_hits:
        intent = "mixed"
    elif faq_hits:
        intent = "faq"
    elif academic_hits:
        intent = "academic"
    else:
        intent = "general"

    return {"intent": intent, "faq_hits": faq_hits, "academic_hits": academic_hits}


def _extract_role_aware_entities(query: str, hierarchy: dict) -> list[dict]:
    """把命中的系所分成 user_department / referenced_department / target_department。
    只有 user/target scope 才有資格進入 academic scope filter。
    """
    entities = resolve_academic_entities(query, hierarchy)
    q = normalize_for_search(query)
    results = []

    for e in entities:
        canonical = e["department"]
        names = [canonical, e.get("matched_alias", "")]
        name_pattern = max((normalize_for_search(x) for x in names if x), key=len, default="")
        idx = q.find(name_pattern) if name_pattern else -1
        before = q[:idx] if idx >= 0 else q

        if any(x in before[-12:] for x in ("我是", "我為", "我在", "本系", "我的系")):
            role = "user_department"
        elif idx >= 0 and any(
            x in q[max(0, idx - 8):idx + len(name_pattern) + 8]
            for x in ("的演算法", "的課程", "的課", "別系", "他系")
        ):
            role = "referenced_department"
        else:
            role = "target_department"

        item = dict(e)
        item["role"] = role
        results.append(item)

    return results


def build_faq_retrieval_queries(user_query: str, rewritten_query: str) -> list[str]:
    queries = [q.strip() for q in (user_query, rewritten_query) if q and q.strip()]
    queries = list(dict.fromkeys(queries))
    for extra in (f"常見問題 {user_query}", f"常見問答 {user_query}", f"FAQ {user_query}"):
        if extra not in queries:
            queries.append(extra)
    return queries


def is_faq_metadata(metadata: dict) -> bool:
    meta = metadata or {}
    if str(meta.get("document_type", "")).strip().lower() == "faq":
        return True
    file_name = str(meta.get("file_name", "")).lower()
    return any(p.lower() in file_name for p in FAQ_FILENAME_PATTERNS)


def filter_faq_candidates(candidates: list) -> list:
    faq = []
    for c in candidates:
        if is_faq_metadata(c.metadata):
            c.faq_match = True
            c.fused_score += FAQ_BOOST
            faq.append(c)
        else:
            c.faq_match = False
    return faq


def build_retrieval_queries(user_query: str, rewritten_query: str, hierarchy: dict):
    """建立 retrieval variants；mapping 只用於搜尋擴張，不直接當答案證據。"""
    queries = [q.strip() for q in (user_query, rewritten_query) if q and q.strip()]
    queries = list(dict.fromkeys(queries))

    entities = _extract_role_aware_entities(user_query, hierarchy)
    scope_entities = [e for e in entities if e.get("role") in {"user_department", "target_department"}]
    for entity in scope_entities:
        department, college = entity["department"], entity["college"]
        variants = [f"{department} {user_query}", f"{department} {rewritten_query}"]
        if college:
            variants += [f"{college} {user_query}", f"{college} {rewritten_query}", f"{department} {college} {user_query}"]
        for v in variants:
            if v.strip() and v.strip() not in queries:
                queries.append(v.strip())
    return queries, entities


def _known_colleges_from_hierarchy(hierarchy: dict) -> list[str]:
    return sorted(
        {str(e.get("college", "")).strip() for e in hierarchy.get("departments", {}).values() if isinstance(e, dict) and str(e.get("college", "")).strip()},
        key=len, reverse=True,
    )


def _known_departments_from_hierarchy(hierarchy: dict) -> list[str]:
    names = []
    for canonical, entry in hierarchy.get("departments", {}).items():
        names.append(canonical)
        if isinstance(entry, dict):
            names.extend(entry.get("aliases", []))
    return sorted({str(x).strip() for x in names if str(x).strip()}, key=len, reverse=True)


def detect_document_scope(node) -> dict:
    """只讀 ingestion 已寫入的 scope metadata；不從正文猜 applicability。"""
    metadata = _node_metadata(node)
    return {
        "scope": str(metadata.get("document_scope") or "").strip().lower(),
        "academic_unit": str(metadata.get("academic_unit") or "").strip(),
        "department": str(metadata.get("department") or "").strip(),
        "university_wide": bool(metadata.get("university_wide", False)),
    }


def _scope_rank(candidate, entities: list[dict]):
    """回傳 applicability 類型與分數；明確不相符的 scope 直接淘汰。"""
    if not entities:
        return "unknown", 0.0

    info = detect_document_scope(candidate.node)
    target_departments = {e["department"] for e in entities}
    target_colleges = {e["college"] for e in entities if e.get("college")}

    if info["department"] and info["department"] in target_departments:
        return "direct", 1.0
    if info["academic_unit"] and info["academic_unit"] in target_colleges:
        return "college", 0.95
    if info["university_wide"] or info["scope"] in {"university", "university_wide", "school"}:
        return "university", 0.90
    if info["department"] or info["academic_unit"]:
        return "mismatch", -1.0
    return "unknown", SCOPE_UNKNOWN_PENALTY


def annotate_and_filter_by_scope(candidates: list, entities: list[dict]) -> list:
    """Scope-first：先排除明確錯誤 scope，再讓 hybrid/reranker 排相關性。"""
    if not entities:
        for c in candidates:
            c.scope_match, c.scope_score = "unknown", 0.0
        return candidates

    accepted, unknown, rejected = [], [], 0
    for c in candidates:
        match, score = _scope_rank(c, entities)
        c.scope_match, c.scope_score = match, score
        if match == "mismatch":
            rejected += 1
        elif match == "unknown":
            unknown.append(c)
        else:
            accepted.append(c)

    print(f"scope 過濾：accepted={len(accepted)} unknown={len(unknown)} rejected={rejected}")
    return accepted + unknown if (accepted or unknown) else candidates


def clean_markdown_output(text: str) -> str:
    """砍掉 GPT 的廢話開場白、警語與 Markdown 區塊標籤。"""
    if not text:
        return ""
    text = re.sub(r"^```markdown\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^(I'm unable to|However, I can|Please adjust|Here's a|Here is|這是一份).*?\n+", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n+(Please adjust|Hope this helps|如需修改|希望這對您有幫助).*?$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    return text.strip()


# ============================================================
# 結構化 Markdown 切塊：表格/條文/Q&A 各自成一個不可分割的 chunk
# ============================================================
@dataclass
class Block:
    kind: str  # "table" | "section"
    text: str
    heading: str = ""


_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_ARTICLE_RE = re.compile(r"^第[一二三四五六七八九十百千0-9]+[條章節款]\s*")


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    return bool(_HEADING_RE.match(s)) or bool(_ARTICLE_RE.match(s))


def _heading_label(line: str) -> str:
    s = line.strip()
    if _HEADING_RE.match(s):
        return re.sub(r"^#{1,6}\s+", "", s)
    m = _ARTICLE_RE.match(s)
    return m.group(0).strip() if m else s


def parse_markdown_blocks(text: str) -> list[Block]:
    """依 Markdown 自身結構（標題/條文、表格）切成區塊。

    設計原則：一個表格永遠是一整個區塊，不會被從中間切開；一個標題（或
    「第X條」）到下一個標題/表格之前的所有內容算同一個區塊——這剛好對應
    到 Vision OCR prompt 原本就要求的排版規則（Q&A 用標題+純文字、表格
    用 Markdown 表格），不需要另外寫 Q&A 專用的判斷邏輯。
    """
    blocks: list[Block] = []
    buffer: list[str] = []
    buffer_kind: Optional[str] = None
    current_heading = ""

    def flush():
        nonlocal buffer, buffer_kind
        content = "\n".join(buffer).strip()
        if content:
            blocks.append(Block(kind=buffer_kind or "section", text=content, heading=current_heading))
        buffer, buffer_kind = [], None

    for raw_line in (text or "").split("\n"):
        line = raw_line.rstrip()

        if _is_heading_line(line):
            flush()
            current_heading = _heading_label(line)
            buffer, buffer_kind = [line], "section"
            continue

        if _is_table_line(line):
            if buffer_kind != "table":
                flush()
                buffer_kind = "table"
            buffer.append(line)
            continue

        if buffer_kind == "table":
            flush()
        if buffer_kind is None:
            buffer_kind = "section"
        buffer.append(line)

    flush()
    return blocks


def _hard_split_by_chars(text: str, max_chars: int, header: str = "") -> list[str]:
    """最後手段：直接依字數硬切（優先在換行處斷開），並在每一段前面重複
    header（表格的表頭列／段落的標題），讓每一段拆出來還是看得懂脈絡。
    只有在「表格沒有分隔線可切」或「一整個段落完全沒有空行可切」這種
    正常切法都失效、單一區塊仍然大到可能超過 embedding API 輸入長度上限
    時才會用到。
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current = [header] if header else []
    current_len = len(header)

    for line in lines:
        if current_len + len(line) + 1 > max_chars and len(current) > (1 if header else 0):
            chunks.append("\n".join(current))
            current = [header] if header else []
            current_len = len(header)
        current.append(line)
        current_len += len(line) + 1

    if current and (not header or len(current) > 1):
        chunks.append("\n".join(current))

    return chunks or [text[:max_chars]]


def _split_long_section(block: Block, max_chars: int) -> list[Block]:
    """一般段落太長時在空行處切；表格原則上不切。

    但兩者都有一個 MAX_HARD_CHUNK_CHARS 的硬上限保底：如果段落完全沒有
    空行可切、或表格本身就大到可能超過 OpenAI embedding API 的輸入長度
    上限（8192 tokens，曾經真的因為一個超大表格 chunk 直接建索引失敗），
    就依字數硬切、表格切開時在每一段前面重複表頭列，避免整個索引建立
    失敗，也不會讓拆出來的段落完全失去脈絡。
    """
    if block.kind == "table":
        if len(block.text) <= MAX_HARD_CHUNK_CHARS:
            return [block]
        lines = block.text.split("\n")
        header = "\n".join(lines[:2]) if len(lines) >= 2 else ""
        pieces = _hard_split_by_chars(block.text, MAX_HARD_CHUNK_CHARS, header=header)
        total = len(pieces)
        return [
            Block(kind="table", text=p, heading=f"{block.heading} ({i}/{total})" if total > 1 else block.heading)
            for i, p in enumerate(pieces, 1)
        ]

    if len(block.text) <= max_chars:
        return [block]

    paragraphs = [p for p in re.split(r"\n\s*\n", block.text) if p.strip()]
    if len(paragraphs) <= 1:
        pieces = _hard_split_by_chars(block.text, min(max_chars, MAX_HARD_CHUNK_CHARS))
        total = len(pieces)
        return [
            Block(kind="section", text=p, heading=f"{block.heading} ({i}/{total})" if total > 1 else block.heading)
            for i, p in enumerate(pieces, 1)
        ]

    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n\n{p}" if current else p
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = p
        else:
            current = candidate
    if current:
        chunks.append(current)

    # 個別段落本身仍可能超過硬上限（例如某一段本身就是一大坨沒有空行的
    # 文字），逐一再檢查一次。
    final_chunks: list[str] = []
    for c in chunks:
        if len(c) <= MAX_HARD_CHUNK_CHARS:
            final_chunks.append(c)
        else:
            final_chunks.extend(_hard_split_by_chars(c, MAX_HARD_CHUNK_CHARS))

    total = len(final_chunks)
    return [
        Block(kind="section", text=c, heading=f"{block.heading} ({i}/{total})" if total > 1 else block.heading)
        for i, c in enumerate(final_chunks, 1)
    ]


def _strip_marker(text: str) -> str:
    return text.replace(TABLE_CONTINUES_MARKER, "").rstrip()


def build_chunks_from_markdown(markdown_text: str, base_metadata: dict) -> list[dict]:
    """把一頁（或合併後的一段）Markdown 依結構切成 chunk，並在每個 chunk
    前面加上身份前綴（檔名/文件類型/所屬段落）再回傳，讓 chunk 被單獨
    embedding/檢索到時也知道自己是誰、屬於哪份文件。
    """
    blocks = parse_markdown_blocks(markdown_text)
    doc_label = "【常見問題】" if base_metadata.get("document_type") == "faq" else "【正式規定】"
    file_name = base_metadata.get("file_name", "未知文件")
    page_label = base_metadata.get("page_label", "")

    chunks: list[dict] = []
    for block in blocks:
        for piece in _split_long_section(block, MAX_SECTION_CHUNK_CHARS):
            prefix_bits = [doc_label + file_name]
            if page_label:
                prefix_bits.append(f"第{page_label}頁")
            if piece.heading:
                prefix_bits.append(piece.heading)
            prefix = "、".join(prefix_bits)

            chunks.append({
                "text": f"{prefix}\n{piece.text}",
                "metadata": {
                    **base_metadata,
                    "chunk_kind": piece.kind,
                    "section_heading": piece.heading,
                },
            })

    return chunks


# ============================================================
# PDF ingestion
#
# ⚠️ 這裡吃過一次真實的教訓：一開始的版本「不管有沒有表格，一律用
# Vision OCR」，理由是原本 has_tables_in_pdf() 那個「儲存格平均字數 <
# 35」的判斷式不可靠。但拿真實文件實測後發現：GPT-4o Vision OCR 本身在
# 「常見問題.pdf」這種密集雙欄版面上會整段幻覺／唸錯字（例如把「我是
# 電機的...如我修了資管的演算法」讀成「我完成輸的...如果修了這堂的演算法」
# ——調高 DPI 到 300 也一樣會錯，不是解析度問題）。
#
# 而這份 PDF其實是「有真正文字層的數位文件」，不是掃描圖檔：
# pdfplumber.extract_text()／extract_tables() 直接就能 100% 準確、零成本、
# 零幻覺地把內容抓出來（已實測驗證）。所以現在的策略改成：
#   1. 優先用 pdfplumber 原生抽取（表格用 extract_tables() 精準保留欄位，
#      比 Vision OCR 唸出來的內容更可信，因為完全沒有 LLM 參與）。
#   2. 只有整份 PDF 幾乎抓不到文字層（判定為掃描圖檔）時，才退回
#      Vision OCR（帶跨頁表格續接偵測）。
# 這樣「表格的欄位結構」跟「文字的忠實度」兩件事都不用犧牲。
# ============================================================
def _table_to_markdown(rows: list[list]) -> str:
    def clean(cell) -> str:
        return (str(cell) if cell is not None else "").replace("\n", " ").strip()

    lines = []
    for i, row in enumerate(rows):
        cells = [clean(c) for c in row]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(lines)


def _row_looks_truncated(row: list) -> bool:
    """一列裡有 None 儲存格（pdfplumber 抓不到值，不是「本來就空白」的
    空字串），視為這一列可能被頁面截斷、延續到下一頁。
    """
    return any(c is None for c in row)


def _extract_pdf_page_raw(page) -> dict:
    return {"tables": page.extract_tables(), "text": page.extract_text() or ""}


def merge_continued_pdfplumber_tables(pages_data: list[dict]) -> list[dict]:
    """把「表格看起來被頁面截斷」的相鄰頁合併成同一個邏輯頁。

    不強求跨頁的欄位精準對齊（不同頁的表格常常欄數對不上，例如原本
    9 欄的表格續到下一頁變成 8 欄），只求把兩頁的表格內容放進同一個
    chunk——這樣即使欄位沒有完美接起來，LLM 讀到同一個 chunk 裡「上一頁
    最後一列」跟「下一頁第一列」還是拼得出完整資訊，這才是真正重要的
    （已用真實案例的欄位資料驗證過這個判斷邏輯）。
    """
    merged: list[dict] = []
    i = 0
    while i < len(pages_data):
        page = {**pages_data[i], "tables": [list(t) for t in pages_data[i]["tables"]]}
        labels = [str(page["page"])]

        while (
            i < len(pages_data) - 1
            and page["tables"]
            and page["tables"][-1]
            and _row_looks_truncated(page["tables"][-1][-1])
            and pages_data[i + 1]["tables"]
        ):
            i += 1
            next_page = pages_data[i]
            next_tables = [list(t) for t in next_page["tables"]]
            page["tables"][-1] = page["tables"][-1] + next_tables[0]
            page["tables"].extend(next_tables[1:])
            page["text"] = page["text"] + "\n" + next_page["text"]
            labels.append(str(next_page["page"]))

        page["page_label"] = labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"
        merged.append(page)
        i += 1

    return merged


def extract_pdf_pages_structured(pdf_path: str) -> list[tuple[str, str]]:
    """優先用 pdfplumber 原生抽取；整份 PDF 幾乎沒有文字層時才退回 Vision OCR。"""
    filename = os.path.basename(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        pages_data = [
            {"page": i, **_extract_pdf_page_raw(page)}
            for i, page in enumerate(pdf.pages, 1)
        ]

    total_text_len = sum(len(p["text"].strip()) for p in pages_data)
    avg_text_len = total_text_len / max(len(pages_data), 1)

    if avg_text_len < 20:
        print(f"[{filename}] 平均每頁文字層長度只有 {avg_text_len:.0f}，判定為掃描圖檔，改用 Vision OCR。")
        vision_pages = convert_pdf_to_markdown_pages_via_vision(pdf_path)
        return merge_continued_vision_pages(vision_pages)

    merged_pages = merge_continued_pdfplumber_tables(pages_data)

    results = []
    for page in merged_pages:
        table_blocks = [_table_to_markdown(t) for t in page["tables"] if t]
        if table_blocks:
            page_md = "\n\n".join(table_blocks)
        else:
            page_md = page["text"]
        results.append((page["page_label"], page_md))

    return results


def convert_pdf_to_markdown_pages_via_vision(pdf_path: str) -> list[tuple[int, str]]:
    """逐頁 Vision OCR 成 Markdown；page-aware JSON cache。"""
    filename = os.path.basename(pdf_path)
    cache_path = os.path.join(CACHE_MD_DIR, f"{filename}.pages.json")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return [(int(x["page"]), x["text"]) for x in cached]

    print(f"[Vision OCR] 正在處理 {filename}...")
    images = convert_from_path(pdf_path, dpi=200, poppler_path=POPPLER_PATH)
    total_pages = len(images)
    page_results: list[tuple[int, str]] = []
    previous_page_snippet = ""

    for i, img in enumerate(images, 1):
        print(f"  --> OCR 第 {i} / {total_pages} 頁...")

        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        user_prompt = (
            "你是一個極度精準的文件視覺 OCR 引擎。請將圖片中的內容 100% 忠實轉錄為 Markdown。\n\n"
            "【排版規則】：\n"
            "1. 表格（資料對照表/門檻規定/收費明細等網格內容）請用 Markdown 表格 (|...|) 精準還原，"
            "欄位數要跟圖片一致，儲存格文字再長都要完整轉錄，不可截斷或摘要。\n"
            "2. Q&A、一般條文、條列式說明請用標題 (#, ##) 或純文字/清單 (- ) 輸出，絕對不要硬排成表格。\n"
            "3. 條文請保留原本的「第X條」編號在該段開頭。\n"
            "4. 空白填寫欄位（簽章欄、日期欄等）請獨立列在最下方，不要跟資料表格混在一起。\n"
            f"5. 如果畫面上的表格看起來在頁面底部被截斷（下面還有列，但沒有畫完/沒看到頁面收尾），"
            f"請在輸出的最後一行加上 {TABLE_CONTINUES_MARKER}；如果表格在這一頁完整結束，就不要加。\n"
            "【嚴格禁令】：必須一字不漏轉錄，不可發明詞彙，嚴禁輸出 ```markdown 標籤或任何開場白/結尾說明。"
        )
        if previous_page_snippet:
            user_prompt += f"\n【跨頁銜接參考】（上一頁結尾，僅供辨識連貫用，不要重複輸出）：\n{previous_page_snippet}\n"

        response = openai_client.chat.completions.create(
            model=os.getenv("RAG_OCR_MODEL", "gpt-4o"),
            messages=[
                {
                    "role": "system",
                    "content": "你是專業的文件 OCR 與表格轉錄引擎，只輸出圖片中實際存在的文字與結構，不得自行補充、不得輸出招呼語或包裝標籤。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}", "detail": "high"}},
                    ],
                },
            ],
            temperature=0,
            max_tokens=4000,
        )

        page_md = clean_markdown_output(response.choices[0].message.content or "")
        page_results.append((i, page_md))
        previous_page_snippet = page_md[-500:] if page_md else ""

    os.makedirs(CACHE_MD_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump([{"page": p, "text": t} for p, t in page_results], f, ensure_ascii=False, indent=2)

    return page_results


def has_table_continuation(page_markdown: str) -> bool:
    """判斷這一頁（Vision OCR 產出的 Markdown）是否以「被截斷的表格」結尾。

    只用在掃描圖檔的 fallback 路徑——一般數位文件已經走 pdfplumber 的
    _row_looks_truncated()，不需要靠這個文字層級的判斷。
    """
    stripped = (page_markdown or "").strip()
    if stripped.endswith(TABLE_CONTINUES_MARKER):
        return True

    blocks = parse_markdown_blocks(stripped)
    if not blocks or blocks[-1].kind != "table":
        return False
    tail_text = blocks[-1].text
    closing_hints = ("合計", "總計", "簽章", "簽名", "以上", "備註")
    return not any(h in tail_text for h in closing_hints)


def merge_continued_vision_pages(pages: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """掃描圖檔 fallback 專用：把「表格被頁面截斷」的相鄰頁合併成同一個
    邏輯頁，page_label 變成範圍（例如 "1-2"）。
    """
    merged: list[tuple[str, str]] = []
    i = 0
    while i < len(pages):
        page_no, text = pages[i]
        labels = [str(page_no)]
        combined = _strip_marker(text)

        while i < len(pages) - 1 and has_table_continuation(pages[i][1]):
            i += 1
            next_no, next_text = pages[i]
            labels.append(str(next_no))
            combined = combined + "\n" + _strip_marker(next_text)

        label = labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"
        merged.append((label, combined))
        i += 1

    return merged


# ============================================================
# DOCX ingestion：直接用 python-docx 走段落 + 表格，原生保留表格結構
# （舊版用 llama_index 的 DocxReader 純文字抽取，docx 裡的表格會被拉平、
# 完全失去欄位結構——這裡改成自己走 python-docx 的 paragraphs/tables，
# 表格輸出成 Markdown 表格，跟 PDF 走同一套結構化切塊邏輯。）
# ============================================================
def _docx_table_to_markdown(table) -> str:
    rows = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
    if len(rows) >= 1:
        col_count = len(table.rows[0].cells)
        separator = "| " + " | ".join(["---"] * col_count) + " |"
        rows.insert(1, separator)
    return "\n".join(rows)


def extract_docx_as_markdown(docx_path: str) -> str:
    """依段落/表格在文件中的原始順序組回一份 Markdown（表格用 |...| 表示），
    讓 DOCX 也能套用跟 PDF 一樣的結構化切塊邏輯。
    """
    doc = python_docx.Document(docx_path)
    body = doc.element.body
    lines: list[str] = []

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            for p in doc.paragraphs:
                if p._p is child:
                    text = p.text.strip()
                    if text:
                        style = (p.style.name or "").lower() if p.style else ""
                        if "heading" in style or "title" in style:
                            lines.append(f"## {text}")
                        else:
                            lines.append(text)
                    break
        elif tag == "tbl":
            for t in doc.tables:
                if t._tbl is child:
                    md_table = _docx_table_to_markdown(t)
                    if md_table.strip():
                        lines.append(md_table)
                    break

    return "\n".join(lines)


# ============================================================
# 舊版 .doc（二進位格式）ingestion：透過 LibreOffice 無頭轉檔成 .docx
#
# .doc 是舊的 OLE2 二進位格式，跟 .docx（其實是 zip 包 XML）完全不是同一種
# 檔案結構，python-docx 讀不了。與其要求每個 .doc 手動開 Word 另存新檔，
# 這裡改成呼叫 LibreOffice 的無頭（headless）轉檔功能自動轉成 .docx，
# 轉過的結果快取起來（第一次轉完之後就不會再轉），之後就跟一般 .docx
# 走同一套解析／切塊邏輯。需要先裝 LibreOffice（免費）：
# https://www.libreoffice.org/download/download/
# ============================================================
DOC_CONVERTED_CACHE_DIR = "./doc_converted_cache"
LIBREOFFICE_PATH = os.getenv("LIBREOFFICE_PATH", None)

_LIBREOFFICE_CANDIDATE_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_soffice() -> Optional[str]:
    """找 LibreOffice 的 soffice 執行檔：優先看 LIBREOFFICE_PATH 環境變數，
    再看常見安裝路徑，最後看 PATH 裡有沒有 soffice。找不到回傳 None。
    """
    if LIBREOFFICE_PATH and os.path.exists(LIBREOFFICE_PATH):
        return LIBREOFFICE_PATH

    for candidate in _LIBREOFFICE_CANDIDATE_PATHS:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("soffice")


def convert_doc_to_docx(doc_path: str) -> Optional[str]:
    """用 LibreOffice 無頭轉檔把 .doc 轉成 .docx，回傳轉檔後的路徑；
    找不到 LibreOffice 或轉檔失敗則回傳 None（呼叫端要自行處理略過）。
    """
    filename = os.path.basename(doc_path)
    os.makedirs(DOC_CONVERTED_CACHE_DIR, exist_ok=True)
    cached_path = os.path.join(DOC_CONVERTED_CACHE_DIR, os.path.splitext(filename)[0] + ".docx")

    if os.path.exists(cached_path) and os.path.getmtime(cached_path) >= os.path.getmtime(doc_path):
        return cached_path

    soffice = _find_soffice()
    if not soffice:
        print(
            f"[{filename}] 找不到 LibreOffice（soffice），無法自動轉檔。"
            "請先安裝 https://www.libreoffice.org/download/download/ "
            "（或設定 LIBREOFFICE_PATH 環境變數指向 soffice.exe）。"
        )
        return None

    print(f"[{filename}] 用 LibreOffice 轉成 .docx...")
    try:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", DOC_CONVERTED_CACHE_DIR, doc_path],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"[{filename}] LibreOffice 轉檔逾時（超過 120 秒），略過這個檔案。")
        return None

    if result.returncode != 0 or not os.path.exists(cached_path):
        print(f"[{filename}] LibreOffice 轉檔失敗：{result.stderr.strip() or result.stdout.strip()}")
        return None

    return cached_path


# ============================================================
# Scope metadata（deterministic，不用 LLM 猜）
# ============================================================
def enrich_document_scope_metadata(metadata: dict, file_name: str, hierarchy: dict) -> dict:
    meta = dict(metadata or {})
    fname = str(file_name or "")
    norm = normalize_for_search(fname)

    meta["document_type"] = "faq" if any(p.lower() in fname.lower() for p in FAQ_FILENAME_PATTERNS) else meta.get("document_type", "formal_policy")

    for department in _known_departments_from_hierarchy(hierarchy):
        if normalize_for_search(department) in norm:
            meta["document_scope"] = "department"
            for canonical, entry in hierarchy.get("departments", {}).items():
                if department == canonical or department in entry.get("aliases", []):
                    meta["department"] = canonical
                    meta["academic_unit"] = entry.get("college", "")
                    break
            meta["university_wide"] = False
            return meta

    for college in _known_colleges_from_hierarchy(hierarchy):
        if normalize_for_search(college) in norm:
            meta["document_scope"] = "college"
            meta["academic_unit"] = college
            meta["university_wide"] = False
            return meta

    if ("全校" in fname) or ("國立中央大學" in fname and "大學部" in fname):
        meta["document_scope"] = "university"
        meta["university_wide"] = True
        return meta

    meta.setdefault("document_scope", "unknown")
    meta.setdefault("university_wide", False)
    return meta


# ============================================================
# load_documents：每個回傳的 TextNode 就是一個最終 chunk
# （不再另外跑 SentenceSplitter/SentenceWindowNodeParser——那是給「沒有
# 結構」的純文字切的，我們的 chunk 已經是表格/條文/Q&A 這種天然完整的
# 單位，不需要再切一次，也不需要用 sentence window 展開上下文）。
#
# ⚠️ 這裡刻意直接建 TextNode（而不是 Document 再讓 VectorStoreIndex.
# from_documents() 內部重新解析），並且自己指定 id_：因為 from_documents()
# 內部會重新產生一組跟 Document.doc_id 完全無關的新 node id，如果 BM25
# 索引記的是 doc_id，之後對著向量索引的 docstore 用這個 id 查詢一定查
#不到、BM25 那一半會整個悄悄失效（已用假的 embedding 實測驗證過這個
# 陷阱）。自己組 TextNode、自己指定 id_，兩邊的 id 才會是同一份。
# ============================================================
def _stable_chunk_id(file_name: str, page_label: str, index_in_doc: int) -> str:
    raw = f"{file_name}|{page_label}|{index_in_doc}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_documents() -> list[TextNode]:
    nodes: list[TextNode] = []
    hierarchy = load_academic_hierarchy()

    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            file_path = os.path.join(root, file)
            if file.startswith(".") or file.startswith("~$") or os.path.getsize(file_path) == 0:
                continue

            try:
                chunk_index = 0

                if file.lower().endswith(".pdf"):
                    merged_pages = extract_pdf_pages_structured(file_path)
                    for page_label, page_text in merged_pages:
                        if not page_text.strip():
                            continue
                        base_metadata = enrich_document_scope_metadata(
                            {"file_name": file, "file_path": file_path, "page_label": page_label, "source_type": "pdf"},
                            file, hierarchy,
                        )
                        for chunk in build_chunks_from_markdown(page_text, base_metadata):
                            node_id = _stable_chunk_id(file, page_label, chunk_index)
                            nodes.append(TextNode(text=chunk["text"], metadata=chunk["metadata"], id_=node_id))
                            chunk_index += 1

                elif file.lower().endswith(".docx") or file.lower().endswith(".doc"):
                    docx_path = file_path
                    if file.lower().endswith(".doc"):
                        # 舊版二進位格式，先透過 LibreOffice 無頭轉檔成 .docx
                        # （轉檔結果會快取，之後不會重轉），轉不成功就跳過
                        # 並記錄原因，不讓例外悄悄吞掉、讓人以為有處理到。
                        docx_path = convert_doc_to_docx(file_path)
                        if docx_path is None:
                            continue

                    markdown_text = extract_docx_as_markdown(docx_path)
                    base_metadata = enrich_document_scope_metadata(
                        {"file_name": file, "file_path": file_path, "source_type": "docx"}, file, hierarchy,
                    )
                    for chunk in build_chunks_from_markdown(markdown_text, base_metadata):
                        node_id = _stable_chunk_id(file, "", chunk_index)
                        nodes.append(TextNode(text=chunk["text"], metadata=chunk["metadata"], id_=node_id))
                        chunk_index += 1

            except Exception as e:
                print(f"[檔案讀取失敗] {file}: {e}")

    print(f"[Academic Agent] load_documents 完成，共 {len(nodes)} 個 chunk。")
    return nodes


def get_latest_data_mtime() -> float:
    latest = 0.0
    if not os.path.exists(DATA_DIR):
        return latest
    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            if file.startswith(".") or file.startswith("~$"):
                continue
            latest = max(latest, os.path.getmtime(os.path.join(root, file)))
    return latest


# ============================================================
# 索引建立：向量索引 + BM25 索引一起建、一起 persist
# ============================================================
CURRENT_SCHEMA_VERSION = "9-structural-chunking-bm25"


def _bm25_tokenize(text: str) -> list[str]:
    """跟原本 lexical scorer 同樣的策略：中文用 2-gram、英數保留完整
    token——差別是這裡回傳 list（保留重複次數），BM25 才能正確算詞頻。
    """
    raw = normalize_for_search(text)
    cjk = re.findall(r"[㐀-鿿]", raw)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    latin = re.findall(r"[a-z0-9][a-z0-9._/-]*", raw)
    return bigrams + latin


def get_or_create_index():
    mtime_file = os.path.join(PERSIST_DIR, ".data_mtime")
    schema_file = os.path.join(PERSIST_DIR, ".rag_schema_version")

    current_mtime = get_latest_data_mtime()
    need_rebuild = not os.path.exists(PERSIST_DIR)

    if not need_rebuild:
        if os.path.exists(mtime_file):
            with open(mtime_file, "r", encoding="utf-8") as f:
                if current_mtime > float(f.read().strip()):
                    need_rebuild = True
        else:
            need_rebuild = True

    if not need_rebuild:
        if not os.path.exists(schema_file):
            need_rebuild = True
        else:
            with open(schema_file, "r", encoding="utf-8") as f:
                need_rebuild = f.read().strip() != CURRENT_SCHEMA_VERSION

    if need_rebuild:
        if os.path.exists(PERSIST_DIR):
            shutil.rmtree(PERSIST_DIR)

        print("正在建立索引（結構化 chunk + 向量 + BM25）...")
        nodes = load_documents()

        # 直接用 nodes= 建索引（不是 from_documents()），這樣 node 的 id_
        # 才會維持我們自己指定的 _stable_chunk_id，BM25 索引才能跟向量
        # 索引的 docstore 用同一組 id 對得起來（見 load_documents 的說明）。
        index = VectorStoreIndex(nodes=nodes)
        os.makedirs(PERSIST_DIR, exist_ok=True)
        index.storage_context.persist(persist_dir=PERSIST_DIR)

        bm25_corpus = [_bm25_tokenize(node.get_content()) for node in nodes]
        bm25 = BM25Okapi(bm25_corpus) if bm25_corpus else None
        node_ids = [node.id_ for node in nodes]
        with open(BM25_INDEX_PATH, "wb") as f:
            pickle.dump({"bm25": bm25, "node_ids": node_ids}, f)

        with open(mtime_file, "w", encoding="utf-8") as f:
            f.write(str(current_mtime))
        with open(schema_file, "w", encoding="utf-8") as f:
            f.write(CURRENT_SCHEMA_VERSION)

        print(f"索引建立完成，共 {len(nodes)} 個 chunk。")
    else:
        print("載入既有索引...")
        storage_context = StorageContext.from_defaults(persist_dir=PERSIST_DIR)
        index = load_index_from_storage(storage_context)

    return index


def _load_bm25():
    if not os.path.exists(BM25_INDEX_PATH):
        return None, []
    with open(BM25_INDEX_PATH, "rb") as f:
        data = pickle.load(f)
    return data.get("bm25"), data.get("node_ids", [])


# ============================================================
# Retrieval：candidate 融合、scope 過濾、rerank
# ============================================================
def _node_text(node) -> str:
    if node is None:
        return ""
    try:
        return node.get_content() or ""
    except Exception:
        return getattr(node, "text", "") or ""


def _node_id(node) -> str:
    if node is None:
        return ""
    for attr in ("node_id", "id_"):
        value = getattr(node, attr, None)
        if value:
            return str(value)
    return str(id(node))


def _node_metadata(node) -> dict:
    return dict(getattr(node, "metadata", {}) or {})


def _unwrap_retrieval_item(item):
    if item is None:
        return None
    inner = getattr(item, "node", None)
    return inner if inner is not None else item


@dataclass
class RetrievalCandidate:
    node_id: str
    text: str
    metadata: dict
    dense_score: float = 0.0
    lexical_score: float = 0.0
    fused_score: float = 0.0
    sources: tuple = field(default_factory=tuple)
    node: object = None


def _candidate_from_node(node, dense_score=0.0, lexical_score=0.0, source=""):
    node = _unwrap_retrieval_item(node)
    if node is None:
        return None
    return RetrievalCandidate(
        node_id=_node_id(node), text=_node_text(node), metadata=_node_metadata(node),
        dense_score=float(dense_score or 0.0), lexical_score=float(lexical_score or 0.0),
        sources=(source,) if source else tuple(), node=node,
    )


def bm25_retrieve(index, query: str, top_k: int = LEXICAL_TOP_K) -> list[RetrievalCandidate]:
    bm25, node_ids = _load_bm25()
    if bm25 is None or not node_ids:
        return []

    scores = bm25.get_scores(_bm25_tokenize(query))
    ranked = sorted(zip(node_ids, scores), key=lambda x: x[1], reverse=True)[:top_k]

    docstore = index.docstore
    candidates = []
    for node_id, score in ranked:
        if score <= 0:
            continue
        try:
            node = docstore.get_node(node_id)
        except Exception:
            continue
        candidate = _candidate_from_node(node, lexical_score=score, source="bm25")
        if candidate:
            candidates.append(candidate)
    return candidates


def _normalize_scores(candidates: list, attr_name: str):
    values = [getattr(c, attr_name) for c in candidates]
    if not values:
        return
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    for c in candidates:
        setattr(c, attr_name, (getattr(c, attr_name) - lo) / span)


def fuse_candidates(dense_candidates, lexical_candidates, dense_weight=0.65, lexical_weight=0.35) -> list:
    merged: dict[str, RetrievalCandidate] = {}
    for candidate in dense_candidates + lexical_candidates:
        existing = merged.get(candidate.node_id)
        if existing is None:
            merged[candidate.node_id] = candidate
            continue
        existing.dense_score = max(existing.dense_score, candidate.dense_score)
        existing.lexical_score = max(existing.lexical_score, candidate.lexical_score)
        existing.sources = tuple(sorted(set(existing.sources + candidate.sources)))

    candidates = list(merged.values())
    _normalize_scores(candidates, "dense_score")
    _normalize_scores(candidates, "lexical_score")
    for c in candidates:
        c.fused_score = dense_weight * c.dense_score + lexical_weight * c.lexical_score
    candidates.sort(key=lambda x: x.fused_score, reverse=True)
    return candidates


def prepare_nodes_for_rerank(candidates: list, max_chars: int = RERANK_MAX_CHARS) -> list[NodeWithScore]:
    prepared = []
    for candidate in candidates:
        text = candidate.text or ""
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[內容已截斷，僅供 rerank 判斷]"
        node = TextNode(text=text, metadata=dict(candidate.metadata or {}), id_=candidate.node_id)
        prepared.append(NodeWithScore(node=node, score=float(candidate.fused_score)))
    return prepared


def candidates_from_reranked(reranked_nodes, original_candidates: list) -> list:
    by_id = {c.node_id: c for c in original_candidates}
    output = []
    for item in reranked_nodes:
        node = _unwrap_retrieval_item(item)
        if node is None:
            continue
        candidate = by_id.get(_node_id(node)) or _candidate_from_node(node, source="rerank")
        if candidate:
            candidate.node = node
            output.append(candidate)
    return output


def build_source_context(candidates: list) -> str:
    """chunk 本身已經帶了身份前綴，這裡再額外附上結構化的來源標記，
    讓生成答案時引用 [SOURCE N] 能對應回實際檔名/頁碼。
    """
    blocks = []
    for i, c in enumerate(candidates, 1):
        meta = c.metadata or {}
        source = meta.get("file_name", "未知文件")
        if meta.get("page_label"):
            source += f"｜第 {meta['page_label']} 頁"
        blocks.append(f"[SOURCE {i}]\n來源：{source}\n內容：\n{c.text}")

    context = "\n\n====================\n\n".join(blocks)
    if len(context) <= MAX_CONTEXT_CHARS:
        return context

    kept, size = [], 0
    for block in blocks:
        if size + len(block) > MAX_CONTEXT_CHARS:
            break
        kept.append(block)
        size += len(block)
    return "\n\n====================\n\n".join(kept)


def expand_query_for_retrieval(user_query: str, history_str: str = "") -> str:
    """只做語意重寫，禁止自行新增學院、規章年份、法規內容——避免 query
    expansion 本身把 retrieval 帶偏。學院/系所擴張交給 academic_hierarchy.json
    這種可審核的 mapping 處理，不讓 LLM 猜。
    """
    prompt = f"""
    你是法規檢索的 query rewrite 模組。

    目標：把使用者問題改寫成一條「更適合搜尋文件」的查詢。
    硬性規則：
    1. 保留使用者的所有核心條件、系所、身份、年級、年份、費用/門檻/申請等詞。
    2. 可以把口語詞改成較正式的同義詞，但不得新增使用者沒有提供的事實。
    3. 不得自行推導學院名稱、規章年份、法規條號或任何答案。
    4. 若問題很短（例如「那資工呢」「多少錢」），可以利用對話歷史補回被省略的核心對象。
    5. 只輸出一條查詢字串，不要解釋。

    對話歷史：
    {history_str}

    使用者問題：
    {user_query}
    """
    try:
        response = llm_smart.chat([
            ChatMessage(role=MessageRole.SYSTEM, content=prompt),
            ChatMessage(role=MessageRole.USER, content=user_query),
        ])
        return response.message.content.strip() or user_query
    except Exception:
        return user_query


ANSWER_SYSTEM_PROMPT = """
你是 NCUXplore 的校園法規檢索助理。

你的唯一任務：根據「檢索到的來源內容」直接回答使用者問題。

【回答優先順序】
1. 先回答問題本身，不要先講搜尋過程。
2. 使用者問「多少、哪個、是否、需要什麼、截止日」等具體問題時，第一段就給具體答案。
3. 只使用來源明確支持的資訊。不要用常識補洞。
4. 如果來源不足，明確說「目前檢索到的文件不足以確認」，並指出缺少什麼。
5. 如果來源彼此衝突，不要自行猜哪個是真的；列出衝突，並優先採用來源中明確標示「最新/修訂/適用年度」者，同時說明判斷依據。
6. 絕對不要把「文件沒有標年份」自行推論成「舊版」。
7. 不要自行推導系所隸屬學院、適用年度或資格條件；除非來源本身有明確寫出，或使用者
   訊息裡的【已驗證 academic entities】已經給出這個對應關係（那是系統算好的事實，不是
   你在猜，可以直接拿來對應來源裡的學院簡稱）。
8. FAQ 與正式法規同時存在時：正式法規回答「規定」，FAQ 只能補充實務說明。
9. 不要把與問題無關的行政資訊塞進答案。
10. 答案以條列為主，簡潔但要完整。

【來源引用】
每一個重要結論後面都要附來源標記，例如 [SOURCE 1]。
如果同一結論由多個來源支持，可以寫 [SOURCE 1][SOURCE 3]。
不要捏造不存在的 SOURCE 編號。

【禁止】
- 不要說「根據我的知識」
- 不要說「我建議你去查」
- 不要輸出搜尋/檢索流程
- 不要重複問題
"""


def query_academic_knowledge(query_str: str, history_str: str = "") -> dict:
    try:
        index = get_or_create_index()
        print(f'原始問題：「{query_str}」')

        intent_info = detect_query_intent(query_str)
        intent = intent_info["intent"]
        print(f"Query intent：{intent}（faq_hits={intent_info['faq_hits']}, academic_hits={intent_info['academic_hits']}）")

        rewritten_query = expand_query_for_retrieval(query_str, history_str)
        print(f"Retrieval query：「{rewritten_query}」")

        hierarchy = load_academic_hierarchy()
        retrieval_queries, resolved_entities = build_retrieval_queries(query_str, rewritten_query, hierarchy)
        faq_queries = build_faq_retrieval_queries(query_str, rewritten_query)

        if intent == "faq":
            active_queries = faq_queries
        elif intent == "mixed":
            active_queries = list(dict.fromkeys(faq_queries + retrieval_queries))
        else:
            active_queries = retrieval_queries
        print("Retrieval variants：" + " | ".join(active_queries))

        retriever = index.as_retriever(similarity_top_k=VECTOR_TOP_K)
        dense_candidates: list[RetrievalCandidate] = []
        for q in active_queries:
            try:
                for item in retriever.retrieve(q):
                    c = _candidate_from_node(item, dense_score=getattr(item, "score", 0.0) or 0.0, source="dense")
                    if c:
                        dense_candidates.append(c)
            except Exception as e:
                print(f"Dense retrieval 失敗：{e}")

        dense_by_id: dict[str, RetrievalCandidate] = {}
        for c in dense_candidates:
            old = dense_by_id.get(c.node_id)
            if old is None:
                dense_by_id[c.node_id] = c
            else:
                old.dense_score = max(old.dense_score, c.dense_score)
        dense_candidates = list(dense_by_id.values())

        lexical_candidates: list[RetrievalCandidate] = []
        for q in active_queries:
            lexical_candidates.extend(bm25_retrieve(index, q, LEXICAL_TOP_K))

        lexical_by_id: dict[str, RetrievalCandidate] = {}
        for c in lexical_candidates:
            old = lexical_by_id.get(c.node_id)
            if old is None:
                lexical_by_id[c.node_id] = c
            else:
                old.lexical_score = max(old.lexical_score, c.lexical_score)
        lexical_candidates = list(lexical_by_id.values())

        candidates = fuse_candidates(dense_candidates, lexical_candidates)
        print(f"hybrid 檢索候選：{len(candidates)}（dense={len(dense_candidates)}, bm25={len(lexical_candidates)}）")

        if intent == "faq":
            faq_candidates = filter_faq_candidates(candidates)
            if faq_candidates:
                candidates = faq_candidates
            elif not FAQ_ONLY_IF_MATCH:
                pass
        elif intent == "mixed":
            filter_faq_candidates(candidates)
        elif intent == "academic":
            candidates = annotate_and_filter_by_scope(
                candidates, [e for e in resolved_entities if e.get("role") in {"user_department", "target_department"}],
            )
        else:
            for c in candidates:
                c.scope_match, c.scope_score = "unknown", 0.0

        if not candidates:
            return {"answer": "目前檢索到的文件不足以確認這個問題的具體答案。", "sources": []}

        candidates.sort(
            key=lambda c: (
                1 if getattr(c, "faq_match", False) else 0,
                getattr(c, "scope_score", 0.0) if intent == "academic" else 0.0,
                c.fused_score,
            ),
            reverse=True,
        )

        rerank_pool = candidates[:max(VECTOR_TOP_K, LEXICAL_TOP_K)]
        reranker = LLMRerank(choice_batch_size=RERANK_BATCH_SIZE, top_n=RERANK_TOP_N, llm=llm_smart)
        reranked_nodes = reranker.postprocess_nodes(
            prepare_nodes_for_rerank(rerank_pool), query_bundle=QueryBundle(query_str),
        )
        final_candidates = candidates_from_reranked(reranked_nodes, rerank_pool) or rerank_pool[:RERANK_TOP_N]

        context_str = build_source_context(final_candidates)
        user_prompt = f"""
【檢索來源】
{context_str}

【已驗證 academic entities】
{json.dumps(resolved_entities, ensure_ascii=False)}
這是系統用可審核的系所/學院對照表算出來的事實，不是你自己推導的，可以直接採信、
用來對應來源裡的學院簡稱（例如「資電」＝資訊電機學院）。entity role 很重要：
user_department / target_department 才可能代表使用者實際要問的範圍；
referenced_department 只是問題裡順帶提到的對象，不能拿來限制檢索範圍。

【使用者問題】
{query_str}

請直接回答問題。若來源不足，明確說明不足之處，不要自行補答案。
"""
        response = openai_client.chat.completions.create(
            model=os.getenv("RAG_ANSWER_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": ANSWER_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
            temperature=0,
            max_tokens=1200,
        )
        answer = (response.choices[0].message.content or "").strip()

        # 只列出答案裡真的用 [SOURCE N] 引用過的來源，不要把 rerank 選進來、
        # 但最後沒被引用的候選也列進去——不然使用者會看到一堆看似相關、
        # 實際上答案根本沒用到的檔名，反而混淆是哪份文件真正支持這個答案。
        cited_indices = {int(n) for n in re.findall(r"\[SOURCE (\d+)\]", answer)}
        cited_candidates = [c for i, c in enumerate(final_candidates, 1) if i in cited_indices] or final_candidates

        sources = []
        for c in cited_candidates:
            meta = c.metadata or {}
            source_text = meta.get("file_name", "未知文件")
            if meta.get("page_label"):
                source_text += f" (第 {meta['page_label']} 頁)"
            if source_text not in sources:
                sources.append(source_text)

        return {"answer": answer, "sources": sources, "retrieval_query": rewritten_query, "intent": intent}

    except Exception as e:
        print(f"查詢錯誤: {e}")
        return {"answer": "系統在檢索法規與回答時發生錯誤，請稍後再試。", "sources": []}


if __name__ == "__main__":
    result = query_academic_knowledge("資工系英文畢業門檻")
    print(f"\n[Academic Agent 回答]:\n{result['answer']}")
