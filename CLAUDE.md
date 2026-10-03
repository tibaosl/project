# CLAUDE.md

給 Claude Code 看的專案守則。細節（功能清單、怎麼跑專案、怎麼測試）請看 [README.md](README.md)，這裡只記「規則」跟「現況」，避免重複維護。

## 最重要的規則：branch 紀律

* **絕對不要直接在 `main` 上改東西、commit 或 push。** `main` 是整合後的主線，新功能要走 PR/merge 流程進來。
* 開始做任何修改前，先確認目前在哪個 branch（`git branch` / `git status`），如果在 `main` 上而使用者要開發功能，先跟使用者確認要不要切 branch、切去哪個。
* 目前的 branch 對應：
  * `main`：整合後主線，不要直接改。
  * `選課`：選課功能開發，**是另一位組員負責的範圍，Claude 不用主動去改這條 branch**。
  * `RAG`：舊的 RAG 開發 branch，**已經落後 main、不要再用**（2026-09-28 起 RAG 改在 `RAG重構` 重寫）。
  * `RAG重構`：RAG 重寫（文件解析、目錄挑文件、評估題庫），從 main 開出來的。
* commit / push 前，照系統既有的安全守則跟使用者確認（尤其是 push、以及任何會影響到共用 branch 的操作）。

## 新增功能時

* 新增 agent 工具（`agent_tools.py` 的 `@tool`）時，一定要在 `suggestions.py` 的 `TOOL_SUGGESTIONS` 登記：功能名稱、需不需要登入、幾個範例問題（不適合推薦就讓範例留空）。開場推薦問題跟「你可能還想問」都是從這裡產生的，前端不用改。範例要挑實際查得到結果的問題（例如活動搜尋是比對標題關鍵字，「藝文」常常查不到，法規查詢只答得出 `data/` 裡有的文件，沒有收錄的主題例如停車證就不要放）。
* 新增工具或改了工具說明、`AGENT_SYSTEM_PROMPT`、supervisor 的模型之後，跑 `python agent_eval/run_routing.py`（跟 `--holdout`）確認 agent 選工具沒有變差；新增工具時也在裡面的 `CASES` 補幾題會用到它的問法。supervisor 用 gpt-5.4-mini 走 Responses API，模型直接回文字時 `content` 是 list，取文字要用 `.text`。
* 改 RAG（`academic_agent.py`、`rag_documents.py`）之前跟之後都要跑 `python rag_eval/run_eval.py`，分數沒變差才算改好；改挑文件的 prompt 可以先用 `--router-only` 快速檢查。`rag_eval/holdout.json` 是保留測試題，不要照著它調 prompt。改了解析或卡片的邏輯要把 `rag_documents.py` 的 `PARSER_VERSION`／`CARD_VERSION` 加一，舊快取才會失效。
* 爬蟲（`crawler.py`）要新增或修改抓取的網站時，改 `crawler_sources.py`，先親自用瀏覽器看過那個網站（連結文字常常不是檔名、副檔名不可信、資訊可能只在網頁或圖片裡），再用 `python crawler.py --source 名稱 --dry-run` 確認檔名跟抓到的檔案。`data/` 的文件變多或換版之後，一樣要跑 `rag_eval` 確認法規問答沒變差。

## 開發環境重點

* Python 虛擬環境在 `venv/`，套件是直接裝在共用 venv 裡，沒有 `requirements.txt`。
* 專案根目錄的 `.env`（不受 git 追蹤）要有 `OPENAI_API_KEY`、`NCU_OAUTH_CLIENT_ID`/`NCU_OAUTH_CLIENT_SECRET`/`NCU_OAUTH_REDIRECT_URI` 才能用到 RAG 問答跟 OAuth 登入相關功能；沒有這些值專案仍能啟動，只是這些功能會不能用。
* 執行整個專案：`python run.py`（同時啟動 FastAPI 後端 `:8000` 跟 Vite 前端 `:5173`）。
* 測試：
  * 後端：`python -m pytest test_schedule_helpers.py test_oauth_portal.py test_rag_documents.py test_academic_agent.py test_crawler.py`
  * 前端：`cd frontend && npm test`
  * `test_agent_tools_schema.py`、`test_hours_activity.py` 需要 `.env` 裡有真的 `OPENAI_API_KEY`（甚至帳密）才能跑，一般改動不一定用得到。

