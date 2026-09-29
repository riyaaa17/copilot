from datetime import date, datetime
from enum import Enum
from sqlmodel import Field, SQLModel


class InvoiceType(str, Enum):
    AR = "AR"  # receivable (we are owed)
    AP = "AP"  # payable (we owe)


class InvoiceStatus(str, Enum):
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


class DraftStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"


class Counterparty(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    kind: str = "customer"  # customer | vendor
    email: str | None = None
    payment_terms_days: int = 30


class Invoice(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    invoice_no: str = Field(index=True)
    type: InvoiceType
    counterparty_id: int = Field(foreign_key="counterparty.id", index=True)
    issue_date: date
    due_date: date
    paid_date: date | None = None
    amount: float
    currency: str = "USD"
    category: str | None = None
    status: InvoiceStatus = InvoiceStatus.OPEN


class BankTransaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    txn_date: date = Field(index=True)
    description: str
    amount: float  # positive = inflow, negative = outflow
    category: str | None = None
    matched_invoice_id: int | None = Field(default=None, foreign_key="invoice.id")


class EmailDraft(SQLModel, table=True):
    """Human-in-the-loop: agents only write drafts; a person approves."""
    id: int | None = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    subject: str
    body: str
    status: DraftStatus = DraftStatus.DRAFT
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None = None


class AnomalyFlag(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    invoice_id: int | None = Field(default=None, foreign_key="invoice.id")
    kind: str  # duplicate | outlier | suspicious
    score: float
    explanation: str
    created_at: datetime = Field(default_factory=datetime.utcnow)