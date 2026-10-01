from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.models.tables import AppMeta
from app.tools import analytics as an
from app.tools import forecast as fx

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


def _inputs(session: Session):
    if session.get(AppMeta, "opening_balance") is None:
        raise HTTPException(409, "No data loaded. Call POST /api/ingest/sample first.")
    return an.load_invoices(session), fx.load_bank(session), an.get_as_of(session)


def _run(session: Session, sims: int, seed: int) -> fx.Forecast:
    inv, bank, as_of = _inputs(session)
    return fx.run_forecast(
        inv, bank, as_of, an.cash_balance(session), sims, seed,
        exclude_ids=fx.flagged_invoice_ids(session, "duplicate"),
        learn_exclude_ids=fx.flagged_invoice_ids(session, "outlier"))


@router.get("")
def forecast(sims: int = Query(1000, ge=100, le=5000), seed: int = 42,
             session: Session = Depends(get_session)) -> dict:
    return _run(session, sims, seed).to_dict()


@router.get("/open-ar")
def open_ar(sims: int = Query(1000, ge=100, le=5000), seed: int = 42,
            session: Session = Depends(get_session)) -> list[dict]:
    """Per-invoice payment prediction for every unpaid customer invoice."""
    fc = _run(session, sims, seed)
    names = an.load_names(session)
    df = fc.open_ar.assign(customer=fc.open_ar["counterparty_id"].map(names))
    return an.df_to_records(df.sort_values("amount", ascending=False))


@router.get("/backtest")
def backtest(session: Session = Depends(get_session)) -> dict:
    inv, bank, as_of = _inputs(session)
    opening = float(session.get(AppMeta, "opening_balance").value)
    return fx.backtest(inv, bank, opening, as_of,
                       exclude_ids=fx.flagged_invoice_ids(session, "duplicate"),
                       learn_exclude_ids=fx.flagged_invoice_ids(session, "outlier"))