## 現況（2026-09-22）

* `main` 剛在 2026-09-21 把前端重寫、OAuth 登入、活動功能等 work 併回來。
* `選課` branch 還在開發中（另一位組員負責），尚未併回 `main`，Claude 不用管。
* RAG 在 2026-09-28 於 `RAG重構` branch 重寫：舊版的問題主要出在文件解析（有表格的頁面整頁文字被丟掉、Big5 亂碼、相容字/康熙部首、Word 合併儲存格重複）跟切 chunk 後分不出是哪個學院/年度的文件。新版用 PyMuPDF 重新解析，改成「看文件目錄卡片挑文件 → 讀整份文件回答」，評估題庫在 `rag_eval/`。`data/` 的文件不在 git 裡，2026-10-03 起用 `python crawler.py` 從學校網站抓（舊的 `crawler_tools.py` 已移除：資工系網站改版後網址 404、會跳過 .odt、檔名常常變成「下載 PDF」或「」」，抓不到也分不出版本）。
* 2026-10-04 第一次用新爬蟲抓：7 個來源（教務處章則與校曆、註冊組、課務組、資工系、語言中心、學務處生活輔導組、住宿服務組）共 322 份，放在 `data/<來源>/`。原本 `data/` 根目錄的 77 個舊檔都有對應的新檔，已移到 `storage/crawler/legacy_backup/`（對照表在裡面的 mapping.json），評估題庫的標準文件也改成新檔名。文件變成 321 份後，挑文件的目錄約 17 萬 token，挑文件一次約 3～9 秒（之前約 3 秒），要更快再考慮加初篩（見 `academic_agent.py` 開頭）。
* 專題企劃書（NCUXplore 智慧校園代理系統）裡規劃但還沒做的功能主要是「學業分析」跟「課表規劃」。「學業分析」正在 `學業分析` branch 開發（`academic_tools.py`，資料來自 iNCU 成績查詢 + 畢業資格審查表）；「課表規劃」尚未開始。
* Portal 登入常跳 reCAPTCHA，Playwright 內建瀏覽器連真人都過不了，所以改開真正的 Chrome 讓使用者自己登入再用 CDP 接管。網頁的主要登入是 `/api/login/chrome`：同一個 Chrome 登入 Portal 後接著跑官方 OAuth 拿身分（帳號以官方 API 為準），一次完成身分驗證跟啟用功能；舊的「OAuth 整頁導向 + 另外補密碼」流程已移除。這只適用本機，將來架伺服器要改走官方資料 API 或瀏覽器擴充功能，屆時只需要換登入/抓資料這層，解析跟分析（`academic_tools.py` 等）不用動。登入狀態可以存在 `%LOCALAPPDATA%\NCUXplore` 重複用，但只有 `NCUSession(reuse_saved_state=True)` 才會用，`/api/login` 這種驗證身分的入口不能開（會變成拿錯密碼也能登入）。
* 選課系統（`cis.ncu.edu.tw/Course`）跟 Portal 分開登入、只收帳號密碼，Chrome 登入的 session 沒有密碼所以登不進去；非選課階段選課頁也沒有「依關鍵字」。目前 `search_courses` 遇到這些情況會丟 `RegistrationUnavailableError`（不會清掉 Portal session），課程搜尋也先從推薦問題拿掉。選課系統有不用登入的公開查詢頁（`/Course/main/query/byKeywords` 等），之後課程搜尋可以改用它——這是選課組員的範圍，要改先跟使用者確認。
* Claude Code CLI 已經整合進這台機器的 VS Code（裝了官方擴充功能），開發時可以直接在 VS Code 側邊欄跟 Claude Code 對話，不用額外開終端機。
