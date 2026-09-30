"""Deterministic ingestion and validation. No LLM involved: rules, not guesses."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

try:
    import pandera.pandas as pa
except ImportError:  # older pandera versions
    import pandera as pa

from sqlmodel import Session, delete

from app.models.tables import (
    AnomalyFlag, AppMeta, BankTransaction, Counterparty, EmailDraft,
    Invoice, InvoiceStatus, InvoiceType,
)

ALLOWED_CURRENCIES = ["USD", "EUR", "GBP", "INR"]

COUNTERPARTY_COLS = ["id", "name", "kind", "email", "payment_terms_days"]
INVOICE_COLS = ["invoice_id", "invoice_no", "type", "counterparty_id", "issue_date", "due_date",
                "paid_date", "amount", "currency", "category", "status"]
BANK_COLS = ["txn_id", "txn_date", "description", "amount", "category"]


@dataclass
class ValidationResult:
    clean: pd.DataFrame
    rejected: pd.DataFrame
    issues: list[dict] = field(default_factory=list)


# ---------- normalisation helpers ----------
def _text(s: pd.Series, upper: bool = False, lower: bool = False) -> pd.Series:
    out = s.astype("string").str.strip().replace("", pd.NA)
    if upper:
        out = out.str.upper()
    if lower:
        out = out.str.lower()
    return out.astype(object).where(out.notna(), None)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed")


def _require_columns(df: pd.DataFrame, cols: list[str], table: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table}: missing required columns {missing}")


def _run_schema(df: pd.DataFrame, schema, table: str) -> tuple[set[int], list[dict]]:
    """Validate lazily so we collect every problem, not just the first."""
    try:
        schema.validate(df, lazy=True)
        return set(), []
    except pa.errors.SchemaErrors as exc:
        bad: set[int] = set()
        issues: list[dict] = []
        seen: set[tuple] = set()
        for rec in exc.failure_cases.to_dict("records"):
            idx = rec.get("index")
            row = None if pd.isna(idx) else int(idx)
            if row is not None:
                bad.add(row)
            row_level = rec.get("schema_context") != "Column"  # rule spans several columns
            problem = str(rec.get("check"))
            key = (row, problem) if row_level else (row, problem, rec.get("column"))
            if key in seen:
                continue
            seen.add(key)
            issues.append(dict(table=table,
                               csv_line=None if row is None else row + 2,
                               column=None if row_level else rec.get("column"),
                               problem=problem,
                               value=None if row_level else str(rec.get("failure_case"))))
        return bad, issues


def _split(df: pd.DataFrame, bad: set[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    mask = df.index.isin(bad)
    return df.loc[~mask].copy(), df.loc[mask].copy()


# ---------- schemas ----------
counterparty_schema = pa.DataFrameSchema({
    "id": pa.Column(nullable=False, unique=True, report_duplicates="exclude_first",
                    checks=pa.Check.ge(1)),
    "name": pa.Column(nullable=False),
    "kind": pa.Column(nullable=False, checks=pa.Check.isin(["customer", "vendor"])),
    "email": pa.Column(nullable=True),
    "payment_terms_days": pa.Column(nullable=False, checks=pa.Check.in_range(0, 180)),
})

invoice_schema = pa.DataFrameSchema(
    {
        "invoice_id": pa.Column(nullable=False, unique=True, report_duplicates="exclude_first"),
        "invoice_no": pa.Column(nullable=False),
        "type": pa.Column(nullable=False, checks=pa.Check.isin(["AR", "AP"])),
        "counterparty_id": pa.Column(nullable=False),
        "issue_date": pa.Column(nullable=False),
        "due_date": pa.Column(nullable=False),
        "paid_date": pa.Column(nullable=True),
        "amount": pa.Column(nullable=False, checks=pa.Check.gt(0)),
        "currency": pa.Column(nullable=False, checks=pa.Check.isin(ALLOWED_CURRENCIES)),
        "category": pa.Column(nullable=True),
        "status": pa.Column(nullable=False, checks=pa.Check.isin(["open", "paid", "void"])),
    },
    checks=[
        pa.Check(lambda d: d.due_date.isna() | d.issue_date.isna() | (d.due_date >= d.issue_date),
                 name="due_date_before_issue_date", error="due_date is before issue_date"),
        pa.Check(lambda d: d.paid_date.isna() | d.issue_date.isna() | (d.paid_date >= d.issue_date),
                 name="paid_before_issue", error="paid_date is before issue_date"),
        pa.Check(lambda d: (d.status != "paid") | d.paid_date.notna(),
                 name="paid_without_date", error="status is paid but paid_date is empty"),
        pa.Check(lambda d: (d.status != "open") | d.paid_date.isna(),
                 name="open_with_paid_date", error="status is open but paid_date is filled"),
    ],
)

bank_schema = pa.DataFrameSchema({
    "txn_id": pa.Column(nullable=False, unique=True, report_duplicates="exclude_first"),
    "txn_date": pa.Column(nullable=False),
    "description": pa.Column(nullable=False),
    "amount": pa.Column(nullable=False, checks=pa.Check(lambda s: s != 0, name="zero_amount",
                                                        error="amount is zero")),
    "category": pa.Column(nullable=True),
})


# ---------- validators ----------
def validate_counterparties(raw: pd.DataFrame) -> ValidationResult:
    _require_columns(raw, COUNTERPARTY_COLS, "counterparties")
    df = raw[COUNTERPARTY_COLS].copy()
    df["id"] = _num(df["id"])
    df["name"] = _text(df["name"])
    df["kind"] = _text(df["kind"], lower=True)
    df["email"] = _text(df["email"], lower=True)
    df["payment_terms_days"] = _num(df["payment_terms_days"])
    bad, issues = _run_schema(df, counterparty_schema, "counterparties")
    clean, rejected = _split(df, bad)
    clean["id"] = clean["id"].astype(int)
    clean["payment_terms_days"] = clean["payment_terms_days"].astype(int)
    return ValidationResult(clean, rejected, issues)


def validate_invoices(raw: pd.DataFrame, cp_kinds: dict[int, str]) -> ValidationResult:
    _require_columns(raw, INVOICE_COLS, "invoices")
    df = raw[INVOICE_COLS].copy()
    df["invoice_id"] = _num(df["invoice_id"])
    df["invoice_no"] = _text(df["invoice_no"])
    df["type"] = _text(df["type"], upper=True)
    df["counterparty_id"] = _num(df["counterparty_id"])
    for c in ("issue_date", "due_date", "paid_date"):
        df[c] = _date(df[c])
    df["amount"] = _num(df["amount"])
    df["currency"] = _text(df["currency"], upper=True)
    df["category"] = _text(df["category"])
    df["status"] = _text(df["status"], lower=True)

    bad, issues = _run_schema(df, invoice_schema, "invoices")

    # Referential checks on rows that passed the schema
    for idx, r in df.loc[~df.index.isin(bad)].iterrows():
        kind = cp_kinds.get(int(r.counterparty_id))
        if kind is None:
            bad.add(idx)
            issues.append(dict(table="invoices", csv_line=idx + 2, column="counterparty_id",
                               problem="unknown counterparty", value=str(int(r.counterparty_id))))
        elif (r.type == "AR") != (kind == "customer"):
            bad.add(idx)
            issues.append(dict(table="invoices", csv_line=idx + 2, column="type",
                               problem=f"{r.type} invoice linked to a {kind}", value=str(r.type)))

    clean, rejected = _split(df, bad)
    clean["invoice_id"] = clean["invoice_id"].astype(int)
    clean["counterparty_id"] = clean["counterparty_id"].astype(int)
    return ValidationResult(clean, rejected, issues)


def validate_bank(raw: pd.DataFrame) -> ValidationResult:
    _require_columns(raw, BANK_COLS, "bank_transactions")
    df = raw[BANK_COLS].copy()
    df["txn_id"] = _num(df["txn_id"])
    df["txn_date"] = _date(df["txn_date"])
    df["description"] = _text(df["description"])
    df["amount"] = _num(df["amount"])
    df["category"] = _text(df["category"])
    bad, issues = _run_schema(df, bank_schema, "bank_transactions")
    clean, rejected = _split(df, bad)
    clean["txn_id"] = clean["txn_id"].astype(int)
    return ValidationResult(clean, rejected, issues)


# ---------- loading ----------
def _none(v):
    return None if pd.isna(v) else v


INVOICE_NO_PATTERN = re.compile(r"(INV-\d{5}|V\d+-\d{4})")


def load_dir(data_dir: Path, session: Session) -> dict:
    """Validate the CSVs in data_dir and load the clean rows into the database."""
    cps = validate_counterparties(pd.read_csv(data_dir / "counterparties.csv"))
    cp_kinds = dict(zip(cps.clean["id"], cps.clean["kind"]))
    inv = validate_invoices(pd.read_csv(data_dir / "invoices.csv"), cp_kinds)
    bank = validate_bank(pd.read_csv(data_dir / "bank_transactions.csv"))

    # start fresh (children first)
    for model in (AnomalyFlag, EmailDraft, BankTransaction, Invoice, Counterparty, AppMeta):
        session.exec(delete(model))

    session.add_all(Counterparty(
        id=int(r.id), name=r.name, kind=r.kind, email=_none(r.email),
        payment_terms_days=int(r.payment_terms_days)) for r in cps.clean.itertuples())

    session.add_all(Invoice(
        id=int(r.invoice_id), invoice_no=r.invoice_no, type=InvoiceType(r.type),
        counterparty_id=int(r.counterparty_id), issue_date=r.issue_date.date(),
        due_date=r.due_date.date(),
        paid_date=None if pd.isna(r.paid_date) else r.paid_date.date(),
        amount=float(r.amount), currency=r.currency, category=_none(r.category),
        status=InvoiceStatus(r.status)) for r in inv.clean.itertuples())

    # Reconcile bank lines to paid invoices using the invoice number in the description
    paid_lookup = {r.invoice_no: int(r.invoice_id) for r in inv.clean.itertuples()
                   if r.status == "paid"}
    reconciled = 0
    txns = []
    for r in bank.clean.itertuples():
        m = INVOICE_NO_PATTERN.search(r.description)
        matched = paid_lookup.get(m.group(1)) if m else None
        reconciled += matched is not None
        txns.append(BankTransaction(
            id=int(r.txn_id), txn_date=r.txn_date.date(), description=r.description,
            amount=float(r.amount), category=_none(r.category), matched_invoice_id=matched))
    session.add_all(txns)

    meta_file = data_dir / "meta.json"
    if meta_file.exists():
        for k, v in json.loads(meta_file.read_text()).items():
            session.add(AppMeta(key=k, value=str(v)))
    session.commit()

    def summarize(name: str, res: ValidationResult) -> dict:
        return dict(table=name, rows_in=len(res.clean) + len(res.rejected),
                    loaded=len(res.clean), rejected=len(res.rejected),
                    issues=res.issues[:20])

    return dict(
        tables=[summarize("counterparties", cps),
                summarize("invoices", inv),
                summarize("bank_transactions", bank)],
        bank_lines_reconciled=reconciled,
    )