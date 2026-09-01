# NCUXplore Academic RAG v4

這一版不是 `academic_agent_v3_4.py` 的小修版，而是新的 baseline：

**NCUXplore Academic API → RAGFlow → Evidence → Answer**

舊版的 dense/lexical/fusion/scope hard-filter/query-policy 不搬過來。

## 目前目標

Phase 1 只做一件事：

1. RAGFlow 正常運作。
2. 用原本 `data/` 的 PDF/DOCX 建立 dataset。
3. 建立 Chat assistant。
4. 由這個 FastAPI thin layer 呼叫 RAGFlow。
5. 用 `eval/questions.jsonl` 做第一輪 baseline。

RAGFlow 官方目前的 quickstart 支援 PDF、DOCX、TXT、MD、表格與圖片等文件格式，dataset 可指定 chunking 與 embedding；文件解析後可直接建立 Chat assistant。官方也提供 HTTP/Python API。 

## 1. 啟動 RAGFlow

官方目前 stable Docker 路線以 `v0.26.4` 為例：

```bash
git clone https://github.com/infiniflow/ragflow.git
cd ragflow
git checkout -f v0.26.4
cd docker
docker compose -f docker-compose.yml up -d
```

Windows + Docker Desktop / WSL2 使用者，請先依 RAGFlow 官方文件處理 `vm.max_map_count=262144`。

啟動完成後，在瀏覽器開 RAGFlow UI。

## 2. 設定模型

在 RAGFlow 的 Model providers 設定：

- Chat model
- Embedding model
- Image-to-text model（如果要處理視覺 PDF）

這些設定由 RAGFlow 管理，Academic API 不再自己建立 LlamaIndex index。

## 3. 建立 dataset

第一輪建議：

`NCU Academic Regulations`

先把正式規章放進去。

FAQ 暫時不要混進正式規章 dataset，第二輪再建立：

`NCU Academic FAQ`

## 4. 上傳現有資料

把原本專案的 `data/` 放到本專案旁邊：

```text
NCUXplore/
├── data/
├── academic_rag_v4/
└── ...
```

建立 `.env`：

```bash
copy .env.example .env
```

填入：

```text
RAGFLOW_API_KEY=你的 RAGFlow API key
```

然後：

```bash
pip install -r requirements.txt
python scripts/upload_documents.py --data ../data
```

腳本會建立 dataset 並上傳文件。

**第一版刻意不自動替你決定 chunk template / embedding model。**

請在 RAGFlow UI 確認 dataset 設定後再開始 parsing。

## 5. 建立 Chat assistant

在 RAGFlow UI：

1. Chat
2. Create chat
3. 指定 `NCU Academic Regulations`
4. 設定 system prompt
5. 設定 empty response
6. 記下 Chat ID

RAGFlow 官方文件明確提供「empty response」設定，可以讓回答被限制在 dataset evidence，而不是沒有檢索結果時自行發揮。

把 Chat ID 放進：

```text
RAGFLOW_CHAT_ID=...
```

## 6. 啟動 Academic API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

測試：

```bash
curl http://localhost:8000/health
```

查詢：

```bash
curl -X POST http://localhost:8000/api/academic/query ^
  -H "Content-Type: application/json" ^
  -d "{"query":"資工系英文畢業門檻"}"
```

Linux/macOS：

```bash
curl -X POST http://localhost:8000/api/academic/query   -H "Content-Type: application/json"   -d '{"query":"資工系英文畢業門檻"}'
```

## 7. 第一輪不要做的事情

不要把舊版這些邏輯搬進來：

- FAQ intent hard routing
- academic intent keyword table
- department → college 自動推導
- filename regex scope classification
- unknown scope hard rejection
- dense/lexical 手工 fusion
- SentenceWindow
- FAQ boost
- query variants 爆炸
- LLM rerank 自己再包一層

這些全部等 baseline 測完再決定。

## 8. 第二階段

當 Phase 1 跑通後，再加入：

```text
document metadata
    ↓
scope/applicability
    ↓
effective year/version
    ↓
academic answer policy
```

這時才處理：

- 資工系 vs 資管系
- 學院規章 vs 全校規章
- 最新年度
- 正式規章 vs FAQ
- 文件衝突

而且 scope 先作為 retrieval/rerank evidence，不直接把 unknown 文件全部殺掉。

## 9. 評估

`eval/questions.jsonl` 是第一批 10 題。

不要用「這次看起來比較好」當判斷。

至少記：

- retrieval recall
- answer correctness
- applicability correctness
- citation correctness
- unsupported answer rate

目標是讓 v4 可以跟舊 v3.4 做 A/B test。

## API 設計

目前只有：

`POST /api/academic/query`

Request:

```json
{
  "query": "資工系英文畢業門檻",
  "session_id": null
}
```

Response:

```json
{
  "answer": "...",
  "session_id": "...",
  "sources": [...]
}
```

Academic API 故意保持很薄。RAGFlow 才是 RAG engine。

## 注意

這個 starter 是 Phase 1 scaffold，不假裝已經完成 Academic applicability layer。

先讓 RAGFlow 原生 retrieval 在你的資料上跑出 baseline，再開始加校規專用邏輯。
