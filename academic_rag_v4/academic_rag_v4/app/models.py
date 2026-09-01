from __future__ import annotations

from pydantic import BaseModel, Field


class AcademicQuery(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class AcademicResponse(BaseModel):
    answer: str
    session_id: str
    sources: list[dict] = []
