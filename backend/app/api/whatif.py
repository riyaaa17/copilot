import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.models.tables import AppMeta
from app.tools import analytics as an
from app.tools import forecast as fx
from app.tools import whatif as wi

router = APIRouter(prefix="/api/whatif", tags=["what-if"])


class WhatIfRequest(BaseModel):
    effects: list[wi.Effect] = Field(min_length=1, max_length=8)


def _require_data(session: Session) -> None:
    if session.get(AppMeta, "opening_balance") is None:
        raise HTTPException(409, "No data loaded. Call POST /api/ingest/sample first.")


def _customers(session: Session):
    """Customers with open receivables, most overdue first."""
    inv, as_of = an.load_invoices(session), an.get_as_of(session)
    live = inv[~inv["id"].isin(fx.flagged_invoice_ids(session, "duplicate"))]
    names = an.load_names(session)
    open_ar = live[(live["type"] == "AR") & (live["status"] == "open")]
    rows = []
    for cid, g in open_ar.groupby("counterparty_id"):
        overdue = g.loc[g["due_date"] < pd.Timestamp(as_of), "amount"].sum()
        rows.append(dict(id=int(cid), name=names.get(int(cid), str(cid)),
                         open=round(float(g["amount"].sum()), 2), overdue=round(float(overdue), 2)))
    return sorted(rows, key=lambda r: (-r["overdue"], -r["open"]))


def _categories(bank: pd.DataFrame) -> list[str]:
    return sorted(c for c in bank["category"].dropna().unique() if c not in fx.INVOICE_CATEGORIES)


@router.get("/options")
def options(session: Session = Depends(get_session)) -> dict:
    """What can be changed: customers with open invoices, and the recurring payments in the bank history."""
    _require_data(session)
    return dict(customers=_customers(session), recurring_categories=_categories(fx.load_bank(session)))


@router.post("")
def run(req: WhatIfRequest, session: Session = Depends(get_session)) -> dict:
    """Run the 13-week forecast with and without the changes, using the same random draws for both."""
    _require_data(session)
    inv, bank, as_of = an.load_invoices(session), fx.load_bank(session), an.get_as_of(session)
    customers = {c["id"]: c["name"] for c in _customers(session)}
    try:
        wi.validate_effects(req.effects, customers, _categories(bank))
    except ValueError as e:
        raise HTTPException(422, str(e))
    kwargs = dict(n_sims=1000, seed=42, exclude_ids=fx.flagged_invoice_ids(session, "duplicate"),
                  learn_exclude_ids=fx.flagged_invoice_ids(session, "outlier"))
    cash = an.cash_balance(session)
    base = fx.run_forecast(inv, bank, as_of, cash, **kwargs)
    alt = fx.run_forecast(inv, bank, as_of, cash, scenario=wi.build_scenario(req.effects), **kwargs)
    result = wi.compare(base, alt)
    return dict(as_of=as_of.isoformat(), assumptions=wi.describe(req.effects, an.load_names(session)),
                summary=wi.summary_sentence(result), **result)