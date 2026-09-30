from datetime import date

import pandas as pd
from app.tools.analytics import (
    INVOICE_COLS, aging_report, customer_profiles, payer_label, working_capital_days,
)

AS_OF = date(2026, 6, 30)


def inv_df(*rows: dict) -> pd.DataFrame:
    base = dict(invoice_no="X", type="AR", counterparty_id=1, category=None,
                issue_date="2026-01-01", due_date="2026-02-01", paid_date=None,
                amount=100.0, status="open")
    df = pd.DataFrame([{**base, "id": i + 1, **r} for i, r in enumerate(rows)],
                      columns=INVOICE_COLS)
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def due_days_ago(n: int) -> str:
    return str((pd.Timestamp(AS_OF) - pd.Timedelta(days=n)).date())


def test_aging_bucket_boundaries():
    cases = {0: "Not yet due", -5: "Not yet due", 1: "1-30", 30: "1-30",
             31: "31-60", 60: "31-60", 61: "61-90", 90: "61-90", 91: "90+"}
    for days, expected in cases.items():
        rep = aging_report(inv_df(dict(due_date=due_days_ago(days))), AS_OF)
        got = [b["bucket"] for b in rep["buckets"] if b["count"] == 1]
        assert got == [expected], f"{days} days overdue"


def test_aging_totals_and_percentages():
    rep = aging_report(inv_df(
        dict(due_date=due_days_ago(-10), amount=300.0),
        dict(due_date=due_days_ago(10), amount=100.0),
        dict(due_date=due_days_ago(100), amount=100.0),
        dict(due_date=due_days_ago(5), status="paid", paid_date="2026-06-01", amount=999.0)),
        AS_OF)
    assert rep["total_open"] == 500.0
    assert rep["total_overdue"] == 200.0
    assert rep["overdue_pct"] == 40.0
    assert round(sum(b["pct"] for b in rep["buckets"]), 1) == 100.0


def test_aging_ignores_the_other_invoice_type():
    rep = aging_report(inv_df(dict(type="AP", due_date=due_days_ago(10))), AS_OF, "AR")
    assert rep["total_open"] == 0.0 and rep["overdue_pct"] == 0.0


def test_dso_formula():
    # 100 open, 300 billed in the last 90 days -> 100/300*90 = 30 days
    df = inv_df(dict(issue_date="2026-05-01", amount=100.0),
                dict(issue_date="2026-05-10", amount=200.0, status="paid",
                     due_date="2026-06-01", paid_date="2026-06-05"))
    assert working_capital_days(df, AS_OF)["dso"] == 30.0


def test_days_beyond_terms_is_amount_weighted():
    df = inv_df(
        dict(status="paid", due_date="2026-02-01", paid_date="2026-02-11", amount=100.0),
        dict(status="paid", due_date="2026-02-01", paid_date="2026-02-01", amount=300.0))
    assert working_capital_days(df, AS_OF)["avg_days_beyond_terms"] == 2.5


def test_payer_label_thresholds():
    assert payer_label(-1, 4) == "prompt"
    assert payer_label(1, 25) == "average"   # prompt needs a tight tail too
    assert payer_label(20, 40) == "slow"
    assert payer_label(45, 80) == "chronic"


def test_profile_counts_overdue_open_invoices_as_lateness():
    paid = [dict(status="paid", due_date="2026-01-01", paid_date="2026-01-01")] * 3
    still_open = [dict(due_date=due_days_ago(100), amount=500.0)]
    prof = customer_profiles(inv_df(*paid, *still_open), {1: "Acme"}, AS_OF)
    row = prof.iloc[0]
    assert row["avg_days_late"] == 0.0                # paid history alone looks perfect
    assert row["avg_days_late_adjusted"] == 25.0      # (0+0+0+100)/4
    assert row["overdue_amount"] == 500.0 and row["max_days_overdue"] == 100


def test_profile_needs_enough_history():
    prof = customer_profiles(inv_df(dict(status="paid", paid_date="2026-02-01")),
                             {1: "Acme"}, AS_OF)
    assert prof.iloc[0]["payment_behavior"] == "insufficient_data"