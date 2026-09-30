from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from app.db import get_session
from app.models.tables import (
    AppMeta, BankTransaction, Counterparty, Invoice, InvoiceStatus, InvoiceType,
)
from app.tools.ingestion import load_dir

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"


@router.post("/sample")
def load_sample(session: Session = Depends(get_session)) -> dict:
    """Validate and load the synthetic CSVs into the database."""
    if not (SAMPLE_DIR / "invoices.csv").exists():
        raise HTTPException(404, "Run data/generate_synthetic.py first.")
    return load_dir(SAMPLE_DIR, session)


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    def count(model) -> int:
        return session.exec(select(func.count()).select_from(model)).one()

    def open_total(kind: InvoiceType) -> float:
        total = session.exec(select(func.sum(Invoice.amount)).where(
            Invoice.type == kind, Invoice.status == InvoiceStatus.OPEN)).one()
        return round(total or 0.0, 2)

    return dict(
        counterparties=count(Counterparty), invoices=count(Invoice),
        bank_transactions=count(BankTransaction),
        open_ar_total=open_total(InvoiceType.AR), open_ap_total=open_total(InvoiceType.AP),
        meta={m.key: m.value for m in session.exec(select(AppMeta)).all()},
    )