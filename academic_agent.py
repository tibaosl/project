import os
from dotenv import load_dotenv
from llama_index.core import (
    SimpleDirectoryReader, 
    VectorStoreIndex, 
    PromptTemplate,
    StorageContext,
    load_index_from_storage,
    Settings
)
from llama_index.llms.openai import OpenAI
from llama_index.core.node_parser import SentenceSplitter
from llama_index.readers.file import PyMuPDFReader

load_dotenv()

Settings.llm = OpenAI(model="gpt-4o-mini", temperature=0)

PERSIST_DIR = "./storage"

parser = PyMuPDFReader()
file_extractor = {".pdf": parser}

def get_or_create_index():
    if not os.path.exists(PERSIST_DIR):
        print("\n[Academic Agent] 正在解析 data 資料夾下的法規...")
        
        documents = SimpleDirectoryReader(
            "data",
            file_extractor=file_extractor,
            exclude=["*.doc"]
        ).load_data()
    
        index = VectorStoreIndex.from_documents(documents)
        
        index.storage_context.persist(persist_dir=PERSIST_DIR)
        print("[Academic Agent] 本地資料庫已成功建立！")
    else:
        print("\n[Academic Agent] 發現本地資料庫，直接從硬碟載入...")
        storage_context = StorageContext.from_defaults(persist_dir=PERSIST_DIR)
        index = load_index_from_storage(storage_context)
    return index

def query_academic_knowledge(query_str: str) -> str:
    index = get_or_create_index()
    
    print(f"[Academic Agent] 正在從資料庫中檢索：「{query_str}」")
    
    query_engine = index.as_query_engine(similarity_top_k=3)
    
    qa_prompt_tmpl_str = """\
    你是 NCUXplore 系統的專業校園法規檢索助理。請根據以下提供的「多份」參考文件來回答問題。
    
    【嚴格規定】：
    1. 跨文件對照：你必須綜合考量「所有」提供的文件內容。如果 A 文件（如申請單）只寫了請繳費，你必須繼續去 B 文件（如管理細則）找出「具體金額」。
    2. 嚴禁敷衍：絕對不允許回答「請自行向系辦查詢」、「文件中未提及具體金額」。你必須盡全力從文件中拼湊出報價。
    3. 輸出格式：請明確條列出「具體金額」、「適用條件」、「免費或例外情況」。
    
    參考文件：
    ---------------------
    {context_str}
    ---------------------
    問題：{query_str}
    回答：
    """
    qa_prompt_tmpl = PromptTemplate(qa_prompt_tmpl_str)
    query_engine.update_prompts({"response_synthesizer:text_qa_template": qa_prompt_tmpl})
    
    response = query_engine.query(query_str)

    # debug
    """ 
    print("\n[Debug ] RAG 實際找到並餵給 AI 的參考區塊：")
    for i, node in enumerate(response.source_nodes):
        filename = node.metadata.get('file_name', '未知檔案')
        print(f"\n--- 區塊 {i+1} (來自: {filename}) ---")
        print(node.node.text.strip())
        print("-" * 30) 
    """

    return str(response)

if __name__ == "__main__":
    test_query = "幫我查資工系空間借用的收費標準？"
    answer = query_academic_knowledge(test_query)
    print(f"\n[Academic Agent 回答]:\n{answer}")