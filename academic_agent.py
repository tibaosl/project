import os
import io
import re
import json
import shutil
import base64
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pdf2image import convert_from_path
from openai import OpenAI
import pdfplumber

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    load_index_from_storage,
    Settings,
    Document,
    QueryBundle,
)
from llama_index.core.schema import NodeWithScore
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from llama_index.core.node_parser import SentenceWindowNodeParser, SentenceSplitter
from llama_index.core.postprocessor import MetadataReplacementPostProcessor, LLMRerank
from llama_index.readers.file import DocxReader
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.prompts import PromptTemplate
from llama_index.embeddings.openai import OpenAIEmbedding

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

Settings.embed_model = OpenAIEmbedding(
    model="text-embedding-3-small",
    dimensions=1536,
)

openai_client = OpenAI()

CACHE_MD_DIR = "./parsed_markdown_cache"
PERSIST_DIR = "./storage"
DATA_DIR = "data"
POPPLER_PATH = os.getenv("POPPLER_PATH", None)

VECTOR_TOP_K = int(os.getenv("VECTOR_TOP_K", "15"))
LEXICAL_TOP_K = int(os.getenv("LEXICAL_TOP_K", "15"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))
RERANK_BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "3"))
RERANK_MAX_CHARS = int(os.getenv("RERANK_MAX_CHARS", "1800"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "12000"))
SCOPE_FALLBACK_UNKNOWN = os.getenv("SCOPE_FALLBACK_UNKNOWN", "1") == "1"
SCOPE_UNKNOWN_PENALTY = float(os.getenv("SCOPE_UNKNOWN_PENALTY", "0.15"))

# V3.3 Intent-First Retrieval
FAQ_BOOST = float(os.getenv("FAQ_BOOST", "0.30"))
FAQ_ONLY_IF_MATCH = os.getenv("FAQ_ONLY_IF_MATCH", "1") == "1"
FAQ_FILENAME_PATTERNS = ("常見問題", "faq", "問答", "q&a", "qa", "常見問答")

# 問題意圖關鍵詞：先用 deterministic rules，避免每次都讓 LLM 決定 scope。
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


# ============================================================
# V3.1 Conservative Academic Entity / Scope Resolution
# ============================================================
#
# 原則：
# 1. 不讓 LLM 自己猜「系所 → 學院」。
# 2. mapping 必須是明確、可審核的資料。
# 3. mapping 只用來擴展 retrieval，不直接證明法規適用。
# 4. 最終是否能把「學院規定」套到「系所」，仍要由文件 scope 判斷。
#
# 可以在環境變數 ACADEMIC_MAPPING_FILE 指定 JSON。
# 若沒有外部 mapping，程式仍可正常運作，但不會自行推導 hierarchy。
_BASE_DIR = Path(__file__).resolve().parent
_ACADEMIC_MAPPING_ENV = os.getenv("ACADEMIC_MAPPING_FILE", "academic_hierarchy.json")
ACADEMIC_MAPPING_FILE = str(Path(_ACADEMIC_MAPPING_ENV) if Path(_ACADEMIC_MAPPING_ENV).is_absolute() else (_BASE_DIR / _ACADEMIC_MAPPING_ENV))

ACADEMIC_MAPPING_SCHEMA_VERSION = "1"

# 只允許人工/官方確認後放入的 mapping。
# 預設故意留空，避免模型或程式自行猜測。
DEFAULT_ACADEMIC_HIERARCHY = {
    "schema_version": ACADEMIC_MAPPING_SCHEMA_VERSION,
    "departments": {},
}


