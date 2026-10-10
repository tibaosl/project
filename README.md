# NCUXplore

中央大學校園智慧助手聊天機器人。使用者用自然語言問問題（法規、課表、時數、活動...），
後端的 LangGraph agent 自己判斷該查什麼資料、該用哪個工具，答案用串流的方式即時顯示在
前端網頁上。

## 目前有的功能

* **校園法規問答**：先看文件目錄挑出相關文件、再讀整份文件回答，回答附上來源出處
  （`rag_documents.py` 處理文件、`academic_agent.py` 查詢，細節見下面「校園法規問答（RAG）」）。
* **學校網站文件爬蟲**：`python -m backend.rag.crawler` 把全校各行政單位（教務處、學務處各組、總務處、國際處、圖書館、
  計中、通識、體育室…）跟各學院、系所網站上的法規、修業規定、表單、說明網頁抓到 `data/`，給法規問答用
  （`crawler.py`，要抓哪些網站設定在 `crawler_sources.py`）。
* **登入**：兩種方式擇一，登入後給一個一次性通行證（token），不會每次對話都重傳密碼。
  * 用 Portal 帳號登入（推薦）：程式在這台電腦開一個 Chrome 視窗，你在 Portal 官方頁面
    自己登入（有人機驗證就自己勾），我們的網站完全不經手密碼；身分由 Portal 官方 OAuth
    確認，登入一次就能用所有功能。登入表單的「記住我」會自動勾好，Portal 會記住帳號
    29 天，這段期間登入時程式會直接幫你按登入（沒跳人機驗證的話），不用再輸入密碼。
    ⚠️ Chrome 視窗開在跑後端的電腦上，只適用於在自己電腦跑 `run.py`。
  * 或手動輸入 Portal 帳號密碼登入（沒裝 Chrome 時的備援）。
  * 只查法規/活動這類公開資訊不需要登入；要查「自己的」課表、時數、活動報名紀錄，
    或要送出報名/取消報名，才需要登入。
* **個人課表查詢**、**選課系統關鍵字搜尋**（`action_tools.py`）。
  ⚠️ 選課系統要另外用帳號密碼登入，用 Chrome 登入時目前查不了；非選課階段也查不到。
* **學業分析**：抓 iNCU 的學生成績查詢跟畢業資格審查表，整理出已修學分與畢業學分缺口、
  各畢業類別還差什麼、歷年學期平均與排名趨勢、不及格／停修需要重修的課（`academic_tools.py`）。
  iNCU 只有百分制的平均，GPA（4.3 制）照教務處註冊組〈成績表說明〉的等第對照表自己算，
  同一批課算出的累計平均跟 iNCU 對不上時會標「僅供參考」。
* **獎學金推薦**：拿 iNCU 成績單上的系所、年級、學期平均跟排名，比對法規問答收錄的獎學金辦法跟每學期的
  〈各項獎學金一覽表〉，列出看起來符合資格的獎學金（每個條件怎麼判斷的都會列出來），以及成績符合、
  但還要確認清寒、原住民這類身分條件的。使用者在對話裡說明身分（例如「我是低收入戶」）就會重新比對
  （`scholarship_tools.py`，見下面「獎學金推薦」）。
* **學習護照時數進度查詢**：對照畢業門檻，算出各類別還差多少小時。
* **我的行程**：把課表、已報名的活動跟校曆整理成每天的行程（「我這週有什麼行程」「明天要上什麼課」），
  放假、補假、停課的日子不列課並標出原因，寒暑假不列課，好幾天的受理期間在最後一天提醒
  （`backend/analysis/agenda.py`）。
* **校曆查詢**：教務處的〈學年度校曆〉用規則解析成有日期的事件（不呼叫模型），回答「期中考是什麼時候」
  「下週有放假嗎」這類問題，不用登入（`backend/analysis/academic_calendar.py`）。
