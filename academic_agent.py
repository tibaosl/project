import os
import io
import re
import shutil
import base64
from dotenv import load_dotenv
from pdf2image import convert_from_path
from openai import OpenAI

from llama_index.core import (
    VectorStoreIndex, 
    PromptTemplate,
    StorageContext,
    load_index_from_storage,
    Settings,
    Document
)
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.readers.file import DocxReader
from llama_index.core.llms import ChatMessage, MessageRole

load_dotenv()

Settings.llm = LlamaOpenAI(model="gpt-4o-mini", temperature=0)
llm_smart = LlamaOpenAI(model="gpt-4o", temperature=0)
openai_client = OpenAI()

CACHE_MD_DIR = "./parsed_markdown_cache"
PERSIST_DIR = "./storage"
DATA_DIR = "data"

POPPLER_PATH = os.getenv("POPPLER_PATH", None)

def clean_markdown_output(text: str) -> str:
    """【自動清洗器】自動砍掉 GPT 的廢話開場白、警語與 Markdown 區塊標籤"""
    if not text:
        return ""

    text = re.sub(r"^```markdown\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^(I'm unable to|However, I can|Please adjust|Here's a|Here is|這是一份).*?\n+", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n+(Please adjust|Hope this helps|如需修改|希望這對您有幫助).*?$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    
    return text.strip()

def convert_pdf_to_markdown_via_vision(pdf_path: str) -> str:
    """智慧型 Context-Aware Vision 轉碼器 (自動銜接跨頁表格 + 自動清洗)"""
    filename = os.path.basename(pdf_path)
    cache_path = os.path.join(CACHE_MD_DIR, f"{filename}.md")
    
    if os.path.exists(cache_path):
        print(f"  [快取命中] 讀取 Markdown: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"  [解析中] 正在處理 {filename}...")
    
    images = convert_from_path(pdf_path, dpi=200, poppler_path=POPPLER_PATH)
    total_pages = len(images)

    all_pages_md = []
    previous_page_snippet = ""

    for i, img in enumerate(images):
        print(f"    --> 正在解析第 {i+1} / {total_pages} 頁...")

        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

        user_prompt = "請將這張校園法規/申請表 PDF 頁面轉為標準且結構完整的 Markdown 格式。"
        user_prompt += (
            "\n\n【排版與表格結構極重要指示】：\n"
            "1. 門檻對照表邏輯：若頁面包含英文門檻表格，請統一整理為標準 6 欄表格："
            "| Test Name | 資電學院 | 客家學院 | 管理學院 | 生醫學院 | 校學士 |\n"
            "2. 填寫欄分離：頁面上的『考試成績/考試日期/簽章』等學生填寫欄位，請獨立拆成後續的小表格或列表，絕對不可將門檻數據與填寫欄混在同一個表格中！\n"
        )

        if previous_page_snippet:
            user_prompt += (
                f"\n3. 跨頁銜接：上一頁 Markdown 結尾如下：\n```\n{previous_page_snippet}\n```\n"
                f"如果本頁頂部的文字（如 150(115年後適用)、培力英檢）是上一頁表格的『未完成延續』，"
                f"**請將它們填入正確的 6 欄表格中，並自動補上 | Test Name | 資電學院 | 客家學院 |... 表頭**！"
            )

        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一個專業的文件 OCR 與表格轉錄引擎。"
                        "你的任務是精準轉錄圖片中的內容為純 Markdown。"
                        "【嚴格禁令】：嚴禁輸出任何招呼語、開場白、結尾說明或 ```markdown 包裹標籤，請直接輸出 Markdown 內文。"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}
                    ]
                }
            ],
            temperature=0
        )
        
        raw_text = response.choices[0].message.content
        
        cleaned_page_md = clean_markdown_output(raw_text)
        
        all_pages_md.append(f"\n{cleaned_page_md}")
        
        previous_page_snippet = cleaned_page_md[-400:] if len(cleaned_page_md) > 400 else cleaned_page_md

    full_md_content = "\n\n---\n\n".join(all_pages_md)

    os.makedirs(CACHE_MD_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(full_md_content)

    return full_md_content

def load_documents():
    """自動同時處理 .pdf 與 .docx 檔案"""
    documents = []
    docx_parser = DocxReader()
    
    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            file_path = os.path.join(root, file)

            if file.startswith(".") or file.startswith("~$"):
                print(f"  [忽略隱藏/暫存檔]: {file}")
                continue
                
            if os.path.getsize(file_path) == 0:
                print(f"  [警告] 檔案長度為 0 (空檔案)，已跳過: {file}")
                continue
            
            if file.endswith(".pdf"):
                try:
                    md_text = convert_pdf_to_markdown_via_vision(file_path)
                    doc = Document(
                        text=md_text,
                        metadata={"file_name": file, "file_path": file_path}
                    )
                    documents.append(doc)
                except Exception as e:
                    print(f"\n  [檔案損毀] 無法讀取 PDF: {file}")
                    print(f" 錯誤原因: {e}")
                    print("系統已自動跳過此檔案，繼續處理其他文件...\n")
                
            elif file.endswith(".docx"):
                try:
                    print(f"  [Word 載入中] 讀取文件: {file}")
                    docx_docs = docx_parser.load_data(file_path)
                    for doc in docx_docs:
                        doc.metadata["file_name"] = file
                        documents.append(doc)
                except Exception as e:
                    print(f"  [檔案損毀] 無法讀取 Word 檔: {file}，錯誤: {e}")
                
    return documents

def get_latest_data_mtime() -> float:
    """計算 data 資料夾內所有檔案的最晚修改時間 (mtime)"""
    latest_mtime = 0.0
    if not os.path.exists(DATA_DIR):
        return latest_mtime

    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            if file.startswith(".") or file.startswith("~$"):
                continue
            file_path = os.path.join(root, file)
            mtime = os.path.getmtime(file_path)
            if mtime > latest_mtime:
                latest_mtime = mtime
    return latest_mtime

def get_or_create_index():
    mtime_file = os.path.join(PERSIST_DIR, ".data_mtime")
    current_latest_mtime = get_latest_data_mtime()
    
    need_rebuild = False

    if not os.path.exists(PERSIST_DIR):
        need_rebuild = True
    else:
        if os.path.exists(mtime_file):
            with open(mtime_file, "r") as f:
                saved_mtime = float(f.read().strip())
            if current_latest_mtime > saved_mtime:
                print("\n[Academic Agent] 偵測到 data 資料夾內容有異動，自動刪除舊 Index 並重新建庫...")
                need_rebuild = True
        else:
            need_rebuild = True

    if need_rebuild:
        if os.path.exists(PERSIST_DIR):
            shutil.rmtree(PERSIST_DIR)

        print("\n[Academic Agent] 正在解析 data 資料夾下的法規 (.pdf / .docx)...")
        documents = load_documents()
        
        print("\n[Academic Agent] 文件載入完成！正在進行 Markdown 智慧切塊...")
        node_parser = MarkdownNodeParser()
        nodes = node_parser.get_nodes_from_documents(documents)

        for node in nodes:
            file_name = node.metadata.get('file_name', '未知檔案')
            node.text = f"【來源檔案: {file_name}】\n{node.text}"
        
        index = VectorStoreIndex(nodes)
        index.storage_context.persist(persist_dir=PERSIST_DIR)

        with open(mtime_file, "w") as f:
            f.write(str(current_latest_mtime))
            
        print("[Academic Agent] 本地向量資料庫已成功建立/更新！")
    else:
        print("\n[Academic Agent] 資料無異動，直接從硬碟載入向量資料庫...")
        storage_context = StorageContext.from_defaults(persist_dir=PERSIST_DIR)
        index = load_index_from_storage(storage_context)

    return index

def expand_query_for_retrieval(user_query: str, history_str: str = "") -> str:
    print(f"\n[Academic Agent] 正在分析並擴寫問題以提升檢索精準度...")
    
    system_prompt = f"""
    你是一個中央大學法規檢索系統的「查詢關鍵字擴充器」，負責服務全校所有科系。
    你的任務是補足使用者問題中隱含的「上層行政單位」或「法規正式用語」，以便讓向量資料庫能搜到正確的文件。

    【對話歷史】
    使用者最近的提問軌跡如下（箭頭表示先後順序）：
    {history_str}
    
    【全校通用擴充規則】：
    1. 意圖與條件強制合併：當使用者在最新輸入中只是「補充條件」（如：資工系、大學部），你必須將「對話歷史」中的「核心詢問目標」（如：畢業門檻、多少錢）完整保留並合併在一起，絕對不可以把歷史中真正要問的事情丟掉。
    2. 話題切換與覆蓋：如果使用者提出了「全新的系所/單位」（如：客家系、那企管系呢），請「直接用新系所覆蓋掉」歷史紀錄中的舊系所，但保留原本詢問的動作，絕對不可以把兩個不同的系所混在一起。
    3. 動態學院推導：若問題中提到「任何系所」（例如：企管系、物理系、資工系），請運用你的常識，自動在關鍵字中補上該系所隸屬的「學院名稱」（如管理學院、理學院、資電學院），以及「全校通用」、「大學部」等上層關鍵字。
    
    【範例】：
    歷史：畢業門檻 -> 資工系大學部的
    輸出：中央大學 資訊工程學系 資電學院 大學部 畢業門檻 英文能力鑑定

    歷史：資工系畢業門檻 -> 那客家系呢
    輸出：中央大學 客家語文暨社會科學學系 客家學院 大學部 畢業門檻 英文能力鑑定
    
    歷史：借大講堂 -> 多少錢
    輸出：中央大學 大講堂 場地借用 收費標準 費用
    
    請只輸出擴充後的字串，絕對不要輸出任何其他解釋或廢話。
    """

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
        ChatMessage(role=MessageRole.USER, content=user_query)
    ]

    response = llm_smart.chat(messages)
    expanded_query = response.message.content.strip()
    
    print(f"[Academic Agent] 擴寫後問題：{expanded_query}")
    return expanded_query

