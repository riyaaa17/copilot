"""13-week cash forecast: driver-based Monte Carlo. Deterministic given a seed, no LLM.

Cash in/out is built from four drivers:
  1. Open receivables  - when will each unpaid customer invoice be paid?
  2. Open payables     - when will each unpaid vendor bill be paid?
  3. New billings      - invoices not yet issued (else the forecast only ever shrinks)
  4. Recurring items   - payroll, rent, tax, fees, detected from bank history
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from app.models.tables import AnomalyFlag, BankTransaction

HORIZON_WEEKS = 13
HORIZON_DAYS = 7 * HORIZON_WEEKS
SMOOTH_K = 5  # pseudo-count: how quickly we trust a customer's own history over the portfolio
INVOICE_CATEGORIES = {"customer_receipt", "vendor_payment"}


# ---------- loaders ----------
def load_bank(session: Session) -> pd.DataFrame:
    rows = [dict(txn_date=r.txn_date, amount=r.amount, category=r.category)
            for r in session.exec(select(BankTransaction)).all()]
    df = pd.DataFrame(rows, columns=["txn_date", "amount", "category"])
    df["txn_date"] = pd.to_datetime(df["txn_date"])
    return df


def flagged_invoice_ids(session: Session) -> set[int]:
    """Invoices the Anomaly Agent flagged; the forecast should not learn from them."""
    return {i for i in session.exec(select(AnomalyFlag.invoice_id)).all() if i is not None}


# ---------- payment-delay distributions ----------
def _km(obs: np.ndarray, paid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Kaplan-Meier estimate of 'days paid after due date'.

    obs  = actual delay for paid invoices, or 'days overdue so far' for unpaid ones.
    Unpaid invoices are right-censored: we only know they will be at least that late.
    Ignoring them would make everyone look like a faster payer than they are.
    Returns (delay values, probabilities); the last value is +inf = 'not paid yet, ever
    observed', which carries the leftover probability.
    """
    if len(obs) == 0:
        return np.array([np.inf]), np.array([1.0])
    surv, vals, probs = 1.0, [], []
    for t in np.unique(obs[paid]):
        n_risk = int((obs >= t).sum())
        events = int(((obs == t) & paid).sum())
        mass = surv * events / n_risk
        vals.append(t)
        probs.append(mass)
        surv -= mass
    vals.append(np.inf)
    probs.append(max(surv, 0.0))
    return np.array(vals, float), np.array(probs, float)


def _build_dists(inv: pd.DataFrame, kind: str, as_of: date):
    sub = inv[(inv["type"] == kind) & (inv["status"] != "void")]
    asof = pd.Timestamp(as_of)
    paid = (sub["status"] == "paid").to_numpy()
    obs = np.where(paid, (sub["paid_date"] - sub["due_date"]).dt.days,
                   (asof - sub["due_date"]).dt.days).astype(float)
    cids = sub["counterparty_id"].to_numpy()
    per = {int(c): (_km(obs[cids == c], paid[cids == c]), int((cids == c).sum()))
           for c in np.unique(cids)}
    return per, _km(obs, paid)


def _dist(per, port, cid: int, min_delay: float):
    """Customer history blended with the portfolio, conditioned on delay > min_delay."""
    (cust, n) = per.get(cid, ((np.array([np.inf]), np.array([1.0])), 0))
    w = n / (n + SMOOTH_K)
    vals = np.concatenate([cust[0], port[0]])
    probs = np.concatenate([w * cust[1], (1 - w) * port[1]])
    keep = vals > min_delay
    total = probs[keep].sum()
    if total <= 1e-12:
        return None
    return vals[keep], probs[keep] / total


def _weeks(offset_days: np.ndarray) -> np.ndarray:
    return np.floor((offset_days - 1) / 7) + 1  # day 1..7 -> week 1


# ---------- simulation of each driver ----------
def _simulate_open(rng, inv, kind, as_of, S, per, port):
    asof = pd.Timestamp(as_of)
    flows = np.zeros((S, HORIZON_WEEKS))
    sims = np.arange(S)
    recs = []
    for r in inv[(inv["type"] == kind) & (inv["status"] == "open")].itertuples():
        overdue = (asof - r.due_date).days
        dist = _dist(per, port, int(r.counterparty_id), overdue)
        if dist is None:
            offset = np.full(S, 1.0 if kind == "AP" else np.inf)
        else:
            offset = rng.choice(dist[0], size=S, p=dist[1]) - overdue  # days from today
            if kind == "AP":  # we owe it: never assume a bill quietly disappears
                offset = np.where(np.isinf(offset), 1.0, offset)
        wk = _weeks(offset)
        ok = (wk >= 1) & (wk <= HORIZON_WEEKS)
        np.add.at(flows, (sims[ok], wk[ok].astype(int) - 1), r.amount)
        recs.append(dict(
            invoice_id=int(r.id), invoice_no=r.invoice_no,
            counterparty_id=int(r.counterparty_id), amount=round(float(r.amount), 2),
            days_overdue=int(overdue), p_collect_13w=round(float(ok.mean()), 3),
            expected_pay_date=(as_of + timedelta(days=int(np.median(offset[ok])))).isoformat()
            if ok.any() else None))
    cols = ["invoice_id", "invoice_no", "counterparty_id", "amount", "days_overdue",
            "p_collect_13w", "expected_pay_date"]
    return flows, pd.DataFrame(recs, columns=cols)


