from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.agents import collections_agent as agent
from app.db import get_session
from app.models.tables import AppMeta, DraftStatus, EmailDraft
from app.tools import analytics as an
from app.tools import collections_tools as col

router = APIRouter(prefix="/api/collections", tags=["collections"])


class DraftEdit(BaseModel):
    subject: str
    body: str


def _require_data(session: Session) -> None:
    if session.get(AppMeta, "opening_balance") is None:
        raise HTTPException(409, "No data loaded. Call POST /api/ingest/sample first.")


@router.get("/priorities")
def priorities(sims: int = Query(500, ge=100, le=3000),
               session: Session = Depends(get_session)) -> dict:
    """Who to chase first. Pure rules and the forecast, no LLM involved."""
    _require_data(session)
    prio, as_of = agent.build_priorities(session, sims)
    held = prio[prio["hold_reason"].notna()]
    return dict(as_of=as_of.isoformat(), customers=col.group_by_customer(prio),
                held=an.df_to_records(held))


@router.post("/drafts")
def create_drafts(top: int = Query(5, ge=1, le=20), use_llm: bool = True,
                  session: Session = Depends(get_session)) -> dict:
    """Write follow-up drafts for the top customers. Drafts are NEVER sent automatically."""
    _require_data(session)
    return agent.generate_drafts(session, top=top, use_llm=use_llm)


@router.get("/drafts")
def list_drafts(status: Literal["draft", "approved", "rejected", "sent", "all"] = "draft",
                session: Session = Depends(get_session)) -> list[dict]:
    q = select(EmailDraft).order_by(EmailDraft.created_at.desc())
    if status != "all":
        q = q.where(EmailDraft.status == DraftStatus(status))
    return [agent.draft_view(session, d) for d in session.exec(q).all()]


def _run(session: Session, fn, *args) -> dict:
    try:
        return agent.draft_view(session, fn(session, *args))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except agent.WorkflowError as e:
        raise HTTPException(409, str(e))


@router.put("/drafts/{draft_id}")
def edit(draft_id: int, payload: DraftEdit, session: Session = Depends(get_session)) -> dict:
    return _run(session, agent.edit_draft, draft_id, payload.subject, payload.body)


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, session: Session = Depends(get_session)) -> dict:
    """Approve only marks the draft ready. You send it yourself (see the mailto link)."""
    return _run(session, agent.move_draft, draft_id, DraftStatus.APPROVED)


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int, session: Session = Depends(get_session)) -> dict:
    return _run(session, agent.move_draft, draft_id, DraftStatus.REJECTED)


@router.post("/drafts/{draft_id}/mark-sent")
def mark_sent(draft_id: int, session: Session = Depends(get_session)) -> dict:
    """Record that you sent an approved draft."""
    return _run(session, agent.move_draft, draft_id, DraftStatus.SENT)