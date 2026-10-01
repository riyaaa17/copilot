import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, select

from app.agents import reporting_agent as agent
from app.db import get_session
from app.models.tables import Briefing

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _view(b: Briefing, full: bool = True) -> dict:
    out = dict(briefing_id=b.id, as_of=b.as_of.isoformat(), source=b.source, warnings=b.warnings,
               created_at=b.created_at.isoformat())
    if full:
        out.update(markdown=b.markdown, data=json.loads(b.data_json))
    return out


@router.post("/weekly")
def create_weekly(use_llm: bool = True, session: Session = Depends(get_session)) -> dict:
    """Build this week's CFO briefing. All figures are computed; the LLM only words two paragraphs."""
    try:
        return _view(agent.generate_briefing(session, use_llm=use_llm))
    except LookupError as e:
        raise HTTPException(409, str(e))


@router.get("")
def list_briefings(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(Briefing).order_by(Briefing.created_at.desc())).all()
    return [_view(b, full=False) for b in rows]


@router.get("/{briefing_id}")
def get_briefing(briefing_id: int, session: Session = Depends(get_session)) -> dict:
    b = session.get(Briefing, briefing_id)
    if b is None:
        raise HTTPException(404, "Briefing not found")
    return _view(b)


@router.get("/{briefing_id}/markdown", response_class=PlainTextResponse)
def get_markdown(briefing_id: int, session: Session = Depends(get_session)) -> str:
    """The briefing as plain text, easy to read in the browser or paste into an email."""
    b = session.get(Briefing, briefing_id)
    if b is None:
        raise HTTPException(404, "Briefing not found")
    return b.markdown