def _simulate_new(rng, inv, kind, as_of, S, per, port, lookback=365):
    """Invoices that don't exist yet: rate, size and terms learned from each counterparty."""
    asof = pd.Timestamp(as_of)
    hist = inv[(inv["type"] == kind) & (inv["status"] != "void")
               & (inv["issue_date"] > asof - pd.Timedelta(days=lookback))
               & (inv["issue_date"] <= asof)]
    flows = np.zeros((S, HORIZON_WEEKS))
    # Use the history we actually have; dividing by a full year would understate the billing rate
    first = inv.loc[inv["type"] == kind, "issue_date"].min()
    span = max(1, min(lookback, (asof - first).days + 1)) if pd.notna(first) else lookback
    for cid, g in hist.groupby("counterparty_id"):
        counts = rng.poisson(len(g) / span * HORIZON_DAYS, S)
        total = int(counts.sum())
        dist = _dist(per, port, int(cid), -np.inf)
        if total == 0 or dist is None:
            continue
        sims = np.repeat(np.arange(S), counts)
        issue_off = rng.integers(1, HORIZON_DAYS + 1, total)
        amounts = rng.choice(g["amount"].to_numpy(), total)
        terms = float((g["due_date"] - g["issue_date"]).dt.days.median())
        pay_off = issue_off + terms + rng.choice(dist[0], size=total, p=dist[1])
        wk = _weeks(pay_off)
        ok = (wk >= 1) & (wk <= HORIZON_WEEKS)
        np.add.at(flows, (sims[ok], wk[ok].astype(int) - 1), amounts[ok])
    return flows


def _simulate_recurring(rng, bank, as_of, S) -> dict[str, np.ndarray]:
    """Payroll, rent, tax, fees: cadence, day-of-month and size learned from bank history."""
    asof = pd.Timestamp(as_of)
    horizon_end = asof + pd.Timedelta(days=HORIZON_DAYS)
    out: dict[str, np.ndarray] = {}
    rec = bank[bank["category"].notna() & ~bank["category"].isin(INVOICE_CATEGORIES)]
    for cat, g in rec.groupby("category"):
        g = g.sort_values("txn_date")
        if len(g) < 2:
            continue
        dates, amts = g["txn_date"], g["amount"].to_numpy()
        step = max(1, int(round(dates.diff().dt.days.dropna().median() / 30.4)))
        month_end = (dates == dates + pd.offsets.MonthEnd(0)).mean() >= 0.8
        dom = int(dates.dt.day.median())
        mean = float(amts[-3:].mean())
        sd = float(amts.std(ddof=1)) if len(amts) >= 3 else abs(mean) * 0.05
        arr = np.zeros((S, HORIZON_WEEKS))
        period = dates.iloc[-1].to_period("M")
        while True:
            period = period + step
            d = (period.end_time.normalize() if month_end else
                 pd.Timestamp(year=period.year, month=period.month,
                              day=min(dom, period.days_in_month)))
            if d > horizon_end:
                break
            if d > asof:
                k = (int((d - asof).days) - 1) // 7
                draw = rng.normal(mean, sd, S)
                arr[:, k] += np.minimum(draw, 0) if mean < 0 else np.maximum(draw, 0)
        if arr.any():
            out[f"Recurring: {cat}"] = arr
    return out


# ---------- the forecast ----------
@dataclass
class Forecast:
    as_of: date
    opening_cash: float
    n_sims: int
    weeks: list[dict]
    summary: dict
    open_ar: pd.DataFrame
    components: dict[str, np.ndarray] = field(repr=False)
    balance: np.ndarray = field(repr=False)

    def to_dict(self) -> dict:
        return dict(as_of=self.as_of.isoformat(), opening_cash=self.opening_cash,
                    n_sims=self.n_sims, summary=self.summary, weeks=self.weeks)


