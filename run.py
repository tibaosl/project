"""一鍵啟動開發環境：後端（FastAPI）+ 前端（Vite dev server），並自動開瀏覽器。

用法：
    python run.py

第一次執行、`frontend/node_modules` 還不存在時，會先自動跑 `npm install`
（Node.js 要先裝好：https://nodejs.org，選 LTS 版本即可）。

舊的 Streamlit 原型（ui.py）還留著沒刪，這支腳本不會啟動它；真的想跑
Streamlit 版本，另外開一個終端機手動執行 `streamlit run ui.py` 即可。
"""

import os
import shutil
import subprocess
import sys
import time
import webbrowser

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")
FRONTEND_DEV_URL = "http://localhost:5173"

# Windows 上剛裝好 Node.js 時，新開的終端機通常就抓得到 PATH，但這支腳本
# 可能是從還沒重開過的終端機/IDE 執行，PATH 還沒刷新——所以額外檢查一下
# 官方安裝程式預設的安裝路徑，找不到 npm 時再退回單純靠 PATH（讓系統的
# 錯誤訊息說明白到底是真的沒裝，還是只是 PATH 沒刷新）。
_NPM_CANDIDATE_DIRS = [r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs"]


def _find_npm() -> str:
    found = shutil.which("npm")
    if found:
        return found
    for candidate_dir in _NPM_CANDIDATE_DIRS:
        candidate = os.path.join(candidate_dir, "npm.cmd" if sys.platform == "win32" else "npm")
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "找不到 npm，請先安裝 Node.js（https://nodejs.org，選 LTS 版本），"
        "裝完後重新開一個終端機再跑一次 `python run.py`。"
    )


def start():
    npm = _find_npm()

    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        print("[run.py] 第一次執行，正在安裝前端套件（npm install）...")
        subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=True)

    print("[run.py] 啟動後端（FastAPI, http://127.0.0.1:8000）...")
    backend_proc = subprocess.Popen([sys.executable, "main.py"], cwd=PROJECT_DIR)

    time.sleep(1.5)

    print(f"[run.py] 啟動前端（Vite dev server, {FRONTEND_DEV_URL}）...")
    frontend_proc = subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND_DIR)

    time.sleep(2)
    webbrowser.open(FRONTEND_DEV_URL)

    try:
        frontend_proc.wait()
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\n[run.py] 正在關閉所有服務...")
        frontend_proc.terminate()
        backend_proc.terminate()
        frontend_proc.wait()
        backend_proc.wait()


if __name__ == "__main__":
    start()
