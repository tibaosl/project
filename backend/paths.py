"""專案裡的資料夾位置。程式碼都在 backend/ 底下，資料跟快取放在專案根目錄的 data/、storage/。"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"        # 爬蟲抓回來的學校文件（不在 git 裡）
STORAGE_DIR = PROJECT_DIR / "storage"  # 快取跟執行紀錄（不在 git 裡）
