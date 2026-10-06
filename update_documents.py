"""法規問答的文件每週自動更新：重抓學校網站（crawler.py），再幫新增、改版的文件補卡片跟向量
（rag_documents.load_catalog），最後整理新增、改版的獎學金文件的申請資格（scholarship_tools.py）。
卡片跟獎學金資格先建好，網站開著的話下一次查詢載入新目錄只要幾秒，不用讓使用者等模型整理。
Windows 的工作排程器每週跑一次（設定方式見 README「法規文件每週自動更新」），也可以自己跑：

    python update_documents.py

每次的紀錄存在 storage/update_logs/（一次一個檔案，只留最近 KEEP_LOGS 份）。前一步失敗就不做後面的步驟。
費用：重抓不花錢，只有新增或改版的文件要呼叫模型產生卡片、整理獎學金資格（平常一週幾份到幾十份）。
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "storage" / "update_logs"
KEEP_LOGS = 12

STEPS = [
    ("重抓學校網站", [sys.executable, "crawler.py"]),
    ("補文件卡片跟向量", [
        sys.executable, "-c",
        "import rag_documents as r; c = r.load_catalog(max_workers=8, processes=4); print(f'目錄共 {len(c)} 份文件')",
    ]),
    ("整理獎學金資格", [sys.executable, "scholarship_tools.py"]),
]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.log"
    # 工作排程器用 pythonw 跑的時候沒有主控台，輸出一律寫進紀錄檔
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    code = 0
    with log_path.open("w", encoding="utf-8") as log:
        for name, command in STEPS:
            log.write(f"== {datetime.now():%Y-%m-%d %H:%M:%S} {name}\n")
            log.flush()
            code = subprocess.run(command, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, env=env).returncode
            log.flush()
            if code != 0:
                log.write(f"== {name}失敗（結束代碼 {code}），後面的步驟不做\n")
                break
        else:
            log.write(f"== {datetime.now():%Y-%m-%d %H:%M:%S} 完成\n")
    for old in sorted(LOG_DIR.glob("*.log"))[:-KEEP_LOGS]:
        old.unlink()
    return code


if __name__ == "__main__":
    sys.exit(main())
