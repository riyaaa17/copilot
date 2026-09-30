"""Deterministic working-capital analytics.

The maths lives in pure functions that take DataFrames, so they are easy to test.
The small loader functions at the top are the only parts that touch the database.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
from sqlmodel import Session, func, select

from app.models.tables import AppMeta, BankTransaction, Counterparty, Invoice

INVOICE_COLS = ["id", "invoice_no", "type", "counterparty_id", "issue_date", "due_date",
                "paid_date", "amount", "category", "status"]
BUCKET_ORDER = ["Not yet due", "1-30", "31-60", "61-90", "90+"]
MIN_PAID_FOR_PROFILE = 3


# ---------- loaders (database access) ----------
def get_as_of(session: Session) -> date:
    """The 'today' of the dataset, so results stay consistent with the loaded data."""
    row = session.get(AppMeta, "as_of")
    return date.fromisoformat(row.value) if row else date.today()


def cash_balance(session: Session) -> float | None:
    opening = session.get(AppMeta, "opening_balance")
    if opening is None:
        return None
    flow = session.exec(select(func.sum(BankTransaction.amount))).one() or 0.0
    return round(float(opening.value) + float(flow), 2)


def load_invoices(session: Session) -> pd.DataFrame:
    rows = [dict(id=r.id, invoice_no=r.invoice_no, type=r.type.value,
                 counterparty_id=r.counterparty_id, issue_date=r.issue_date,
                 due_date=r.due_date, paid_date=r.paid_date, amount=r.amount,
                 category=r.category, status=r.status.value)
            for r in session.exec(select(Invoice)).all()]
    df = pd.DataFrame(rows, columns=INVOICE_COLS)
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def load_names(session: Session) -> dict[int, str]:
    return {c.id: c.name for c in session.exec(select(Counterparty)).all()}


# ---------- pure analytics ----------
def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "Not yet due"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def aging_report(inv: pd.DataFrame, as_of: date, kind: str = "AR") -> dict:
    """Open invoices grouped by how many days past due they are."""
    open_ = inv[(inv["type"] == kind) & (inv["status"] == "open")].copy()
    open_["days_overdue"] = (pd.Timestamp(as_of) - open_["due_date"]).dt.days
    open_["bucket"] = open_["days_overdue"].map(_bucket)
    total = float(open_["amount"].sum())
    buckets = []
    for name in BUCKET_ORDER:
        sub = open_[open_["bucket"] == name]
        amt = float(sub["amount"].sum())
        buckets.append(dict(bucket=name, count=int(len(sub)), amount=round(amt, 2),
                            pct=round(100 * amt / total, 1) if total else 0.0))
    overdue = float(open_.loc[open_["days_overdue"] > 0, "amount"].sum())
    return dict(as_of=as_of.isoformat(), type=kind, buckets=buckets,
                total_open=round(total, 2), total_overdue=round(overdue, 2),
                overdue_pct=round(100 * overdue / total, 1) if total else 0.0)


def working_capital_days(inv: pd.DataFrame, as_of: date, window_days: int = 90) -> dict:
    """DSO = open AR / AR billed in the window x window. DPO is the same for AP."""
    asof = pd.Timestamp(as_of)
    cutoff = asof - pd.Timedelta(days=window_days)
    out: dict = {"window_days": window_days}
    for kind, label in (("AR", "dso"), ("AP", "dpo")):
        sub = inv[inv["type"] == kind]
        open_bal = float(sub.loc[sub["status"] == "open", "amount"].sum())
        billed = float(sub.loc[(sub["issue_date"] > cutoff) & (sub["issue_date"] <= asof),
                               "amount"].sum())
        out[label] = round(open_bal / billed * window_days, 1) if billed else None

    paid = inv[(inv["type"] == "AR") & (inv["status"] == "paid")]
    if len(paid) and paid["amount"].sum() > 0:
        w = paid["amount"]
        days_to_pay = (paid["paid_date"] - paid["issue_date"]).dt.days
        days_late = (paid["paid_date"] - paid["due_date"]).dt.days
        out["avg_days_to_pay"] = round(float((days_to_pay * w).sum() / w.sum()), 1)
        out["avg_days_beyond_terms"] = round(float((days_late * w).sum() / w.sum()), 1)
    else:
        out["avg_days_to_pay"] = out["avg_days_beyond_terms"] = None
    return out


def _r(x) -> float | None:
    return None if pd.isna(x) else round(float(x), 1)


def payer_label(mean_late: float, p90_late: float) -> str:
    if mean_late <= 2 and p90_late <= 10:
        return "prompt"
    if mean_late <= 12:
        return "average"
    if mean_late <= 30:
        return "slow"
    return "chronic"


def customer_profiles(inv: pd.DataFrame, names: dict[int, str], as_of: date) -> pd.DataFrame:
    """How each customer actually pays, learned only from invoice history.

    Invoices that are still open and already overdue are included as lower-bound
    observations ("at least this late"). Ignoring them would flatter slow payers,
    because their late invoices are exactly the ones that haven't been paid yet.
    """
    asof = pd.Timestamp(as_of)
    rows = []
    for cid, grp in inv[inv["type"] == "AR"].groupby("counterparty_id"):
        paid = grp[grp["status"] == "paid"]
        late = (paid["paid_date"] - paid["due_date"]).dt.days
        opn = grp[grp["status"] == "open"]
        overdue_days = (asof - opn["due_date"]).dt.days
        is_overdue = overdue_days > 0
        adj = pd.concat([late, overdue_days[is_overdue]])
        n_paid = len(late)
        if n_paid >= MIN_PAID_FOR_PROFILE and len(adj):
            label = payer_label(float(adj.mean()), float(adj.quantile(0.9)))
        else:
            label = "insufficient_data"
        rows.append(dict(
            counterparty_id=int(cid), name=names.get(int(cid), str(cid)),
            payment_behavior=label, n_paid=n_paid,
            avg_days_late=_r(late.mean()), median_days_late=_r(late.median()),
            p90_days_late=_r(late.quantile(0.9)) if n_paid else None,
            pct_paid_late=_r((late > 0).mean() * 100) if n_paid else None,
            avg_days_late_adjusted=_r(adj.mean()) if len(adj) else None,
            open_count=int(len(opn)), open_amount=round(float(opn["amount"].sum()), 2),
            overdue_amount=round(float(opn.loc[is_overdue, "amount"].sum()), 2),
            max_days_overdue=int(overdue_days.max()) if is_overdue.any() else 0,
        ))
    cols = ["counterparty_id", "name", "payment_behavior", "n_paid", "avg_days_late",
            "median_days_late", "p90_days_late", "pct_paid_late", "avg_days_late_adjusted",
            "open_count", "open_amount", "overdue_amount", "max_days_overdue"]
    return (pd.DataFrame(rows, columns=cols)
            .sort_values("overdue_amount", ascending=False).reset_index(drop=True))


def df_to_records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records (NaN becomes null)."""
    return json.loads(df.to_json(orient="records"))