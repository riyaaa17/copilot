from datetime import date

import numpy as np
import pandas as pd
from app.tools.analytics import INVOICE_COLS
from app.tools.forecast import _dist, _km, run_forecast

AS_OF = date(2026, 6, 30)
NO_BANK = pd.DataFrame(columns=["txn_date", "amount", "category"]).astype(
    {"txn_date": "datetime64[ns]", "amount": float, "category": object})


def make_inv(rows: list[dict]) -> pd.DataFrame:
    base = dict(invoice_no="X", type="AR", counterparty_id=1, category=None,
                issue_date="2026-03-01", due_date="2026-03-31", paid_date=None,
                amount=100.0, status="open")
    df = pd.DataFrame([{**base, "id": i + 1, **r} for i, r in enumerate(rows)],
                      columns=INVOICE_COLS)
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def on_time_history(n=10, cid=1, kind="AR") -> list[dict]:
    return [dict(type=kind, counterparty_id=cid, status="paid", paid_date="2026-03-31")] * n


def test_km_without_censoring():
    vals, probs = _km(np.array([0.0, 0.0, 10.0]), np.array([True, True, True]))
    assert list(vals) == [0.0, 10.0, np.inf]
    assert np.allclose(probs, [2 / 3, 1 / 3, 0.0])


def test_km_keeps_unpaid_invoices_as_a_tail():
    # one paid on time, one still unpaid after 30 days -> half the mass is "later than 30"
    vals, probs = _km(np.array([0.0, 30.0]), np.array([True, False]))
    assert list(vals) == [0.0, np.inf] and np.allclose(probs, [0.5, 0.5])


def test_dist_is_conditioned_on_being_already_overdue():
    port = (np.array([0.0, 20.0, np.inf]), np.array([0.5, 0.4, 0.1]))
    vals, probs = _dist({}, port, cid=1, min_delay=10)
    assert 0.0 not in vals and np.isclose(probs.sum(), 1.0)


def test_open_invoice_lands_in_the_expected_week():
    rows = on_time_history() + [dict(issue_date="2026-06-03", due_date="2026-07-03",
                                     amount=1000.0)]
    fc = run_forecast(make_inv(rows), NO_BANK, AS_OF, 10_000.0, n_sims=200)
    assert fc.open_ar.iloc[0]["p_collect_13w"] == 1.0
    assert fc.open_ar.iloc[0]["expected_pay_date"] == "2026-07-03"
    assert fc.weeks[0]["drivers"]["Collections: open invoices"] == 1000.0


def test_hopelessly_overdue_receivable_is_not_counted_as_cash():
    rows = on_time_history(3) + [dict(due_date="2025-02-01", issue_date="2025-01-01",
                                      amount=500.0)]
    fc = run_forecast(make_inv(rows), NO_BANK, AS_OF, 0.0, n_sims=200)
    assert fc.open_ar.iloc[0]["p_collect_13w"] == 0.0
    assert fc.summary["open_ar_at_risk_count"] == 1


def test_overdue_payable_is_assumed_paid_next_week():
    rows = on_time_history(3, cid=2, kind="AP") + [dict(
        type="AP", counterparty_id=2, due_date="2025-02-01", issue_date="2025-01-01",
        amount=300.0)]
    fc = run_forecast(make_inv(rows), NO_BANK, AS_OF, 1000.0, n_sims=200)
    assert fc.weeks[0]["drivers"]["Vendor payments: open bills"] == -300.0


def test_recurring_items_are_projected_from_bank_history():
    bank = pd.DataFrame(
        [dict(txn_date=d, amount=-1000.0, category="rent")
         for d in pd.date_range("2026-01-01", periods=6, freq="MS")]
        + [dict(txn_date=d, amount=-5000.0, category="payroll")
           for d in pd.date_range("2026-01-31", periods=6, freq="ME")])
    fc = run_forecast(make_inv([]), bank, AS_OF, 50_000.0, n_sims=100)
    rent = sum(w["drivers"]["Recurring: rent"] for w in fc.weeks)
    payroll = [w["drivers"]["Recurring: payroll"] for w in fc.weeks]
    assert rent == -3000.0                       # Jul 1, Aug 1, Sep 1
    assert payroll[4] == -5000.0 and sum(payroll) == -10000.0   # Jul 31, Aug 31


def test_closing_balance_equals_opening_plus_all_flows():
    rows = on_time_history() + [dict(issue_date="2026-06-03", due_date="2026-07-03")]
    fc = run_forecast(make_inv(rows), NO_BANK, AS_OF, 10_000.0, n_sims=100)
    total = sum(c.sum(axis=1) for c in fc.components.values())
    assert np.allclose(fc.balance[:, -1], 10_000.0 + total)


def test_same_seed_gives_same_forecast():
    rows = on_time_history() + [dict(issue_date="2026-06-03", due_date="2026-07-03")]
    a = run_forecast(make_inv(rows), NO_BANK, AS_OF, 1.0, n_sims=100, seed=5)
    b = run_forecast(make_inv(rows), NO_BANK, AS_OF, 1.0, n_sims=100, seed=5)
    assert a.summary == b.summary