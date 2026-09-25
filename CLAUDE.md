# CLAUDE.md

給 Claude Code 看的專案守則。細節（功能清單、怎麼跑專案、怎麼測試）請看 [README.md](README.md)，這裡只記「規則」跟「現況」，避免重複維護。

## 最重要的規則：branch 紀律

* **絕對不要直接在 `main` 上改東西、commit 或 push。** `main` 是整合後的主線，新功能要走 PR/merge 流程進來。
* 開始做任何修改前，先確認目前在哪個 branch（`git branch` / `git status`），如果在 `main` 上而使用者要開發功能，先跟使用者確認要不要切 branch、切去哪個。
* 目前的 branch 對應：
  * `main`：整合後主線，不要直接改。
  * `選課`：選課功能開發，**是另一位組員負責的範圍，Claude 不用主動去改這條 branch**。
  * `RAG`：RAG 相關功能開發，**目前暫緩**（一直修不好），先不要主動接手或修改這條 branch，除非使用者明確要求。
* commit / push 前，照系統既有的安全守則跟使用者確認（尤其是 push、以及任何會影響到共用 branch 的操作）。

## 開發環境重點

* Python 虛擬環境在 `venv/`，套件是直接裝在共用 venv 裡，沒有 `requirements.txt`。
* 專案根目錄的 `.env`（不受 git 追蹤）要有 `OPENAI_API_KEY`、`NCU_OAUTH_CLIENT_ID`/`NCU_OAUTH_CLIENT_SECRET`/`NCU_OAUTH_REDIRECT_URI` 才能用到 RAG 問答跟 OAuth 登入相關功能；沒有這些值專案仍能啟動，只是這些功能會不能用。
* 執行整個專案：`python run.py`（同時啟動 FastAPI 後端 `:8000` 跟 Vite 前端 `:5173`）。
* 測試：
  * 後端：`python -m pytest test_schedule_helpers.py test_oauth_portal.py`
  * 前端：`cd frontend && npm test`
  * `test_agent_tools_schema.py`、`test_hours_activity.py` 需要 `.env` 裡有真的 `OPENAI_API_KEY`（甚至帳密）才能跑，一般改動不一定用得到。

## 現況（2026-09-22）

* `main` 剛在 2026-09-21 把前端重寫、OAuth 登入、活動功能等 work 併回來。
* `選課` branch 還在開發中（另一位組員負責），尚未併回 `main`，Claude 不用管。
* `RAG` branch 暫緩開發（bug 一直修不好），先不要主動處理。
* 專題企劃書（NCUXplore 智慧校園代理系統）裡規劃但還沒做的功能主要是「學業分析」跟「課表規劃」。「學業分析」正在 `學業分析` branch 開發（`academic_tools.py`，資料來自 iNCU 成績查詢 + 畢業資格審查表）；「課表規劃」尚未開始。
* Portal 登入常跳 reCAPTCHA，Playwright 內建瀏覽器連真人都過不了，所以改開真正的 Chrome 讓使用者自己登入再用 CDP 接管。網頁的主要登入是 `/api/login/chrome`：同一個 Chrome 登入 Portal 後接著跑官方 OAuth 拿身分（帳號以官方 API 為準），一次完成身分驗證跟啟用功能；舊的「OAuth 整頁導向 + 另外補密碼」流程已移除。這只適用本機，將來架伺服器要改走官方資料 API 或瀏覽器擴充功能，屆時只需要換登入/抓資料這層，解析跟分析（`academic_tools.py` 等）不用動。登入狀態可以存在 `%LOCALAPPDATA%\NCUXplore` 重複用，但只有 `NCUSession(reuse_saved_state=True)` 才會用，`/api/login` 這種驗證身分的入口不能開（會變成拿錯密碼也能登入）。
* Claude Code CLI 已經整合進這台機器的 VS Code（裝了官方擴充功能），開發時可以直接在 VS Code 側邊欄跟 Claude Code 對話，不用額外開終端機。
