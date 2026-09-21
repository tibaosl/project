# NCUXplore

中央大學校園智慧助手聊天機器人。使用者用自然語言問問題（法規、課表、時數、活動...），
後端的 LangGraph agent 自己判斷該查什麼資料、該用哪個工具，答案用串流的方式即時顯示在
前端網頁上。

## 目前有的功能

* **校園法規問答**：RAG 檢索中央大學的法規/辦法文件，回答附上來源出處（`academic_agent.py`）。
* **登入**：兩種方式擇一，登入後給一個一次性通行證（token），不會每次對話都重傳密碼。
  * 用中央大學 Portal 官方 OAuth2 登入（推薦，我們的伺服器完全看不到密碼）。
  * 或手動輸入 Portal 帳號密碼登入。
  * 只查法規/活動這類公開資訊不需要登入；要查「自己的」課表、時數、活動報名紀錄，
    或要送出報名/取消報名，才需要登入。
* **個人課表查詢**、**選課系統關鍵字搜尋**（`action_tools.py`）。
* **學習護照時數進度查詢**：對照畢業門檻，算出各類別還差多少小時。
* **活動查詢與推薦**：關鍵字搜尋、依「時數缺口」自動推薦活動、依時數標籤查詢，都會過濾掉
  報名時間已經截止的場次（`activity_tools.py`）。
* **查詢自己的活動報名紀錄**、**活動報名/取消報名**：報名/取消一定要使用者在對話裡明確回覆
  「確定」才會真的送出，不會被機器人自己誤觸發。

## 開發注意事項

目前 GitHub 有三個主要 branch：

* `main`：整合後的主線，這次 (2026-09-21) 剛把之前累積的前端重寫、OAuth 登入、活動功能等
  work 併回來，之後新功能開發完、測試過還是要走 PR/merge 的流程進來，**不要直接在這裡改**
* `選課`：選課功能開發，之後要改選課相關程式請在這個 branch 修改
* `RAG`：RAG 相關功能

## 如果要做選課功能

第一次使用：

```powershell
git fetch origin
git switch 選課
```

之後每次開始開發前：

```powershell
git switch 選課
git pull origin 選課
```

修改完、測試沒問題後：

```powershell
git status
git add .
git commit -m "描述這次修改"
git push origin 選課
```

## 如果只是要執行專案

前置需求：

* Python 虛擬環境（`venv/`）已經裝好需要的套件（沒有 `requirements.txt`，套件是直接裝在
  共用的 `venv` 裡，不確定裝了什麼可以直接 `pip list` 看）。
* 專案根目錄要有一個 `.env` 檔（不會被 git 追蹤，跟別人要或自己另外設定），至少要有：
  * `OPENAI_API_KEY`：法規問答（RAG）用。
  * `NCU_OAUTH_CLIENT_ID` / `NCU_OAUTH_CLIENT_SECRET` / `NCU_OAUTH_REDIRECT_URI`：
    Portal OAuth 登入用，去 portal.ncu.edu.tw 登入後在「應用系統管理」頁面申請取得。
  * 沒有這些值也能啟動，只是登入相關功能會不能用。
* [Node.js](https://nodejs.org)（選 LTS 版本）：前端是用 React + Vite，第一次執行會自動
  幫你 `npm install`，但要先裝好 Node.js 本身。

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

## 測試

```powershell
# 後端（pytest）
python -m pytest test_schedule_helpers.py test_oauth_portal.py

# 前端（Vitest）
cd frontend
npm test
```

`test_agent_tools_schema.py`、`test_hours_activity.py` 這兩個需要 `.env` 裡有真的
`OPENAI_API_KEY`（甚至 `NCU_USERNAME`/`NCU_PASSWORD`）才能跑，一般開發改動前端/一般工具
邏輯不一定用得到，看檔案開頭的說明。

## 簡單記

```text
不要直接改 main ❌

要做選課 → 切到「選課」branch
要做 RAG  → 切到「RAG」branch

選課開發：
git switch 選課
git pull --ff-only origin 選課
    ↓
修改 / 測試
    ↓
git add .
git commit -m "描述修改"
git push origin 選課
```
