import os
import io
import re
import shutil
import base64
import math
from collections import Counter, defaultdict

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
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from llama_index.core.node_parser import SentenceWindowNodeParser
from llama_index.core.postprocessor import MetadataReplacementPostProcessor, LLMRerank
from llama_index.readers.file import DocxReader
from llama_index.core.llms import ChatMessage, MessageRole

load_dotenv()

Settings.llm = LlamaOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=800,
    presence_penalty=0.5,
    frequency_penalty=0.5,
)

llm_smart = LlamaOpenAI(
    model="gpt-4o",
    temperature=0,
    max_tokens=1000,
    presence_penalty=0.5,
    frequency_penalty=0.5,
)
openai_client = OpenAI()

CACHE_MD_DIR = "./parsed_markdown_cache"
PERSIST_DIR = "./storage"
DATA_DIR = "data"
POPPLER_PATH = os.getenv("POPPLER_PATH", None)

# -----------------------------------------------------------------------------
# RAGFlow-style retrieval parameters
# -----------------------------------------------------------------------------
DENSE_TOP_K = 48
LEXICAL_TOP_K = 48
CANDIDATE_TOP_K = 48
RERANK_TOP_N = 8
RERANK_BATCH_SIZE = 8

# Hybrid ranking. Reranker is the main semantic judge; lexical retrieval is
# deliberately kept as a recall channel rather than a hard gate.
VECTOR_WEIGHT = 0.55
LEXICAL_WEIGHT = 0.25
RERANK_WEIGHT = 0.20

# Academic metadata is only a soft ranking signal.
ACADEMIC_SCOPE_BOOST = {
    "direct": 0.15,
    "college": 0.08,
    "university": 0.03,
    "unknown": 0.00,
    "mismatch": -0.10,
}
FAQ_BOOST = 0.05

# Final evidence gate. This is intentionally conservative because precision
# belongs at the answer boundary, not in the initial retrieval stage.
MIN_FINAL_RERANK_SCORE = 0.20
MIN_EVIDENCE_CHARS = 18

STOPWORDS = set("""
的 了 和 與 及 或 是 在 有 要 我 你 他 她 它 這 那 哪 什麼 怎麼 如何 可以 能 會
請 問 一下 一個 有關 關於 是否 為 之 中 後 前 上 下 從 到 對 依 按 其 以及
the a an and or is are of to in for on with by from what how can could should
""".split())


def clean_markdown_output(text: str) -> str:
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


def has_tables_in_pdf(pdf_path: str) -> bool:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables or []:
                    if not table:
                        continue
                    cells = [cell.strip() for row in table for cell in row if cell and cell.strip()]
                    if len(cells) >= 4 and (sum(map(len, cells)) / len(cells)) < 35:
                        return True
    except Exception as e:
        print(f"[表格偵測警告] {os.path.basename(pdf_path)}: {e}")
        return True
    return False


def extract_plain_text_from_pdf(pdf_path: str) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        print(f"[純文字抽取失敗] {os.path.basename(pdf_path)}: {e}")
        return ""


