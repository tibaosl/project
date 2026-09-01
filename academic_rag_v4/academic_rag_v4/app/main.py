from fastapi import FastAPI, HTTPException

from .config import EMPTY_RESPONSE
from .models import AcademicQuery, AcademicResponse
from .ragflow_client import RAGFlowClient, RAGFlowError

app = FastAPI(
    title="NCUXplore Academic RAG v4",
    version="0.1.0",
    description="Thin Academic API over RAGFlow. RAG is intentionally delegated to RAGFlow.",
)

client = RAGFlowClient()


@app.get("/health")
def health():
    try:
        result = client.health()
        return {"ok": True, "ragflow": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/academic/query", response_model=AcademicResponse)
def academic_query(req: AcademicQuery):
    try:
        result = client.ask(req.query, session_id=req.session_id)
    except RAGFlowError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    answer = result["answer"].strip()
    if not answer:
        answer = EMPTY_RESPONSE

    return AcademicResponse(
        answer=answer,
        session_id=result["session_id"],
        sources=result["sources"],
    )