* **匯出到行事曆**：下載 .ics 檔，匯入 Google 日曆、iPhone、Outlook。沒登入是整年的校曆，登入後再加上
  這學期每週的課（放假、停課的日子跳過）跟已報名的活動。校曆、我的行程卡片下面都有下載按鈕，
  也可以直接問「課表可以匯入 Google 日曆嗎」（`backend/analysis/calendar_export.py`、`/api/calendar/export`）。
* **活動查詢與推薦**：關鍵字搜尋、依「時數缺口」自動推薦活動、依時數標籤查詢，都會過濾掉
  報名時間已經截止、或正取跟備取都額滿的場次（`activity_tools.py`）。
* **查詢自己的活動報名紀錄**（接下來的活動排在前面）、**活動報名/取消報名**：活動有好幾個場次時會先問
  要哪一場，取消報名是從自己的報名紀錄找（活動名稱或場次名稱都可以）。報名/取消一定要使用者在對話裡
  明確回覆「確定」才會真的送出（「我還不確定」「確定要報名嗎」不算），不會被機器人自己誤觸發。
* **建議問題**：一進對話頁會隨機推薦幾個可以直接點的問題（沒登入只推薦不需登入的功能）。
  每輪回答完會在下面放可以直接點的選項（`suggestions.py`）：系統在反問使用者時，選項是那個反問的
  回答（例如學院名稱，是非題就只有「要／不用了」，數量看問題決定），其他時候是最多 3 個「你可能還想問」。
  模型會逐題抄出回答裡回答到它的那句話，抄得出來的（已經回答過的）就不推薦。

## 專案結構

```text
backend/                      後端（Python）
├── main.py                   FastAPI 伺服器：API、把 data/ 掛在 /files
├── paths.py                  data/、storage/ 這些資料夾的位置
├── logging_config.py
├── agent/                    對話代理
│   ├── supervisor_agent.py   判斷要用哪個功能、串流回答
│   ├── agent_tools.py        給模型用的工具（每個功能一個）
│   └── suggestions.py        開場推薦跟「你可能還想問」
├── ncu/                      連學校的系統
│   ├── action_tools.py       Portal 登入、iNCU 頁面（課表、時數、報名紀錄）、活動報名、選課
│   ├── activity_tools.py     公開的活動查詢
│   ├── oauth_portal.py       Portal 官方 OAuth
│   └── secure_requests.py    連學校網站時的憑證驗證
├── analysis/                 用自己的資料跟學校公開資料整理的資訊
│   ├── academic_tools.py     學業分析
│   ├── scholarship_tools.py  獎學金推薦
│   ├── academic_calendar.py  解析校曆
│   ├── agenda.py             我的行程（課表、活動、校曆）
│   └── calendar_export.py    匯出行事曆檔（.ics）
└── rag/                      校園法規問答
    ├── academic_agent.py     挑文件、讀全文回答
    ├── rag_documents.py      解析文件、產生文件卡片
    ├── crawler.py            學校網站文件爬蟲
    ├── crawler_sources.py    要抓哪些網站
    ├── update_documents.py   每週自動更新文件
    └── academic_hierarchy.json  系所跟學院的對照
frontend/                     前端（React + Vite）
tests/                        後端測試（pytest），tests/manual/ 是要真的帳密才能跑的手動測試
agent_eval/、rag_eval/        選工具、法規問答的評估（會呼叫 OpenAI）
run.py                        一鍵啟動前後端
data/、storage/               爬蟲抓的文件、快取跟執行紀錄（不在 git 裡）
```

`backend/` 裡的程式要在專案根目錄用 `python -m` 執行，例如 `python -m backend.rag.crawler`
（直接跑 `python backend/rag/crawler.py` 會找不到 `backend`）。

## 開發注意事項

* **新增 agent 工具時**，要在 `suggestions.py` 的 `TOOL_SUGGESTIONS` 補一筆（功能名稱、
  需不需要登入、幾個範例問題，不適合拿來推薦就讓範例留空），開場推薦跟追問才會涵蓋
  新功能。`tests/test_agent_tools_schema.py` 會檢查有沒有漏登記。