def convert_pdf_to_markdown_via_vision(pdf_path: str) -> str:
    filename = os.path.basename(pdf_path)
    cache_path = os.path.join(CACHE_MD_DIR, f"{filename}.md")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"  [Vision OCR] {filename}")
    images = convert_from_path(pdf_path, dpi=200, poppler_path=POPPLER_PATH)
    pages = []
    previous_page_snippet = ""

    for i, img in enumerate(images):
        print(f"    --> 第 {i + 1}/{len(images)} 頁")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        prompt = (
            "你是中央大學校務文件 OCR 引擎。請忠實將圖片內容轉成 Markdown。"
            "表格必須完整保留欄列與文字；一般條文使用標題與條列；不要摘要、不要補寫圖片沒有的內容。"
            "只輸出文件內容，不要輸出解釋。"
        )
        if previous_page_snippet:
            prompt += f"\n上一頁結尾（僅供銜接）：\n{previous_page_snippet}\n"

        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "專業文件 OCR 與表格轉錄引擎。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}", "detail": "high"}},
                    ],
                },
            ],
            temperature=0,
            max_tokens=4000,
        )
        page_text = clean_markdown_output(response.choices[0].message.content)
        pages.append(page_text)
        previous_page_snippet = page_text[-400:]

    full_text = "\n\n---\n\n".join(pages)
    os.makedirs(CACHE_MD_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(full_text)
    return full_text


def load_documents():
    documents = []
    docx_parser = DocxReader()

    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            if file.startswith(".") or file.startswith("~$"):
                continue
            file_path = os.path.join(root, file)
            if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
                continue

            try:
                if file.lower().endswith(".pdf"):
                    md_cache_path = os.path.join(CACHE_MD_DIR, f"{file}.md")
                    if os.path.exists(md_cache_path):
                        with open(md_cache_path, "r", encoding="utf-8") as f:
                            text = f.read()
                    elif has_tables_in_pdf(file_path):
                        text = convert_pdf_to_markdown_via_vision(file_path)
                    else:
                        text = extract_plain_text_from_pdf(file_path)
                    if text:
                        documents.append(Document(text=text, metadata={"file_name": file, "file_path": file_path}))

                elif file.lower().endswith(".docx"):
                    for doc in docx_parser.load_data(file_path):
                        doc.metadata["file_name"] = file
                        documents.append(doc)
            except Exception as e:
                print(f"[文件跳過] {file}: {e}")

    return documents


def get_latest_data_mtime() -> float:
    latest = 0.0
    if not os.path.exists(DATA_DIR):
        return latest
    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            if file.startswith(".") or file.startswith("~$"):
                continue
            path = os.path.join(root, file)
            if os.path.isfile(path):
                latest = max(latest, os.path.getmtime(path))
    return latest


def get_or_create_index():
    mtime_file = os.path.join(PERSIST_DIR, ".data_mtime")
    current = get_latest_data_mtime()
    rebuild = not os.path.exists(PERSIST_DIR)

    if not rebuild:
        if not os.path.exists(mtime_file):
            rebuild = True
        else:
            try:
                rebuild = current > float(open(mtime_file, "r", encoding="utf-8").read().strip())
            except Exception:
                rebuild = True

    if rebuild:
        if os.path.exists(PERSIST_DIR):
            shutil.rmtree(PERSIST_DIR)
        print("\n[Academic Agent V4] 建立 SentenceWindow index...")
        documents = load_documents()
        node_parser = SentenceWindowNodeParser.from_defaults(
            window_size=3,
            window_metadata_key="window",
            original_text_metadata_key="original_text",
        )
        nodes = node_parser.get_nodes_from_documents(documents)
        index = VectorStoreIndex(nodes)
        index.storage_context.persist(persist_dir=PERSIST_DIR)
        os.makedirs(PERSIST_DIR, exist_ok=True)
        with open(mtime_file, "w", encoding="utf-8") as f:
            f.write(str(current))
        print(f"[Academic Agent V4] Index 建立完成：{len(nodes)} nodes")
    else:
        storage_context = StorageContext.from_defaults(persist_dir=PERSIST_DIR)
        index = load_index_from_storage(storage_context)

    return index


def tokenize(text: str):
    """Mixed Chinese/English tokenizer without adding a new dependency."""
    text = (text or "").lower()
    # Keep contiguous CJK characters and alphanumeric terms. Single CJK chars
    # are retained because Chinese academic terms can be two/three characters.
    chunks = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", text)
    tokens = []
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) <= 4:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
                tokens.append(chunk)
        else:
            tokens.append(chunk)
    return [t for t in tokens if t not in STOPWORDS]


def node_text(node):
    try:
        return node.get_content()
    except Exception:
        return getattr(node, "text", "") or ""


def build_lexical_stats(nodes):
    doc_tokens = []
    df = Counter()
    for node in nodes:
        terms = tokenize(node_text(node))
        counts = Counter(terms)
        doc_tokens.append(counts)
        df.update(counts.keys())
    return doc_tokens, df, len(nodes)


def bm25_score(query_tokens, doc_counts, df, n_docs, avgdl, k1=1.5, b=0.75):
    if not query_tokens or not doc_counts:
        return 0.0
    dl = sum(doc_counts.values())
    score = 0.0
    for term in set(query_tokens):
        tf = doc_counts.get(term, 0)
        if not tf:
            continue
        dft = df.get(term, 0)
        idf = math.log(1.0 + (n_docs - dft + 0.5) / (dft + 0.5))
        denom = tf + k1 * (1 - b + b * dl / max(avgdl, 1.0))
        score += idf * (tf * (k1 + 1)) / denom
    return score


def minmax(values):
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi <= lo:
        return {i: (1.0 if hi > 0 else 0.0) for i in range(len(values))}
    return {i: (v - lo) / (hi - lo) for i, v in enumerate(values)}


def infer_scope(query: str, node) -> str:
    """Soft academic scope signal. Never rejects a node."""
    text = (query + " " + node_text(node)).lower()
    meta = getattr(node, "metadata", {}) or {}
    scope = " ".join(str(v) for v in meta.values()).lower()

    # Keep this deliberately conservative. Exact department names can be
    # supplied by the existing query-expansion layer, while unknown metadata
    # remains neutral rather than being treated as a mismatch.
    direct_terms = ["資訊工程學系", "資工系", "資訊工程"]
    college_terms = ["資訊電機學院", "資電學院"]
    university_terms = ["全校", "共同", "大學部"]

    if any(t in scope for t in direct_terms) or any(t in text for t in direct_terms):
        return "direct"
    if any(t in scope for t in college_terms) or any(t in text for t in college_terms):
        return "college"
    if any(t in scope for t in university_terms):
        return "university"
    return "unknown"


