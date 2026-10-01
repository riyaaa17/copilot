import pandas as pd
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from app.models.tables import AnomalyFlag
from app.tools.analytics import INVOICE_COLS
from app.tools.anomalies import find_duplicates, find_outliers, number_similarity, save_flags, scan

NAMES = {1: "Acme", 2: "Globex"}


def make_inv(rows: list[dict]) -> pd.DataFrame:
    base = dict(invoice_no="INV-00001", type="AR", counterparty_id=1, category=None,
                issue_date="2026-01-10", due_date="2026-02-10", paid_date=None,
                amount=500.0, status="open")
    df = pd.DataFrame([{**base, "id": i + 1, **r} for i, r in enumerate(rows)],
                      columns=INVOICE_COLS)
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = pd.to_datetime(df[c])
    return df


def test_number_similarity():
    assert number_similarity("INV-01042", "inv 01042") == 1.0
    assert number_similarity("INV-01042", "INV-01042-A") == 0.9
    assert number_similarity("INV-01042", "INV-1042") == 0.9
    assert number_similarity("INV-01042", "INV-01043") == 0.0     # neighbours are normal
    assert number_similarity("INV-01042", "CRN-01042") == 0.0     # credit note, not a repeat


def test_repeat_invoice_flags_the_later_one():
    df = make_inv([dict(), dict(invoice_no="INV-00001-A", issue_date="2026-01-12")])
    d = find_duplicates(df, NAMES)
    assert list(d["invoice_id"]) == [2] and list(d["related_invoice_id"]) == [1]
    assert d.iloc[0]["severity"] == "high"


def test_monthly_subscription_is_not_a_duplicate():
    df = make_inv([dict(invoice_no="INV-00010", issue_date="2026-01-01"),
                   dict(invoice_no="INV-00042", issue_date="2026-01-31"),
                   dict(invoice_no="INV-00077", issue_date="2026-03-02")])
    assert find_duplicates(df, NAMES).empty


def test_same_amount_different_counterparties_is_not_a_duplicate():
    df = make_inv([dict(), dict(counterparty_id=2)])
    assert find_duplicates(df, NAMES).empty


def test_duplicate_that_was_paid_twice_is_high_severity_and_mentions_cash():
    df = make_inv([dict(status="paid", paid_date="2026-02-10"),
                   dict(issue_date="2026-01-11", status="paid", paid_date="2026-02-12")])
    d = find_duplicates(df, NAMES)
    assert d.iloc[0]["severity"] == "high" and "duplicate payment" in d.iloc[0]["explanation"]


def test_outlier_is_flagged_and_normal_invoices_are_not():
    amounts = [500, 520, 480, 510, 495, 505, 490, 515, 5000]
    df = make_inv([dict(amount=float(a), invoice_no=f"INV-{i:05d}") for i, a in enumerate(amounts)])
    o = find_outliers(df, NAMES)
    assert list(o["invoice_id"]) == [9]


def test_regular_identical_invoices_do_not_trigger_outliers():
    df = make_inv([dict(amount=500.0, invoice_no=f"INV-{i:05d}") for i in range(8)]
                  + [dict(amount=510.0, invoice_no="INV-99999")])
    assert find_outliers(df, NAMES).empty     # 2% wobble is not an anomaly


def test_too_little_history_is_not_judged():
    df = make_inv([dict(amount=500.0), dict(amount=9000.0, invoice_no="INV-2")])
    assert find_outliers(df, NAMES).empty


def test_review_decisions_survive_a_rescan():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    df = make_inv([dict(), dict(invoice_no="INV-00001", issue_date="2026-01-11")])
    with Session(engine) as s:
        save_flags(s, scan(df, NAMES))
        f = s.exec(select(AnomalyFlag)).one()
        f.status = "dismissed"
        s.add(f)
        s.commit()
        save_flags(s, scan(df, NAMES))
        assert s.exec(select(AnomalyFlag)).one().status == "dismissed"