def load_academic_hierarchy():
    """載入可審核的系所 → 學院 mapping；失敗時安全退回空 mapping。"""
    if not os.path.exists(ACADEMIC_MAPPING_FILE):
        print(f"[Academic Agent] academic mapping 不存在：{ACADEMIC_MAPPING_FILE}")
        return DEFAULT_ACADEMIC_HIERARCHY.copy()

    try:
        with open(ACADEMIC_MAPPING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("mapping 必須是 JSON object")

        if data.get("schema_version") != ACADEMIC_MAPPING_SCHEMA_VERSION:
            raise ValueError("mapping schema version 不相容")

        departments = data.get("departments", {})
        if not isinstance(departments, dict):
            raise ValueError("departments 必須是 object")

        # 僅保留格式正確的 entry。
        clean = {
            "schema_version": ACADEMIC_MAPPING_SCHEMA_VERSION,
            "departments": {},
        }

        for canonical, entry in departments.items():
            if not isinstance(canonical, str) or not canonical.strip():
                continue
            if not isinstance(entry, dict):
                continue

            college = entry.get("college", "")
            aliases = entry.get("aliases", [])

            if college and not isinstance(college, str):
                continue
            if not isinstance(aliases, list):
                aliases = []

            clean["departments"][canonical] = {
                "college": college.strip(),
                "aliases": [
                    str(x).strip()
                    for x in aliases
                    if str(x).strip()
                ],
            }

        print(f"[Academic Agent] academic mapping loaded: {len(clean['departments'])} departments")
        return clean

    except Exception as e:
        print(f"[Academic Agent] academic mapping 載入失敗，停用 hierarchy expansion: {e}")
        return DEFAULT_ACADEMIC_HIERARCHY.copy()


def resolve_academic_entities(query: str, hierarchy: dict):
    """只做人工 mapping 的 exact/alias match，不讓 LLM 猜 hierarchy。"""
    query_norm = normalize_for_search(query)
    matches = []
    for canonical, entry in hierarchy.get("departments", {}).items():
        names = [canonical] + entry.get("aliases", [])
        for name in sorted(names, key=len, reverse=True):
            name_norm = normalize_for_search(name)
            if name_norm and name_norm in query_norm:
                matches.append({
                    "department": canonical,
                    "matched_alias": name,
                    "college": entry.get("college", ""),
                })
                break
    unique = {}
    for item in matches:
        unique[item["department"]] = item
    return list(unique.values())


def detect_query_intent(query: str):
    """
    V3.3：先判斷問題型態，再決定是否啟用 academic scope。

    FAQ 類問題即使提到多個系所，也不能把所有提及的系所當成使用者的
    scope。這是為了處理「我是電機的，如果我修了資管的演算法...」這類跨系 FAQ。
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

    return {
        "intent": intent,
        "faq_hits": faq_hits,
        "academic_hits": academic_hits,
    }


def _extract_role_aware_entities(query: str, hierarchy: dict):
    """
    將命中的系所分成 user_department / referenced_department / target_department。
    只有 target/user scope 才有資格進入 academic scope filter。
    referenced/course department 絕不能單獨改變 scope。
    """
    entities = resolve_academic_entities(query, hierarchy)
    q = normalize_for_search(query)
    results = []

    for e in entities:
        canonical = e["department"]
        names = [canonical, e.get("matched_alias", "")]
        name_pattern = max((normalize_for_search(x) for x in names if x), key=len, default="")
        before = q[:q.find(name_pattern)] if name_pattern and name_pattern in q else q

        if any(x in before[-12:] for x in ("我是", "我為", "我在", "本系", "我的系")):
            role = "user_department"
        elif any(x in q[max(0, q.find(name_pattern)-8):q.find(name_pattern)+len(name_pattern)+8] for x in ("的演算法", "的課程", "的課", "別系", "他系")):
            role = "referenced_department"
        else:
            role = "target_department"

        item = dict(e)
        item["role"] = role
        results.append(item)

    return results


def build_faq_retrieval_queries(user_query: str, rewritten_query: str):
    queries = []
    for q in (user_query, rewritten_query):
        if q and q.strip() and q.strip() not in queries:
            queries.append(q.strip())
    extras = [
        f"常見問題 {user_query}",
        f"常見問答 {user_query}",
        f"FAQ {user_query}",
    ]
    for q in extras:
        if q not in queries:
            queries.append(q)
    return queries


def is_faq_metadata(metadata: dict):
    meta = metadata or {}
    doc_type = str(meta.get("document_type", "")).strip().lower()
    if doc_type == "faq":
        return True
    file_name = str(meta.get("file_name", "")).lower()
    return any(p.lower() in file_name for p in FAQ_FILENAME_PATTERNS)


def filter_faq_candidates(candidates):
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
    queries = []
    for q in (user_query, rewritten_query):
        if q and q.strip() and q.strip() not in queries:
            queries.append(q.strip())

    entities = _extract_role_aware_entities(user_query, hierarchy)
    scope_entities = [e for e in entities if e.get("role") in {"user_department", "target_department"}]
    for entity in scope_entities:
        department = entity["department"]
        college = entity["college"]
        variants = [
            f"{department} {user_query}",
            f"{department} {rewritten_query}",
        ]
        if college:
            variants.extend([
                f"{college} {user_query}",
                f"{college} {rewritten_query}",
                f"{department} {college} {user_query}",
            ])
        for variant in variants:
            if variant.strip() and variant.strip() not in queries:
                queries.append(variant.strip())
    return queries, entities


def _known_colleges_from_hierarchy(hierarchy: dict):
    return sorted({
        str(entry.get("college", "")).strip()
        for entry in hierarchy.get("departments", {}).values()
        if isinstance(entry, dict) and str(entry.get("college", "")).strip()
    }, key=len, reverse=True)


def _known_departments_from_hierarchy(hierarchy: dict):
    names = []
    for canonical, entry in hierarchy.get("departments", {}).items():
        names.append(canonical)
        if isinstance(entry, dict):
            names.extend(entry.get("aliases", []))
    return sorted({str(x).strip() for x in names if str(x).strip()}, key=len, reverse=True)


def detect_document_scope(node):
    """只讀 ingestion 已寫入的 scope metadata；不從正文猜 applicability。"""
    metadata = _node_metadata(node)
    scope = str(metadata.get("document_scope") or metadata.get("scope") or "").strip().lower()
    academic_unit = str(
        metadata.get("academic_unit") or metadata.get("college") or ""
    ).strip()
    department = str(
        metadata.get("department") or metadata.get("department_name") or ""
    ).strip()
    university_wide = bool(metadata.get("university_wide", False))
    return {
        "scope": scope,
        "academic_unit": academic_unit,
        "department": department,
        "university_wide": university_wide,
    }


def _scope_rank(candidate, entities):
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

    # 已知 metadata 指向其他系/院：hard reject。
    if info["department"] or info["academic_unit"]:
        return "mismatch", -1.0

    return "unknown", SCOPE_UNKNOWN_PENALTY


def validate_candidate_scope(candidate, entities):
    match, _ = _scope_rank(candidate, entities)
    return match


def annotate_and_filter_by_scope(candidates, entities):
    """Scope-first：先排除明確錯誤 scope，再讓 hybrid/reranker 排相關性。"""
    if not entities:
        for c in candidates:
            c.scope_match = "unknown"
            c.scope_score = 0.0
        return candidates

    accepted = []
    unknown = []
    rejected = 0

    for c in candidates:
        match, score = _scope_rank(c, entities)
        c.scope_match = match
        c.scope_score = score

        if match == "mismatch":
            rejected += 1
            continue
        if match == "unknown":
            unknown.append(c)
        else:
            accepted.append(c)

    # 有明確 applicability 時，unknown 不污染主候選池。
    if accepted:
        accepted.sort(key=lambda c: (c.scope_score, c.fused_score), reverse=True)
        print(
            f"[Academic Agent] Scope filter：保留 {len(accepted)}，"
            f"排除 {rejected} 個明確不適用文件"
        )
        return accepted

    if SCOPE_FALLBACK_UNKNOWN and unknown:
        unknown.sort(key=lambda c: c.fused_score, reverse=True)
        print(
            f"[Academic Agent] Scope filter：沒有明確適用文件，"
            f"fallback unknown={len(unknown)}，排除 {rejected}"
        )
        return unknown

    print(f"[Academic Agent] Scope filter：沒有可接受文件，排除 {rejected}")
    return []


def clean_markdown_output(text: str) -> str:
    """清掉 OCR 模型偶爾產生的包裝語句，但不改動正文。"""
    if not text:
        return ""
    text = re.sub(r"^```markdown\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"^(I'm unable to|However, I can|Please adjust|Here's a|Here is|這是一份).*?\n+",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(
        r"\n+(Please adjust|Hope this helps|如需修改|希望這對您有幫助).*?$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return text.strip()


def normalize_for_search(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s+", "", text)
    return text


def search_terms(text: str):
    """
    不依賴 jieba 的輕量 lexical retrieval：
    - 中文：使用 2-gram
    - 英文/數字：保留完整 token
    - 保留原始詞組做 exact phrase bonus
    """
    raw = normalize_for_search(text)
    cjk = re.findall(r"[\u3400-\u9fff]", raw)
    bigrams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    latin = set(re.findall(r"[a-z0-9][a-z0-9._/-]*", raw))
    return raw, bigrams | latin


def lexical_score(query: str, text: str, file_name: str = "") -> float:
    q_raw, q_terms = search_terms(query)
    if not q_raw:
        return 0.0

    t_raw = normalize_for_search(text)
    f_raw = normalize_for_search(file_name)

    score = 0.0

    if q_raw in t_raw:
        score += 12.0

    if q_raw in f_raw:
        score += 10.0

    if q_terms:
        hits = sum(1 for term in q_terms if term in t_raw)
        score += 2.0 * hits
        score += 0.5 * hits / max(len(q_terms), 1)

    return score


def _node_text(node) -> str:
    """集中處理目前 LlamaIndex node 的文字 API。"""
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
    """LlamaIndex adapter：NodeWithScore -> node；TextNode 原樣返回。"""
    if item is None:
        return None
    inner = getattr(item, "node", None)
    return inner if inner is not None else item


def get_all_index_nodes(index):
    docs = getattr(index.docstore, "docs", {})
    return list(docs.values())


@dataclass
class RetrievalCandidate:
    """Application-level candidate；不讓 LlamaIndex wrapper 穿透 pipeline。"""
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
        node_id=_node_id(node),
        text=_node_text(node),
        metadata=_node_metadata(node),
        dense_score=float(dense_score or 0.0),
        lexical_score=float(lexical_score or 0.0),
        sources=(source,) if source else tuple(),
        node=node,
    )


def lexical_retrieve(index, query: str, top_k: int = LEXICAL_TOP_K):
    scored = []
    for node in get_all_index_nodes(index):
        text = _node_text(node)
        file_name = _node_metadata(node).get("file_name", "")
        score = lexical_score(query, text, file_name)
        if score > 0:
            candidate = _candidate_from_node(node, lexical_score=score, source="lexical")
            if candidate:
                scored.append(candidate)
    scored.sort(key=lambda x: x.lexical_score, reverse=True)
    return scored[:top_k]


def _normalize_scores(candidates, attr_name):
    values = [float(getattr(c, attr_name, 0.0) or 0.0) for c in candidates]
    if not values:
        return
    lo, hi = min(values), max(values)
    if hi <= lo:
        value = 1.0 if hi > 0 else 0.0
        for c in candidates:
            setattr(c, attr_name, value)
        return
    for c in candidates:
        raw = float(getattr(c, attr_name, 0.0) or 0.0)
        setattr(c, attr_name, (raw - lo) / (hi - lo))


def fuse_candidates(dense_candidates, lexical_candidates, dense_weight=0.65, lexical_weight=0.35):
    merged = {}
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


def prepare_nodes_for_rerank(candidates, max_chars=RERANK_MAX_CHARS):
    """唯一建立 NodeWithScore 的 application -> LlamaIndex adapter。"""
    from llama_index.core.schema import TextNode
    prepared = []
    for candidate in candidates:
        text = candidate.text or ""
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[內容已截斷，僅供 rerank 判斷]"
        node = TextNode(
            text=text,
            metadata=dict(candidate.metadata or {}),
            id_=candidate.node_id,
        )
        prepared.append(NodeWithScore(node=node, score=float(candidate.fused_score)))
    return prepared


def candidates_from_reranked(reranked_nodes, original_candidates):
    by_id = {c.node_id: c for c in original_candidates}
    output = []
    for item in reranked_nodes:
        node = _unwrap_retrieval_item(item)
        if node is None:
            continue
        candidate = by_id.get(_node_id(node))
        if candidate is None:
            candidate = _candidate_from_node(node, source="rerank")
        if candidate:
            candidate.node = node
            output.append(candidate)
    return output

def build_source_context(nodes):
    """
    把 metadata 明確寫進 context，避免模型只看到內容卻不知道來源。
    """
    blocks = []
    for i, node in enumerate(nodes, 1):
        meta = getattr(node, "metadata", {})
        file_name = meta.get("file_name", "未知文件")
        page = meta.get("page_label", "")
        source = f"{file_name}"
        if page:
            source += f"｜第 {page} 頁"

        text = getattr(node, "text", "") or ""
        blocks.append(f"[SOURCE {i}]\n來源：{source}\n內容：\n{text}")

    context = "\n\n====================\n\n".join(blocks)
    if len(context) <= MAX_CONTEXT_CHARS:
        return context

    kept = []
    size = 0
    for block in blocks:
        if size + len(block) > MAX_CONTEXT_CHARS:
            break
        kept.append(block)
        size += len(block)

    return "\n\n====================\n\n".join(kept)


def has_tables_in_pdf(pdf_path: str) -> bool:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        if not table:
                            continue
                        total_cells = 0
                        total_text_length = 0
                        for row in table:
                            for cell in row:
                                if cell and cell.strip():
                                    total_cells += 1
                                    total_text_length += len(cell.strip())

                        if total_cells > 0:
                            avg_cell_length = total_text_length / total_cells
                            if avg_cell_length < 35 and total_cells >= 4:
                                return True
    except Exception as e:
        print(f"[表格偵測警告] {os.path.basename(pdf_path)}: {e}")
        return True
    return False


def extract_plain_text_pages_from_pdf(pdf_path: str):
    """每頁獨立成 Document，避免整份 PDF 被當成單一長文件。"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((page_no, text.strip()))
    return pages


def convert_pdf_to_markdown_pages_via_vision(pdf_path: str):
    """
    Vision OCR 仍逐頁做，但 cache 改成 JSON，保留 page number。
    舊的 .md cache 若存在，會被忽略一次並重新建立 page-aware cache。
    """
    filename = os.path.basename(pdf_path)
    cache_path = os.path.join(CACHE_MD_DIR, f"{filename}.pages.json")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return [(int(x["page"]), x["text"]) for x in cached]

    print(f"  [Vision OCR] 正在處理 {filename}...")
    images = convert_from_path(pdf_path, dpi=200, poppler_path=POPPLER_PATH)
    total_pages = len(images)
    page_results = []
    previous_page_snippet = ""

    for i, img in enumerate(images, 1):
        print(f"    --> OCR 第 {i} / {total_pages} 頁...")

        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        user_prompt = (
            "你是一個極度精準的文件 OCR 引擎。請將圖片中的內容忠實轉錄為 Markdown。\n\n"
            "規則：\n"
            "1. 表格必須保留原本欄列結構，不得自行合併欄位。\n"
            "2. 一般條文、Q&A、條列內容維持一般 Markdown，不要硬轉成表格。\n"
            "3. 文字必須完整，不摘要、不補寫、不猜測看不清楚的內容。\n"
            "4. 空白填寫欄位請保留。\n"
            "5. 不要輸出 ```markdown 包裝，不要輸出開場白或結尾說明。\n"
        )

        if previous_page_snippet:
            user_prompt += (
                "\n上一頁結尾僅供跨頁辨識參考；不要把上一頁內容重複輸出：\n"
                f"{previous_page_snippet}\n"
            )

        response = openai_client.chat.completions.create(
            model=os.getenv("RAG_OCR_MODEL", "gpt-4o"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是文件 OCR 與表格轉錄引擎。"
                        "只輸出圖片中實際存在的文字與結構，不得自行補充。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_base64}",
                                "detail": "high",
                            },
                        },
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
        json.dump(
            [{"page": page, "text": text} for page, text in page_results],
            f,
            ensure_ascii=False,
            indent=2,
        )

    return page_results


def enrich_document_scope_metadata(metadata, file_name, hierarchy):
    """由可審核 mapping + 明確檔名訊號建立 deterministic scope metadata。
    不用 LLM 推論；無法確認就保持 unknown。
    """
    meta = dict(metadata or {})
    fname = str(file_name or "")
    norm = normalize_for_search(fname)

    if any(p.lower() in fname.lower() for p in FAQ_FILENAME_PATTERNS):
        meta["document_type"] = "faq"
    else:
        meta.setdefault("document_type", "formal_policy")
    colleges = _known_colleges_from_hierarchy(hierarchy)
    departments = _known_departments_from_hierarchy(hierarchy)

    for department in departments:
        if normalize_for_search(department) in norm:
            meta["document_scope"] = "department"
            for canonical, entry in hierarchy.get("departments", {}).items():
                if department == canonical or department in entry.get("aliases", []):
                    meta["department"] = canonical
                    meta["academic_unit"] = entry.get("college", "")
                    break
            meta["university_wide"] = False
            return meta

    for college in colleges:
        if normalize_for_search(college) in norm:
            meta["document_scope"] = "college"
            meta["academic_unit"] = college
            meta["university_wide"] = False
            return meta

    # 已知全校文件優先於一般未知文件。
    if ("全校" in fname) or ("國立中央大學" in fname and "大學部" in fname):
        meta["document_scope"] = "university"
        meta["university_wide"] = True
        return meta

    meta.setdefault("document_scope", "unknown")
    meta.setdefault("university_wide", False)
    return meta


def load_documents():
    documents = []
    docx_parser = DocxReader()
    hierarchy = load_academic_hierarchy()

    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            file_path = os.path.join(root, file)

            if file.startswith(".") or file.startswith("~$"):
                continue

            if os.path.getsize(file_path) == 0:
                continue

            try:
                if file.lower().endswith(".pdf"):
                    if has_tables_in_pdf(file_path):
                        pages = convert_pdf_to_markdown_pages_via_vision(file_path)
                    else:
                        pages = extract_plain_text_pages_from_pdf(file_path)

                    for page_no, page_text in pages:
                        if not page_text.strip():
                            continue
                        documents.append(
                            Document(
                                text=page_text,
                                metadata=enrich_document_scope_metadata(
                                    {
                                        "file_name": file,
                                        "file_path": file_path,
                                        "page_label": str(page_no),
                                        "source_type": "pdf",
                                    },
                                    file,
                                    hierarchy,
                                ),
                            )
                        )

                elif file.lower().endswith(".docx"):
                    docx_docs = docx_parser.load_data(file_path)
                    for docx_doc in docx_docs:
                        docx_doc.metadata = enrich_document_scope_metadata(
                            {
                                **dict(docx_doc.metadata or {}),
                                "file_name": file,
                                "file_path": file_path,
                                "source_type": "docx",
                            },
                            file,
                            hierarchy,
                        )
                        documents.append(docx_doc)

            except Exception as e:
                print(f"[檔案讀取失敗] {file}: {e}")

    return documents


def get_latest_data_mtime() -> float:
    latest_mtime = 0.0
    if not os.path.exists(DATA_DIR):
        return latest_mtime

    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            if file.startswith(".") or file.startswith("~$"):
                continue
            file_path = os.path.join(root, file)
            latest_mtime = max(latest_mtime, os.path.getmtime(file_path))

    return latest_mtime


def get_or_create_index():
    mtime_file = os.path.join(PERSIST_DIR, ".data_mtime")
    schema_file = os.path.join(PERSIST_DIR, ".rag_schema_version")

    current_latest_mtime = get_latest_data_mtime()
    need_rebuild = False

    if not os.path.exists(PERSIST_DIR):
        need_rebuild = True

    elif os.path.exists(mtime_file):
        with open(mtime_file, "r", encoding="utf-8") as f:
            saved_mtime = float(f.read().strip())

        if current_latest_mtime > saved_mtime:
            need_rebuild = True

    else:
        need_rebuild = True

    # Schema version changed → rebuild
    CURRENT_SCHEMA_VERSION = "8-v3.3-intent-first-faq-retrieval"

    if not os.path.exists(schema_file):
        need_rebuild = True
    else:
        with open(schema_file, "r", encoding="utf-8") as f:
            saved_schema = f.read().strip()

        if saved_schema != CURRENT_SCHEMA_VERSION:
            need_rebuild = True

    if need_rebuild:

        if os.path.exists(PERSIST_DIR):
            shutil.rmtree(PERSIST_DIR)

        print("[Academic Agent] 正在建立 page-aware index...")

        documents = load_documents()

        # --------------------------------------------------
        # 1. 先把每個 page Document 切成小 chunk
        # --------------------------------------------------

        splitter = SentenceSplitter(
            chunk_size=500,
            chunk_overlap=50,
        )

        chunked_nodes = splitter.get_nodes_from_documents(documents)

        print(
            f"[Academic Agent] SentenceSplitter: "
            f"{len(documents)} documents → "
            f"{len(chunked_nodes)} chunks"
        )

        # --------------------------------------------------
        # 2. 再建立 Sentence Window
        # --------------------------------------------------

        node_parser = SentenceWindowNodeParser.from_defaults(
            window_size=2,
            window_metadata_key="window",
            original_text_metadata_key="original_text",
        )

        final_nodes = node_parser.get_nodes_from_documents(
            chunked_nodes
        )

        print(
            f"[Academic Agent] SentenceWindow: "
            f"{len(final_nodes)} nodes"
        )

        # --------------------------------------------------
        # 3. 安全檢查
        # --------------------------------------------------

        max_chars = max(
            len(getattr(node, "text", "") or "")
            for node in final_nodes
        )

        print(
            f"[Academic Agent] 最大 node text 長度: "
            f"{max_chars} chars"
        )

        # --------------------------------------------------
        # 4. 只建立一次 VectorStoreIndex
        # --------------------------------------------------

        index = VectorStoreIndex(final_nodes)

        # --------------------------------------------------
        # 5. Persist
        # --------------------------------------------------

        index.storage_context.persist(
            persist_dir=PERSIST_DIR
        )

        with open(mtime_file, "w", encoding="utf-8") as f:
            f.write(str(current_latest_mtime))

        with open(schema_file, "w", encoding="utf-8") as f:
            f.write(CURRENT_SCHEMA_VERSION)

        print(
            f"[Academic Agent] Index 建立完成，"
            f"共 {len(final_nodes)} nodes。"
        )

    else:

        print("[Academic Agent] 載入既有 index...")

        storage_context = StorageContext.from_defaults(
            persist_dir=PERSIST_DIR
        )

        index = load_index_from_storage(
            storage_context
        )

    return index


def expand_query_for_retrieval(user_query: str, history_str: str = "") -> str:
    """
    只做「語意重寫」，禁止自行新增學院、規章年份、法規內容。
    這是為了避免 query expansion 本身把 retrieval 帶偏。
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
        response = llm_smart.chat(
            [
                ChatMessage(role=MessageRole.SYSTEM, content=prompt),
                ChatMessage(role=MessageRole.USER, content=user_query),
            ]
        )
        rewritten = response.message.content.strip()
        return rewritten or user_query
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
7. 不要自行推導系所隸屬學院、適用年度或資格條件；除非來源本身有明確寫出。
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
        print(f'\n[Academic Agent] 原始問題：「{query_str}」')

        intent_info = detect_query_intent(query_str)
        intent = intent_info["intent"]
        print(
            f"[Academic Agent] Query intent：{intent} "
            f"(faq_hits={intent_info['faq_hits']}, academic_hits={intent_info['academic_hits']})"
        )

        rewritten_query = expand_query_for_retrieval(query_str, history_str)
        print(f"[Academic Agent] Retrieval query：「{rewritten_query}」")

        hierarchy = load_academic_hierarchy()
        retrieval_queries, resolved_entities = build_retrieval_queries(
            query_str, rewritten_query, hierarchy
        )
        faq_queries = build_faq_retrieval_queries(query_str, rewritten_query)

        print(
            "[Academic Agent] 已驗證 academic entities："
            + json.dumps(resolved_entities, ensure_ascii=False)
            if resolved_entities else
            "[Academic Agent] 未命中已驗證 academic mapping，不自行推導 hierarchy"
        )

        # FAQ 問題：保留原始問題與 FAQ 擴張，但不啟用 academic scope。
        if intent == "faq":
            active_queries = faq_queries
        elif intent == "mixed":
            active_queries = list(dict.fromkeys(faq_queries + retrieval_queries))
        else:
            active_queries = retrieval_queries

        print("[Academic Agent] Retrieval variants：" + " | ".join(active_queries))

        retriever = index.as_retriever(similarity_top_k=VECTOR_TOP_K)
        dense_candidates = []
        for q in active_queries:
            try:
                results = retriever.retrieve(q)
            except Exception as e:
                print(f"[Academic Agent] Dense retrieval 失敗：{e}")
                results = []
            for item in results:
                candidate = _candidate_from_node(
                    item,
                    dense_score=getattr(item, "score", 0.0) or 0.0,
                    source="dense",
                )
                if candidate:
                    dense_candidates.append(candidate)

        dense_by_id = {}
        for c in dense_candidates:
            old = dense_by_id.get(c.node_id)
            if old is None:
                dense_by_id[c.node_id] = c
            else:
                old.dense_score = max(old.dense_score, c.dense_score)
                old.sources = tuple(sorted(set(old.sources + c.sources)))
        dense_candidates = list(dense_by_id.values())

        lexical_candidates = []
        for q in active_queries:
            for c in lexical_retrieve(index, q, LEXICAL_TOP_K):
                c.sources = tuple(sorted(set(c.sources + ("lexical",))))
                lexical_candidates.append(c)

        lexical_by_id = {}
        for c in lexical_candidates:
            old = lexical_by_id.get(c.node_id)
            if old is None:
                lexical_by_id[c.node_id] = c
            else:
                old.lexical_score = max(old.lexical_score, c.lexical_score)
                old.sources = tuple(sorted(set(old.sources + c.sources)))
        lexical_candidates = list(lexical_by_id.values())

        candidates = fuse_candidates(
            dense_candidates, lexical_candidates,
            dense_weight=0.65, lexical_weight=0.35,
        )
        print(
            f"[Academic Agent] V3.3 混合檢索候選：{len(candidates)} "
            f"(dense={len(dense_candidates)}, lexical={len(lexical_candidates)})"
        )

        # ----------------------------------------------------------
        # Intent-first policy
        # ----------------------------------------------------------
        if intent == "faq":
            faq_candidates = filter_faq_candidates(candidates)
            if faq_candidates:
                candidates = faq_candidates
                print(
                    f"[Academic Agent] FAQ-first：命中 {len(candidates)} 個 FAQ 文件，"
                    "停用 academic scope filter"
                )
            elif FAQ_ONLY_IF_MATCH:
                print("[Academic Agent] FAQ-first：沒有 FAQ 文件命中，fallback 一般 retrieval")
                for c in candidates:
                    c.faq_match = False
        elif intent == "mixed":
            # mixed 問題不做 hard scope；FAQ 有 boost，正式文件仍可進候選。
            faq_candidates = filter_faq_candidates(candidates)
            print(
                f"[Academic Agent] Mixed intent：FAQ candidates={len(faq_candidates)}；"
                "不對提及的系所套用 hard academic scope"
            )
        else:
            # 只有 academic intent 才使用 hierarchy scope。
            if intent == "academic":
                candidates = annotate_and_filter_by_scope(
                    candidates,
                    [e for e in resolved_entities if e.get("role") in {"user_department", "target_department"}],
                )
            else:
                for c in candidates:
                    c.scope_match = "unknown"
                    c.scope_score = 0.0

        if not candidates:
            return {
                "answer": "目前檢索到的文件不足以確認這個問題的具體答案。",
                "sources": [],
                "retrieval_query": rewritten_query,
            }

        # 排序：scope score 只有 academic intent 才有意義；FAQ boost 已寫入 fused_score。
        candidates.sort(
            key=lambda c: (
                1 if getattr(c, "faq_match", False) else 0,
                getattr(c, "scope_score", 0.0) if intent == "academic" else 0.0,
                c.fused_score,
            ),
            reverse=True,
        )

        rerank_pool = candidates[:max(VECTOR_TOP_K, LEXICAL_TOP_K)]
        for c in rerank_pool:
            c.metadata = dict(c.metadata or {})
            c.metadata["applicability_evidence"] = getattr(c, "scope_match", "unknown")
            c.metadata["retrieval_intent"] = intent
            c.metadata["faq_match"] = bool(getattr(c, "faq_match", False))

        rerank_input = prepare_nodes_for_rerank(
            rerank_pool, max_chars=RERANK_MAX_CHARS
        )
        reranker = LLMRerank(
            choice_batch_size=RERANK_BATCH_SIZE,
            top_n=RERANK_TOP_N,
            llm=llm_smart,
        )
        reranked_nodes = reranker.postprocess_nodes(
            rerank_input,
            query_bundle=QueryBundle(query_str),
        )

        final_candidates = candidates_from_reranked(reranked_nodes, rerank_pool)
        if not final_candidates:
            final_candidates = rerank_pool[:RERANK_TOP_N]

        window_processor = MetadataReplacementPostProcessor(
            target_metadata_key="window"
        )
        window_input = [
            NodeWithScore(node=c.node, score=float(c.fused_score))
            for c in final_candidates if c.node is not None
        ]
        try:
            window_nodes = window_processor.postprocess_nodes(window_input)
        except Exception as e:
            print(f"[Academic Agent] Sentence Window 展開失敗，退回 rerank node：{e}")
            window_nodes = window_input

        final_nodes = [
            _unwrap_retrieval_item(item)
            for item in window_nodes
            if _unwrap_retrieval_item(item) is not None
        ]
        context_str = build_source_context(final_nodes)

        retrieval_notes = [f"intent = {intent}"]
        if resolved_entities:
            retrieval_notes.append(
                "entities = " + json.dumps(resolved_entities, ensure_ascii=False)
            )
        retrieval_notes.append(
            "FAQ documents are prioritized for FAQ intent; mentioned departments are not automatically treated as applicability scope."
        )
        context_str += "\n\n[Retrieval notes]\n" + "\n".join(retrieval_notes)

        user_prompt = f"""
【檢索來源】
{context_str}

【檢索意圖】
{intent}

【已驗證 academic entities】
{json.dumps(resolved_entities, ensure_ascii=False)}
注意：entity role 很重要。user_department / target_department 才可能形成 academic scope；referenced_department / course_department 只是問題中被提及的對象，不能單獨限制檢索範圍。

【使用者問題】
{query_str}

請直接回答問題。若來源不足，明確說明不足之處，不要自行補答案。
"""

        response = openai_client.chat.completions.create(
            model=os.getenv("RAG_ANSWER_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=1200,
        )
        answer = (response.choices[0].message.content or "").strip()

        sources = []
        for node in final_nodes:
            meta = getattr(node, "metadata", {})
            file_name = meta.get("file_name", "未知文件")
            page_label = meta.get("page_label", "")
            source_text = file_name
            if page_label:
                source_text += f" (第 {page_label} 頁)"
            if source_text not in sources:
                sources.append(source_text)

        return {
            "answer": answer,
            "sources": sources,
            "retrieval_query": rewritten_query,
            "intent": intent,
            "resolved_entities": resolved_entities,
        }

    except Exception as e:
        print(f"\n[Academic Agent] 查詢錯誤: {e}")
        return {
            "answer": "系統在檢索法規與回答時發生錯誤，請稍後再試。",
            "sources": [],
        }


if __name__ == "__main__":
    test_query = "資工系英文畢業門檻"
    result = query_academic_knowledge(test_query)
    print(f"\n[Academic Agent 回答]:\n{result['answer']}")
    print(f"\n[來源]:\n{result['sources']}")