def expand_query_for_retrieval(user_query: str, history_str: str = "") -> str:
    print("[Academic Agent V4] Query expansion...")
    system_prompt = f"""
你是中央大學校務法規檢索系統的查詢擴充器。
只做檢索用語補充，不改變使用者原始意圖。
保留對話中的核心問題；若使用者明確換系所，以新系所覆蓋舊系所。
可以補充法規正式用語、學院、全校共同規定、大學部等上層詞，但不要虛構具體規定。

對話歷史：
{history_str}

只輸出一行檢索字串。
"""
    try:
        response = llm_smart.chat([
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=user_query),
        ])
        expanded = response.message.content.strip()
        print(f"[Academic Agent V4] Expanded: {expanded}")
        return expanded
    except Exception as e:
        print(f"[Query expansion fallback] {e}")
        return user_query


def retrieve_hybrid(index, original_query: str, expanded_query: str):
    """RAGFlow-style: independent recall channels -> merge -> normalized hybrid ranking."""
    all_nodes = list(index.docstore.docs.values())
    if not all_nodes:
        return []

    # Dense channel: use LlamaIndex's existing vector index, preserving the
    # current embedding/storage layer instead of changing two variables at once.
    retriever = index.as_retriever(similarity_top_k=DENSE_TOP_K)
    dense_original = retriever.retrieve(original_query)
    dense_expanded = retriever.retrieve(expanded_query) if expanded_query and expanded_query != original_query else []

    dense_scores = {}
    dense_nodes = {}
    for item in dense_original + dense_expanded:
        node = item.node
        nid = node.node_id
        score = float(item.score or 0.0)
        if nid not in dense_scores or score > dense_scores[nid]:
            dense_scores[nid] = score
            dense_nodes[nid] = node

    # Lexical channel: BM25 over the same SentenceWindow nodes. This avoids a
    # new external dependency and gives exact academic terms a second recall path.
    stats = build_lexical_stats(all_nodes)
    doc_tokens, df, n_docs = stats
    avgdl = sum(sum(c.values()) for c in doc_tokens) / max(n_docs, 1)
    q_tokens = tokenize(original_query + " " + expanded_query)

    lexical_scores = {}
    for i, node in enumerate(all_nodes):
        score = bm25_score(q_tokens, doc_tokens[i], df, n_docs, avgdl)
        if score > 0:
            lexical_scores[node.node_id] = score

    # Candidate union. No scope/FAQ filter happens here.
    candidate_ids = set(dense_nodes) | set(sorted(lexical_scores, key=lexical_scores.get, reverse=True)[:LEXICAL_TOP_K])

    raw_vector = [dense_scores.get(nid, 0.0) for nid in candidate_ids]
    raw_lexical = [lexical_scores.get(nid, 0.0) for nid in candidate_ids]
    vnorm = minmax(raw_vector)
    lnorm = minmax(raw_lexical)

    ranked = []
    for i, nid in enumerate(candidate_ids):
        node = dense_nodes.get(nid)
        if node is None:
            node = next((n for n in all_nodes if n.node_id == nid), None)
        if node is None:
            continue
        score = VECTOR_WEIGHT * vnorm[i] + LEXICAL_WEIGHT * lnorm[i]
        scope = infer_scope(original_query + " " + expanded_query, node)
        score += ACADEMIC_SCOPE_BOOST.get(scope, 0.0)
        meta_text = " ".join(str(v) for v in (getattr(node, "metadata", {}) or {}).values()).lower()
        if "faq" in meta_text or "常見問題" in meta_text or "問答" in meta_text:
            score += FAQ_BOOST
        ranked.append({"node": node, "vector": vnorm[i], "lexical": lnorm[i], "hybrid": score, "scope": scope})

    ranked.sort(key=lambda x: x["hybrid"], reverse=True)
    return ranked[:CANDIDATE_TOP_K]


def rerank_candidates(candidates, query: str):
    if not candidates:
        return []

    nodes = [x["node"] for x in candidates]
    reranker = LLMRerank(
        choice_batch_size=RERANK_BATCH_SIZE,
        top_n=min(RERANK_TOP_N, len(nodes)),
        llm=Settings.llm,
    )
    reranked_nodes = reranker.postprocess_nodes(nodes, QueryBundle(query))

    by_id = {x["node"].node_id: x for x in candidates}
    results = []
    for node_with_score in reranked_nodes:
        nid = node_with_score.node.node_id
        base = by_id.get(nid, {"hybrid": 0.0, "vector": 0.0, "lexical": 0.0, "scope": "unknown"})
        rr = float(node_with_score.score or 0.0)
        # LlamaIndex rerank scores are provider/model dependent. Keep the
        # score as the primary gate, while hybrid remains a tie-breaker.
        results.append({**base, "node": node_with_score.node, "rerank": rr})

    results.sort(key=lambda x: (x["rerank"], x["hybrid"]), reverse=True)
    return results


