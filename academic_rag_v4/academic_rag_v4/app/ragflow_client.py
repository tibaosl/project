from __future__ import annotations

from typing import Any
import requests

from .config import RAGFLOW_API_KEY, RAGFLOW_BASE_URL, RAGFLOW_CHAT_ID


class RAGFlowError(RuntimeError):
    pass


class RAGFlowClient:
    def __init__(
        self,
        base_url: str = RAGFLOW_BASE_URL,
        api_key: str = RAGFLOW_API_KEY,
        chat_id: str = RAGFLOW_CHAT_ID,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RAGFlowError("RAGFLOW_API_KEY 尚未設定")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def health(self) -> dict[str, Any]:
        # RAGFlow exposes system health endpoints; this is only a connectivity probe.
        r = requests.get(
            f"{self.base_url}/api/v1/system/healthz",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def create_session(self, name: str = "academic-api") -> str:
        r = requests.post(
            f"{self.base_url}/api/v1/chats/{self.chat_id}/sessions",
            headers=self.headers,
            json={"name": name},
            timeout=30,
        )
        self._raise_for_status(r)
        data = r.json().get("data") or {}
        session_id = data.get("id")
        if not session_id:
            raise RAGFlowError(f"RAGFlow 沒有回傳 session id: {r.text[:1000]}")
        return session_id

    def ask(
        self,
        question: str,
        session_id: str | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        if not self.chat_id:
            raise RAGFlowError("RAGFLOW_CHAT_ID 尚未設定")

        if session_id is None:
            session_id = self.create_session()

        payload = {
            "question": question,
            "stream": stream,
            "session_id": session_id,
        }

        r = requests.post(
            f"{self.base_url}/api/v1/chats/{self.chat_id}/completions",
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        self._raise_for_status(r)
        body = r.json()

        return {
            "session_id": session_id,
            "raw": body,
            "answer": self._extract_answer(body),
            "sources": self._extract_sources(body),
        }

    @staticmethod
    def _raise_for_status(r: requests.Response) -> None:
        if r.ok:
            return
        raise RAGFlowError(
            f"RAGFlow HTTP {r.status_code}: {r.text[:2000]}"
        )

    @staticmethod
    def _extract_answer(body: dict[str, Any]) -> str:
        data = body.get("data") or {}

        # Current RAGFlow chat completion responses commonly expose answer
        # under data.answer; retain fallbacks for minor API variations.
        for obj in (data, body):
            if isinstance(obj, dict):
                for key in ("answer", "content", "text"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()

        return ""

    @staticmethod
    def _extract_sources(body: dict[str, Any]) -> list[dict[str, Any]]:
        data = body.get("data") or {}
        candidates = []

        for obj in (data, body):
            if not isinstance(obj, dict):
                continue
            for key in ("reference", "references", "chunks", "sources"):
                value = obj.get(key)
                if isinstance(value, list):
                    candidates.extend(x for x in value if isinstance(x, dict))

        # De-duplicate without assuming a fixed RAGFlow reference schema.
        seen = set()
        result = []
        for item in candidates:
            marker = json_safe_key(item)
            if marker not in seen:
                seen.add(marker)
                result.append(item)
        return result


def json_safe_key(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