def run_forecast(inv: pd.DataFrame, bank: pd.DataFrame, as_of: date, opening_cash: float,
                 n_sims: int = 1000, seed: int = 42, exclude_ids=frozenset()) -> Forecast:
    rng = np.random.default_rng(seed)
    inv = inv[~inv["id"].isin(exclude_ids)]
    S = n_sims

    per_ar, port_ar = _build_dists(inv, "AR", as_of)
    per_ap, port_ap = _build_dists(inv, "AP", as_of)
    ar_open, open_ar = _simulate_open(rng, inv, "AR", as_of, S, per_ar, port_ar)
    ap_open, _ = _simulate_open(rng, inv, "AP", as_of, S, per_ap, port_ap)

    comps: dict[str, np.ndarray] = {
        "Collections: open invoices": ar_open,
        "Collections: new billings": _simulate_new(rng, inv, "AR", as_of, S, per_ar, port_ar),
        "Vendor payments: open bills": -ap_open,
        "Vendor payments: new bills": -_simulate_new(rng, inv, "AP", as_of, S, per_ap, port_ap),
    }
    comps.update(_simulate_recurring(rng, bank, as_of, S))

    net = sum(comps.values())
    balance = opening_cash + np.cumsum(net, axis=1)
    p10, p50, p90 = np.percentile(balance, [10, 50, 90], axis=0)

    weeks = []
    for k in range(HORIZON_WEEKS):
        parts = {n: round(float(a[:, k].mean()), 2) for n, a in comps.items()}
        weeks.append(dict(
            week=k + 1,
            start=(as_of + timedelta(days=7 * k + 1)).isoformat(),
            end=(as_of + timedelta(days=7 * k + 7)).isoformat(),
            drivers=parts,
            inflows=round(sum(v for n, v in parts.items() if n.startswith("Collections")), 2),
            outflows=round(sum(v for n, v in parts.items() if not n.startswith("Collections")), 2),
            net=round(sum(parts.values()), 2),
            closing_p10=round(float(p10[k]), 2), closing_p50=round(float(p50[k]), 2),
            closing_p90=round(float(p90[k]), 2)))

    at_risk = open_ar[open_ar["p_collect_13w"] < 0.5]
    low_week = int(np.argmin(p10)) + 1
    summary = dict(
        opening_cash=round(opening_cash, 2),
        closing_p50_week13=weeks[-1]["closing_p50"],
        change_pct=round(100 * (weeks[-1]["closing_p50"] - opening_cash) / opening_cash, 1)
        if opening_cash else None,
        lowest_p10_balance=round(float(p10.min()), 2), lowest_p10_week=low_week,
        prob_negative_cash_pct=round(float((balance.min(axis=1) < 0).mean() * 100), 1),
        open_ar_at_risk_amount=round(float(at_risk["amount"].sum()), 2),
        open_ar_at_risk_count=int(len(at_risk)),
        expected_collections_from_open_ar=round(
            float((open_ar["amount"] * open_ar["p_collect_13w"]).sum()), 2))
    return Forecast(as_of, round(opening_cash, 2), S, weeks, summary, open_ar, comps, balance)


# ---------- backtest ----------
def backtest(inv: pd.DataFrame, bank: pd.DataFrame, opening_balance: float, as_of: date,
             n_sims: int = 500, seed: int = 7) -> dict:
    """Rewind to 13 weeks ago, forecast using only what was known then, compare to reality."""
    cutoff = as_of - timedelta(days=HORIZON_DAYS)
    c = pd.Timestamp(cutoff)
    inv_c = inv[inv["issue_date"] <= c].copy()
    later = inv_c["paid_date"] > c
    inv_c.loc[later, "status"] = "open"
    inv_c.loc[later, "paid_date"] = pd.NaT
    bank_c = bank[bank["txn_date"] <= c]
    cash_c = opening_balance + float(bank_c["amount"].sum())
    fc = run_forecast(inv_c, bank_c, cutoff, cash_c, n_sims, seed)

    actual = bank[(bank["txn_date"] > c) & (bank["txn_date"] <= c + pd.Timedelta(days=HORIZON_DAYS))]
    wk = ((actual["txn_date"] - c).dt.days - 1) // 7
    weekly = actual.groupby(wk)["amount"].sum().reindex(range(HORIZON_WEEKS), fill_value=0.0)
    actual_bal = cash_c + weekly.cumsum().to_numpy()

    rows = []
    for k, w in enumerate(fc.weeks):
        rows.append(dict(week=k + 1, actual=round(float(actual_bal[k]), 2),
                         p10=w["closing_p10"], p50=w["closing_p50"], p90=w["closing_p90"],
                         within_p10_p90=bool(w["closing_p10"] <= actual_bal[k] <= w["closing_p90"])))
    err = np.abs(np.array([r["p50"] for r in rows]) - actual_bal) / np.abs(actual_bal) * 100

    def actual_sum(cat): return float(actual.loc[actual["category"] == cat, "amount"].sum())
    forecast_coll = sum(sum(w["drivers"][n] for n in w["drivers"] if n.startswith("Collections"))
                        for w in fc.weeks)
    forecast_pay = sum(sum(w["drivers"][n] for n in w["drivers"] if n.startswith("Vendor"))
                       for w in fc.weeks)
    return dict(
        forecast_date=cutoff.isoformat(), opening_cash=round(cash_c, 2), weeks=rows,
        mean_abs_pct_error_p50=round(float(err.mean()), 1),
        final_week_error_pct=round(float(err[-1]), 1),
        band_coverage_pct=round(100 * sum(r["within_p10_p90"] for r in rows) / len(rows), 1),
        collections=dict(forecast=round(forecast_coll, 2),
                         actual=round(actual_sum("customer_receipt"), 2)),
        vendor_payments=dict(forecast=round(forecast_pay, 2),
                             actual=round(actual_sum("vendor_payment"), 2)))