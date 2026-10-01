from datetime import date, datetime, timezone
from enum import Enum
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    """Human-in-the-loop: agents only write drafts; a person approves. Nothing is auto-sent."""
    id: int | None = Field(default=None, primary_key=True)
    counterparty_id: int = Field(foreign_key="counterparty.id", index=True)
    to_email: str | None = None
    subject: str
    body: str
    tone: str = "friendly"  # friendly | firm | urgent
    invoice_ids: str = ""  # comma-separated invoice ids covered by this email
    total_amount: float = 0.0
    source: str = "template"  # llm | template
    warnings: str | None = None  # why an LLM draft was rejected, if it was
    status: DraftStatus = DraftStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: datetime | None = None


class AnomalyFlag(SQLModel, table=True):
    """A suspicious invoice. A person reviews it: open -> confirmed or dismissed."""
    id: int | None = Field(default=None, primary_key=True)
    invoice_id: int | None = Field(default=None, foreign_key="invoice.id", index=True)
    related_invoice_id: int | None = Field(default=None, foreign_key="invoice.id")
    kind: str  # duplicate | outlier
    score: float  # confidence, 0 to 1
    severity: str = "medium"  # medium | high
    explanation: str
    status: str = "open"  # open | confirmed | dismissed
    created_at: datetime = Field(default_factory=utcnow)


class AppMeta(SQLModel, table=True):
    """Small key-value store: as_of date, opening balance, etc."""
    key: str = Field(primary_key=True)
    value: str

class Briefing(SQLModel, table=True):
    """A saved weekly CFO briefing."""
    id: int | None = Field(default=None, primary_key=True)
    as_of: date
    markdown: str
    data_json: str  # every figure behind the briefing, for audit
    source: str = "template"  # llm | template (how the two narrative paragraphs were written)
    warnings: str | None = None
    created_at: datetime = Field(default_factory=utcnow)