from typing import Literal

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session
from app.tools import analytics as an
from app.tools import forecast as fx

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _live_invoices(session: Session):
    """Invoices that are real: flagged duplicates (unless a person dismissed the flag) are left out,
    so these figures agree with the forecast, the briefing and the chat."""
    inv = an.load_invoices(session)
    return inv[~inv["id"].isin(fx.flagged_invoice_ids(session, "duplicate"))]


@router.get("/aging")
def aging(kind: Literal["AR", "AP"] = "AR", session: Session = Depends(get_session)) -> dict:
    return an.aging_report(_live_invoices(session), an.get_as_of(session), kind)


@router.get("/kpis")
def kpis(session: Session = Depends(get_session)) -> dict:
    inv, as_of = _live_invoices(session), an.get_as_of(session)
    ar, ap = an.aging_report(inv, as_of, "AR"), an.aging_report(inv, as_of, "AP")
    return dict(as_of=as_of.isoformat(), cash_balance=an.cash_balance(session),
                open_ar=ar["total_open"], overdue_ar=ar["total_overdue"],
                overdue_ar_pct=ar["overdue_pct"], open_ap=ap["total_open"],
                **an.working_capital_days(inv, as_of))


@router.get("/customers")
def customers(session: Session = Depends(get_session)) -> list[dict]:
    inv = _live_invoices(session)
    return an.df_to_records(an.customer_profiles(inv, an.load_names(session),
                                                 an.get_as_of(session)))