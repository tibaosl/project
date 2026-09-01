from __future__ import annotations

import argparse
from pathlib import Path
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
API_KEY = os.getenv("RAGFLOW_API_KEY", "").strip()


def headers():
    if not API_KEY:
        raise SystemExit("請先設定 RAGFLOW_API_KEY")
    return {"Authorization": f"Bearer {API_KEY}"}


def create_dataset(name: str) -> str:
    r = requests.post(
        f"{BASE_URL}/api/v1/datasets",
        headers={**headers(), "Content-Type": "application/json"},
        json={"name": name},
        timeout=30,
    )
    if not r.ok:
        raise SystemExit(f"建立 dataset 失敗: {r.status_code} {r.text}")
    data = r.json().get("data") or {}
    dataset_id = data.get("id")
    if not dataset_id:
        raise SystemExit(f"建立 dataset 沒有回傳 id: {r.text}")
    return dataset_id


def upload(dataset_id: str, path: Path):
    with path.open("rb") as f:
        r = requests.post(
            f"{BASE_URL}/api/v1/datasets/{dataset_id}/documents",
            headers=headers(),
            files={"file": (path.name, f)},
            timeout=120,
        )
    if not r.ok:
        raise RuntimeError(f"{path}: {r.status_code} {r.text[:1000]}")
    return r.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="../data")
    parser.add_argument("--dataset-name", default="NCU Academic Regulations")
    parser.add_argument("--dataset-id", default="")
    args = parser.parse_args()

    data_dir = Path(args.data).resolve()
    dataset_id = args.dataset_id or create_dataset(args.dataset_name)

    files = [
        p for p in data_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt", ".md", ".xlsx", ".xls", ".csv"}
    ]

    print(f"Dataset: {dataset_id}")
    print(f"Files: {len(files)}")

    for path in files:
        try:
            result = upload(dataset_id, path)
            print(f"[OK] {path.name}: {result}")
        except Exception as exc:
            print(f"[FAIL] {path}: {exc}")

    print()
    print("下一步：進 RAGFlow UI 檢查文件，選擇 chunk/embedding 設定並啟動 parsing。")
    print("完成後建立 Chat assistant，綁定這個 dataset，再把 chat ID 寫入 .env 的 RAGFLOW_CHAT_ID。")


if __name__ == "__main__":
    main()