目前 GitHub 上的 branch：

* `main`：整合後的主線，**不要直接在這裡改**。新功能或修改從最新的 `main` 開一個新 branch，
  開發完、測試過再開 PR 併回 `main`。
* `選課`：選課功能開發，之後要改選課相關程式請在這個 branch 修改

## 執行專案

前置需求：

* Python 3.14 的虛擬環境（`venv/`），需要的套件列在 `requirements.txt`：
  `pip install -r requirements.txt`（共用的 `venv` 裡已經都裝好了）。
* 專案根目錄要有一個 `.env` 檔（不會被 git 追蹤，跟別人要或自己另外設定），至少要有：
  * `OPENAI_API_KEY`：法規問答（RAG）用。
  * `NCU_OAUTH_CLIENT_ID` / `NCU_OAUTH_CLIENT_SECRET` / `NCU_OAUTH_REDIRECT_URI`：
    Portal OAuth 登入用，去 portal.ncu.edu.tw 登入後在「應用系統管理」頁面申請取得。
  * 沒有這些值也能啟動，只是登入相關功能會不能用。
* [Node.js](https://nodejs.org)（選 LTS 版本）：前端是用 React + Vite，第一次執行會自動
  幫你 `npm install`，但要先裝好 Node.js 本身。
* Google Chrome：Portal 登入跳出人機驗證時，程式會開一個真正的 Chrome 視窗讓你自己登入
  （Playwright 內建的瀏覽器常常連真人都過不了驗證），沒裝 Chrome 才退回內建瀏覽器。

先進入虛擬環境：

```powershell
.\venv\Scripts\Activate.ps1
```

看到：

```text
(venv)
```

之後執行：

```powershell
python run.py
```

會同時啟動後端（FastAPI，`http://127.0.0.1:8000`）跟前端（Vite dev server，
`http://localhost:5173`），並自動開瀏覽器。關掉的話在終端機按 `Ctrl+C` 即可。

## 校園法規問答（RAG）

法規文件放在 `data/`（PDF、Word、OpenDocument，`.doc`、`.odt` 需要裝
[LibreOffice](https://www.libreoffice.org/download/download/) 自動轉檔）。這些檔案不在 git 裡，
用爬蟲從學校網站抓：

```powershell
python -m backend.rag.crawler --dry-run   # 先看會抓哪些檔案、檔名對不對
python -m backend.rag.crawler             # 抓到 data/<來源>/，例如 data/教務處註冊組/
```

* 要抓哪些網站設定在 `backend/rag/crawler_sources.py`（約 80 個單位，每個網站的狀況都寫在裡面）。重抓時沒變的檔案不會再下載，
  網站上更新的檔案會覆蓋，網站上已經拿掉的檔案會移到 `storage/crawler/removed/`。
* 有些資訊不是附檔而是網頁本身（獎學金一覽、宿舍 Q&A），會存成 `.md`，海報圖片（英文畢業門檻）
  另存成 PDF，讓 RAG 用圖片轉錄讀（挑到網頁時會一起帶上）。網頁裡的瀏覽人次、今天日期這類每次都不一樣的
  內容會拿掉，不然每次重抓都會被當成改版、重做卡片。
* 總務處、生醫理工學院的網頁是 JavaScript 載入的，原始 HTML 裡沒有內容，這兩個來源用 Playwright 的
  無頭 Chromium 渲染完再讀（`crawler_sources.py` 的 `render=True`），所以爬蟲也要先跑過
  `python -m playwright install chromium`。
* 每個檔案的來源網址記在 `data/.crawler_manifest.json`。不在裡面的檔案（自己手動放進 `data/` 的）
  爬蟲不會動。`python -m backend.rag.crawler --legacy` 可以檢查這些手動檔跟爬到的檔案有沒有重複，
  加 `--move-duplicate-legacy` 會把內容一模一樣的移到 `storage/crawler/legacy_backup/`。
  之前跟組員拿的舊 `data/`（根目錄那 77 個檔案）都已經有爬蟲抓的新版，可以直接移走再爬一次。

### 法規文件每週自動更新

`python -m backend.rag.update_documents` 會依序重抓學校網站、幫新增或改版的文件補卡片跟向量、整理新增或改版的
獎學金文件的申請資格，紀錄存在 `storage/update_logs/`（只留最近 12 份）。卡片先建好，網站開著的話
下一次查詢載入新目錄只要幾秒。平常一週只有幾份到幾十份文件要產生卡片，費用很低。某個網站剛好掛掉的話，連續兩次連不上之後這一輪就先跳過它
（原本抓到的檔案照舊保留），不會讓整個更新卡住。

跑網站的這台電腦用 Windows 工作排程器每週日 03:00 執行一次（工作名稱 `NCUXplore-update-documents`），
電腦那時關機或睡眠的話，下次開機登入後會補跑。要改時間或停用，開「工作排程器」找這個名稱，
或用 PowerShell：

```powershell
Get-ScheduledTask NCUXplore-update-documents | Get-ScheduledTaskInfo   # 上次、下次執行時間
Start-ScheduledTask NCUXplore-update-documents                         # 立刻跑一次
Unregister-ScheduledTask NCUXplore-update-documents                    # 移除
```

換一台電腦跑網站時，在專案資料夾用 PowerShell 重新註冊：

```powershell
$action = New-ScheduledTaskAction -Execute "$PWD\venv\Scripts\pythonw.exe" -Argument "-m backend.rag.update_documents" -WorkingDirectory $PWD
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 03:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName NCUXplore-update-documents -Action $action -Trigger $trigger -Settings $settings
```

### 查詢流程

每個問題的流程：

0. **初篩**：文件有一千八百份左右，目錄沒辦法整份交給模型。先用卡片的向量（`text-embedding-3-large`）
   跟關鍵字各排一次名，合併之後取前 60 份。問題或對話裡提到系所時，那個系所的文件會優先放進來。
1. **挑文件**：把問題、對話歷史跟初篩出來的「目錄卡片」（標題、類型、適用範圍、版本、摘要、
   能回答的問題）交給模型，挑出最多 4 份文件，或判斷要反問使用者、資料庫裡沒有。
   判斷成「沒有」時會用關鍵字比對找候選文件，再確認一次。
2. **回答**：把挑中文件的全文交給模型回答，標注引用 [1]、[2]。引用編號只用來決定下方要列出哪幾份參考資料，
   送給使用者之前會拿掉，分號也會換成逗號或句號（`academic_agent.AnswerCleaner`）。

文件解析結果、目錄卡片跟卡片向量都快取在 `storage/rag/`（以內容的 hash 當 key，檔案沒變就不會
重做）。`data/` 新增或修改檔案後，下一次查詢會自動補做，也可以先手動建好：

```powershell
python -m backend.rag.rag_documents   # 解析全部文件、補齊卡片，並印出目錄（用 6 個行程平行解析）
```

第一次建好全部一千八百份左右的文件要一兩個小時：掃描版的 PDF 每頁要用圖片轉錄（每份最多 30 頁，
`RAG_VISION_MAX_PAGES`），`.doc`、`.odt` 要用 LibreOffice 轉檔，每份文件還要呼叫模型產生卡片，
都會花 OpenAI 的費用。之後只會補新增或改過的檔案。

**改 RAG 之前跟之後都要跑評估**，分數沒變差才算改好（之前改了很多版一直修不好，就是因為
沒有固定的測試題，修好一題又弄壞另一題也不會發現）：

```powershell
python rag_eval/run_eval.py                        # 開發用題目（rag_eval/questions.json）
python rag_eval/run_eval.py --file holdout.json    # 保留測試題，不要照著它調 prompt
python rag_eval/run_eval.py --router-only          # 只測挑文件那一步，快又便宜
python rag_eval/check_retrieval.py                 # 只看初篩有沒有把標準文件排進前 60 份，不呼叫 LLM
```

使用的模型可以用環境變數換：`RAG_ROUTER_MODEL`（挑文件，預設 gpt-5.4、不推理，`RAG_ROUTER_REASONING=none`）、
`RAG_ANSWER_MODEL`（回答，預設 gpt-5.4）、`RAG_CARD_MODEL`（產生卡片，預設 gpt-5.4）、
`RAG_EMBEDDING_MODEL`（初篩的向量，預設 text-embedding-3-large），初篩留幾份是 `RAG_ROUTER_CANDIDATES`（預設 60），
每張卡片列幾題「能回答的問題」是 `RAG_ROUTER_ANSWERS`（預設 4，0 是全部列出，挑文件的 token 會多七成）。

### 獎學金推薦

獎學金推薦（agent 工具 `recommend_scholarships_for_me`）用的也是 `data/` 裡的文件，分成兩步：

1. **整理資格**：標題有「獎學金」「助學金」「獎勵」「補助」這類字的文件（申請表、舊版除外，約 150 份），
   每份請模型把資格拆成欄位：學制跟年級、限定的學院或系所、成績跟排名門檻、要不要各科及格、身分條件
   （清寒、原住民、身心障礙、語言檢定等）、金額、申請期間。結果快取在 `storage/rag/scholarships/`，
   文件沒變就不會重做。同一個獎學金出現在好幾份文件時合併成一筆：資格以辦法為準，金額、名額跟截止日以
   最新的〈各項獎學金一覽表〉為準。
2. **比對**：查詢時拿成績單上的資料逐條比對，不呼叫模型。成績單看得出來的條件都符合的列成「符合資格」，
   成績符合但有身分條件、或資料不夠判斷的列成「還要確認條件」，其他的不列出。

第一次要整理一百多份文件（約 5 分鐘、會花 OpenAI 的費用），先手動跑一次，不然第一個查詢要等：

```powershell
python -m backend.analysis.scholarship_tools          # 只整理新增、改版的文件
python -m backend.analysis.scholarship_tools --list   # 列出整理好的每一項獎學金，檢查整理得對不對
```

改了整理資格的 prompt 或欄位要把 `scholarship_tools.py` 的 `SCHOLARSHIP_VERSION` 加一，舊快取才會失效。
整理用的模型是 `SCHOLARSHIP_MODEL`（預設 gpt-5.4）。限制：成績單上沒有操行成績，操行門檻只會提醒使用者
自己確認；成績單只有學期排名，「前一學年排名」用上下學期的排名判斷，兩學期一個在門檻內、一個不在時會列成要確認。

## 測試

```powershell
# 後端（pytest，在專案根目錄跑，會跑 tests/ 底下全部的測試）
python -m pytest

# 前端（Vitest）
cd frontend
npm test
```

這兩組測試在 GitHub 上也會自動跑：每個 PR、還有併進 `main` 之後（`.github/workflows/tests.yml`），
結果顯示在 PR 下方的檢查。CI 裡只有 `requirements.txt` 列的套件，加了新套件要記得寫進去，
不然 CI 會失敗。新的測試檔放在 `tests/`、檔名用 `test_` 開頭就會自動被跑到（設定在 `pytest.ini`）。

改了 agent 的系統提示、工具說明（`agent_tools.py` 的 docstring）或 supervisor 用的模型之後，
跑 `python agent_eval/run_routing.py` 檢查 agent 會不會選對工具（只看選了哪個工具，不會真的
執行，不用登入，但需要 `OPENAI_API_KEY`）。

`tests/manual/` 底下的 `test_hours_activity.py`（活動查詢、時數、報名）跟 `course_search.py`（選課搜尋）
是直接執行的腳本（例如 `python tests/manual/course_search.py`），登入的部分需要 `.env` 裡有
`NCU_USERNAME`/`NCU_PASSWORD`，`python -m pytest` 不會跑它們，用法看檔案開頭的說明。