def evidence_gate(results, query: str):
    """Final conservative gate: retrieval can be broad; answering cannot."""
    accepted = []
    for item in results:
        text = node_text(item["node"]).strip()
        if len(text) < MIN_EVIDENCE_CHARS:
            continue
        rr = item.get("rerank", 0.0)
        if rr >= MIN_FINAL_RERANK_SCORE:
            accepted.append(item)

    # Never fabricate an answer from an empty/weak evidence set.
    return accepted[:RERANK_TOP_N]


QA_PROMPT = """你是 NCUXplore 的中央大學校園法規檢索助理。
只能根據參考文件回答，不得使用外部知識補充不存在的規定。

規則：
1. 若文件存在不同年份版本，優先採用文件明確標示為最新/最新修訂/最新學年度的內容；不要自行猜年份。
2. 一般法規是規定依據；FAQ/問答只能作為實務補充。
3. 若不同文件衝突，指出衝突並依文件中的日期、修訂資訊判斷，不可自行創造優先順序。
4. 若證據不足，直接回答「目前系統的參考文件中未包含足以回答此問題的資訊。」
5. 簡潔條列回答，保留重要條件、門檻、例外與適用對象。

參考文件：
{context}

使用者問題：
{query}
"""


def answer_from_evidence(query: str, final_nodes):
    if not final_nodes:
        return "目前系統的參考文件中未包含足以回答此問題的資訊。"

    context_parts = []
    for i, item in enumerate(final_nodes, 1):
        node = item["node"]
        meta = getattr(node, "metadata", {}) or {}
        file_name = meta.get("file_name", "未知文件")
        page = meta.get("page_label", "")
        label = f"[{i}] {file_name}"
        if page:
            label += f" 第 {page} 頁"
        context_parts.append(f"{label}\n{node_text(node)}")

    prompt = QA_PROMPT.format(context="\n\n---\n\n".join(context_parts), query=query)
    response = Settings.llm.chat([ChatMessage(role=MessageRole.USER, content=prompt)])
    return response.message.content.strip()


def query_academic_knowledge(query_str: str, history_str: str = "") -> dict:
    try:
        index = get_or_create_index()
        print(f"\n[Academic Agent V4] 原始問題：{query_str}")
        expanded_query = expand_query_for_retrieval(query_str, history_str)

        candidates = retrieve_hybrid(index, query_str, expanded_query)
        print(f"[Academic Agent V4] Hybrid candidates: {len(candidates)}")
        for i, item in enumerate(candidates[:10], 1):
            print(
                f"  #{i} hybrid={item['hybrid']:.3f} "
                f"vec={item['vector']:.3f} lex={item['lexical']:.3f} "
                f"scope={item['scope']} file={item['node'].metadata.get('file_name', '未知')}"
            )

        reranked = rerank_candidates(candidates, query_str)
        print(f"[Academic Agent V4] Reranked: {len(reranked)}")
        for i, item in enumerate(reranked, 1):
            print(f"  #{i} rerank={item['rerank']:.3f} hybrid={item['hybrid']:.3f} {item['node'].metadata.get('file_name', '未知')}")

        final_results = evidence_gate(reranked, query_str)
        print(f"[Academic Agent V4] Evidence accepted: {len(final_results)}")

        answer = answer_from_evidence(query_str, final_results)
        window_processor = MetadataReplacementPostProcessor(target_metadata_key="window")
        # Source text is generated from the original nodes above; metadata window
        # replacement is intentionally not used to alter the evidence gate.

        sources = []
        for item in final_results:
            meta = getattr(item["node"], "metadata", {}) or {}
            name = meta.get("file_name", "未知文件")
            page = meta.get("page_label", "")
            source = f"{name} (第 {page} 頁)" if page else name
            if source not in sources:
                sources.append(source)

        return {"answer": answer, "sources": sources}

    except Exception as e:
        print(f"[Academic Agent V4] 查詢錯誤：{e}")
        return {
            "answer": "系統在檢索法規與常見問題時發生錯誤，請稍後再試。",
            "sources": [],
        }


if __name__ == "__main__":
    result = query_academic_knowledge("資工系英文畢業門檻")
    print("\n[Academic Agent V4 回答]")
    print(result["answer"])
    print("\n[Sources]")
    for source in result["sources"]:
        print("-", source)
