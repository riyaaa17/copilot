import pandas as pd
from app.tools.ingestion import validate_invoices

KINDS = {1: "customer", 2: "vendor"}


def make_invoice(**overrides) -> dict:
    row = dict(invoice_id=1, invoice_no="INV-00001", type="AR", counterparty_id=1,
               issue_date="2026-01-01", due_date="2026-01-31", paid_date=None,
               amount=1000.0, currency="usd", category="Services", status="open")
    row.update(overrides)
    return row


def run(*rows):
    return validate_invoices(pd.DataFrame(rows), KINDS)


def test_good_row_passes_and_currency_is_normalised():
    res = run(make_invoice())
    assert len(res.clean) == 1 and res.rejected.empty
    assert res.clean.iloc[0]["currency"] == "USD"


def test_negative_amount_rejected():
    res = run(make_invoice(amount=-5))
    assert len(res.rejected) == 1


def test_due_before_issue_rejected():
    res = run(make_invoice(due_date="2025-12-01"))
    assert len(res.rejected) == 1


def test_paid_status_without_date_rejected():
    res = run(make_invoice(status="paid", paid_date=None))
    assert len(res.rejected) == 1


def test_unparseable_date_rejected():
    res = run(make_invoice(issue_date="not a date"))
    assert len(res.rejected) == 1


def test_unknown_counterparty_rejected():
    res = run(make_invoice(counterparty_id=99))
    assert any("unknown counterparty" in i["problem"] for i in res.issues)


def test_ar_invoice_on_vendor_rejected():
    res = run(make_invoice(counterparty_id=2))
    assert len(res.rejected) == 1


def test_duplicate_invoice_id_rejects_only_the_repeat():
    res = run(make_invoice(), make_invoice(invoice_no="INV-00002"))
    assert len(res.clean) == 1 and len(res.rejected) == 1


def test_lookalike_duplicate_invoices_are_kept_for_the_anomaly_agent():
    res = run(make_invoice(), make_invoice(invoice_id=2))  # same no/amount, new id
    assert len(res.clean) == 2