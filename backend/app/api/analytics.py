from typing import Literal

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session
from app.tools import analytics as an

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/aging")
def aging(kind: Literal["AR", "AP"] = "AR", session: Session = Depends(get_session)) -> dict:
    return an.aging_report(an.load_invoices(session), an.get_as_of(session), kind)


@router.get("/kpis")
def kpis(session: Session = Depends(get_session)) -> dict:
    inv, as_of = an.load_invoices(session), an.get_as_of(session)
    ar, ap = an.aging_report(inv, as_of, "AR"), an.aging_report(inv, as_of, "AP")
    return dict(as_of=as_of.isoformat(), cash_balance=an.cash_balance(session),
                open_ar=ar["total_open"], overdue_ar=ar["total_overdue"],
                overdue_ar_pct=ar["overdue_pct"], open_ap=ap["total_open"],
                **an.working_capital_days(inv, as_of))


@router.get("/customers")
def customers(session: Session = Depends(get_session)) -> list[dict]:
    inv = an.load_invoices(session)
    return an.df_to_records(an.customer_profiles(inv, an.load_names(session),
                                                 an.get_as_of(session)))