def query_academic_knowledge(query_str: str, history_str: str = "") -> str:
    index = get_or_create_index()
    
    print(f"\n[Academic Agent] 收到原始問題：「{query_str}」")

    expanded_query = expand_query_for_retrieval(query_str, history_str)

    query_engine = index.as_query_engine(similarity_top_k=8)
    
    qa_prompt_tmpl_str = """\
    你是 NCUXplore 系統的專業校園法規檢索助理，負責服務「中央大學全校師生」。請嚴格根據以下提供的參考文件內容來回答問題。

    【核心守則】：
    1. 衝突與新舊版本判定：
       - 若參考文件中，有部分文件「標註了最新年份（如 115 學年）」而另一份文件「未標註年份/無年份」，**未標註年份者一律視為舊版法規**。
       - 參考文件中可能同時包含「最新版」與「舊版/歷年」的法規資料。
       - 若不同區塊間出現數據或規定衝突（例如：分數門檻不同），**必須強制優先採用檔名或內容標註為最新年份（如 115 學年度、最新修訂）的區塊**，絕對不可採用舊版數據。
       - 系統預設必須**優先採用標有最新年份的文件內容**進行回答。
       - 回答時請明確說明：「依據 115 學年度起適用之最新規定...」。
    2. 動態階層繼承推理：使用者可能會詢問全校「任何系所」的規定。若文件中標示為該系所隸屬的「學院」規定，或「全校大學部」通用規定，請直接套用該標準回答，並主動向使用者說明層級關係（例如：「您詢問的企管系隸屬管理學院，依據管理學院/全校大學部規定...」）。
    3. 雜訊過濾：請精準針對使用者的「問題核心」回答，自動忽略無關的獎金、匯款帳號、郵局存摺等行政細節。
    4. 嚴禁推託與捏造：只能根據文件回答。若文件中完全沒有相關資訊，請直接回答：「目前系統的參考文件中未包含此資訊。」

    ---------------------
    參考文件內容如下：
    {context_str}

    使用者問題：
    {query_str}
    """
    qa_prompt_tmpl = PromptTemplate(qa_prompt_tmpl_str)
    query_engine.update_prompts({"response_synthesizer:text_qa_template": qa_prompt_tmpl})
    
    response = query_engine.query(expanded_query)

    sources = []
    if hasattr(response, "source_nodes") and response.source_nodes:
        for node in response.source_nodes:
            file_name = node.metadata.get("file_name", "未知文件")
            page_label = node.metadata.get("page_label", "")
            
            source_text = f"{file_name}"
            if page_label:
                source_text += f" (第 {page_label} 頁)"
                
            if source_text not in sources:
                sources.append(source_text)

    # debug
    """ print("\n[Debug ] RAG 實際找到並餵給 AI 的參考區塊：")
    if not response.source_nodes:
        print("沒有檢索到任何相關區塊！請確認法規文件是否齊全。")
        
    for i, node in enumerate(response.source_nodes):
        filename = node.metadata.get('file_name', '未知檔案')
        print(f"\n--- 區塊 {i+1} (來自: {filename}) ---")
        print(node.node.text.strip())
        print("-" * 30) """
    
    return {
        "answer": str(response),
        "sources": sources
    }

if __name__ == "__main__":
    test_query = "資工系畢業門檻"
    answer = query_academic_knowledge(test_query)
    print(f"\n[Academic Agent 回答]:\n{answer}")