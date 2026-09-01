import os
from dotenv import load_dotenv

load_dotenv()

RAGFLOW_BASE_URL = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "").strip()
RAGFLOW_CHAT_ID = os.getenv("RAGFLOW_CHAT_ID", "").strip()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

EMPTY_RESPONSE = os.getenv(
    "RAGFLOW_EMPTY_RESPONSE",
    "目前檢索到的校規文件不足以確認這個問題。",
)
