from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models.tables import AnomalyFlag, Invoice
from app.tools import analytics as an
from app.tools.anomalies import save_flags, scan

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


def _view(session: Session, flags: list[AnomalyFlag]) -> list[dict]:
    ids = {f.invoice_id for f in flags}
    invs = {i.id: i for i in session.exec(select(Invoice).where(Invoice.id.in_(ids))).all()} \
        if ids else {}
    names = an.load_names(session)
    out = []
    for f in flags:
        i = invs.get(f.invoice_id)
        out.append(dict(
            flag_id=f.id, kind=f.kind, severity=f.severity, score=f.score, status=f.status,
            explanation=f.explanation, invoice_id=f.invoice_id,
            related_invoice_id=f.related_invoice_id,
            invoice_no=i.invoice_no if i else None, type=i.type.value if i else None,
            counterparty=names.get(i.counterparty_id) if i else None,
            amount=i.amount if i else None,
            issue_date=i.issue_date.isoformat() if i else None))
    return out


@router.post("/scan")
def run_scan(session: Session = Depends(get_session)) -> dict:
    """Detect duplicates and outliers. Earlier review decisions are kept."""
    inv = an.load_invoices(session)
    if inv.empty:
        raise HTTPException(409, "No data loaded. Call POST /api/ingest/sample first.")
    flags = scan(inv, an.load_names(session))
    save_flags(session, flags)
    stored = session.exec(select(AnomalyFlag).order_by(AnomalyFlag.score.desc())).all()
    return dict(total=len(stored),
                duplicates=sum(f.kind == "duplicate" for f in stored),
                outliers=sum(f.kind == "outlier" for f in stored),
                flags=_view(session, stored))


@router.get("")
def list_flags(status: Literal["open", "confirmed", "dismissed", "all"] = "open",
               session: Session = Depends(get_session)) -> list[dict]:
    q = select(AnomalyFlag).order_by(AnomalyFlag.score.desc())
    if status != "all":
        q = q.where(AnomalyFlag.status == status)
    return _view(session, session.exec(q).all())


@router.post("/{flag_id}/review")
def review(flag_id: int, status: Literal["confirmed", "dismissed"],
           session: Session = Depends(get_session)) -> dict:
    """Human decision. Dismissed flags no longer affect the forecast."""
    flag = session.get(AnomalyFlag, flag_id)
    if flag is None:
        raise HTTPException(404, "Flag not found")
    flag.status = status
    session.add(flag)
    session.commit()
    return dict(flag_id=flag_id, status=status)