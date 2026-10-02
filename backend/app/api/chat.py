from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.agents import orchestrator
from app.db import get_session
from app.models.tables import AppMeta

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = []
    use_llm: bool = True


@router.post("")
def chat(req: ChatRequest, session: Session = Depends(get_session)) -> dict:
    """Ask the CFO assistant. Send earlier turns in `history` for follow-up questions."""
    if session.get(AppMeta, "opening_balance") is None:
        raise HTTPException(409, "No data loaded. Call POST /api/ingest/sample first.")
    return orchestrator.answer_question(session, req.message, [t.model_dump() for t in req.history],
                                        use_llm=req.use_llm)