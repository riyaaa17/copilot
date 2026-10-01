"""Collections Agent: deterministic prioritisation + LLM wording + human approval queue."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

from sqlmodel import Session, select

from app.config import get_settings
from app.models.tables import Counterparty, DraftStatus, EmailDraft
from app.tools import analytics as an
from app.tools import collections_tools as col
from app.tools import forecast as fx

LLMFn = Callable[[str, str], str]  # (system prompt, user prompt) -> text

SYSTEM_PROMPT = """You write accounts-receivable follow-up emails for a finance team.
Rules:
- Use ONLY the facts in the JSON. Never invent or change amounts, dates, invoice numbers or terms.
- Write dates exactly as supplied (YYYY-MM-DD) and amounts exactly as supplied.
- Do not mention legal action, late fees, interest, penalties or collection agencies.
- Match the requested tone: friendly = warm and light; firm = polite but direct; urgent = serious, still courteous.
- Under 150 words. List every invoice number with its amount. State the total outstanding.
- Ask the customer to confirm a payment date or tell us about any problem with the invoice.
- Sign off with the sender name and company supplied.
Respond with ONLY a JSON object: {"subject": "...", "body": "..."}"""


def default_llm() -> LLMFn | None:
    """The Groq-backed writer, or None when no API key is configured."""
    if not get_settings().groq_api_key:
        return None
    from app import llm
    return lambda system, prompt: llm.chat(prompt, system=system, fast=True, max_tokens=2000)


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and data.get("subject") and data.get("body"):
                return dict(subject=str(data["subject"]).strip(), body=str(data["body"]).strip())
        except (ValueError, TypeError):
            continue
    return None


def draft_for_customer(facts: dict, llm_fn: LLMFn | None, attempts: int = 2) -> dict:
    """LLM draft if it passes every check, otherwise the safe template."""
    warnings: list[str] = []
    if llm_fn is not None:
        prompt = "FACTS:\n" + json.dumps(facts, indent=1)
        for n in range(attempts):
            try:
                parsed = _parse_json(llm_fn(SYSTEM_PROMPT, prompt))
            except Exception as exc:  # network, rate limit, bad key...
                warnings.append(f"attempt {n + 1}: LLM call failed ({type(exc).__name__})")
                continue
            if parsed is None:
                warnings.append(f"attempt {n + 1}: reply was not valid JSON")
                continue
            problems = col.check_draft(facts, parsed["subject"], parsed["body"])
            if not problems:
                return dict(**parsed, source="llm", warnings=None)
            warnings.append(f"attempt {n + 1}: " + "; ".join(problems))
    tpl = col.render_template(facts)
    return dict(**tpl, source="template", warnings=" | ".join(warnings) or None)


# ---------- data assembly ----------
def build_priorities(session: Session, sims: int = 500):
    inv, bank, as_of = an.load_invoices(session), fx.load_bank(session), an.get_as_of(session)
    dup = fx.flagged_invoice_ids(session, "duplicate")
    held = fx.flagged_invoice_ids(session, "outlier")
    fc = fx.run_forecast(inv, bank, as_of, an.cash_balance(session) or 0.0, sims, 42,
                         exclude_ids=dup, learn_exclude_ids=held)
    names = an.load_names(session)
    profiles = an.customer_profiles(inv[~inv["id"].isin(dup)], names, as_of)
    return col.prioritize(fc.open_ar, inv, profiles, names, held_ids=held), as_of


def generate_drafts(session: Session, top: int = 5, use_llm: bool = True, sims: int = 500,
                    llm_fn: LLMFn | None = None) -> dict:
    s = get_settings()
    prio, as_of = build_priorities(session, sims)
    if llm_fn is None and use_llm:
        llm_fn = default_llm()
    emails = {c.id: c.email for c in session.exec(select(Counterparty)).all()}
    pending = {d.counterparty_id for d in session.exec(
        select(EmailDraft).where(EmailDraft.status == DraftStatus.DRAFT)).all()}
    created, skipped = [], []
    for c in col.group_by_customer(prio)[:top]:
        if c["counterparty_id"] in pending:
            skipped.append(dict(customer=c["customer"], reason="a draft is already waiting for review"))
            continue
        facts = dict(customer=c["customer"], tone=c["tone"], invoices=c["invoices"],
                     total_amount=c["total_amount"], sender_name=s.sender_name,
                     company_name=s.company_name)
        result = draft_for_customer(facts, llm_fn)
        draft = EmailDraft(
            counterparty_id=c["counterparty_id"], to_email=emails.get(c["counterparty_id"]),
            subject=result["subject"], body=result["body"], tone=c["tone"],
            invoice_ids=",".join(str(i["invoice_id"]) for i in c["invoices"]),
            total_amount=c["total_amount"], source=result["source"], warnings=result["warnings"])
        session.add(draft)
        created.append(draft)
    session.commit()
    for d in created:
        session.refresh(d)
    return dict(as_of=as_of.isoformat(), llm_used=llm_fn is not None,
                created=[draft_view(session, d) for d in created], skipped=skipped)


# ---------- approval workflow (a person is always in the loop) ----------
class WorkflowError(Exception):
    pass


def draft_view(session: Session, d: EmailDraft) -> dict:
    cp = session.get(Counterparty, d.counterparty_id)
    view = dict(
        draft_id=d.id, status=d.status.value, customer=cp.name if cp else None, to_email=d.to_email,
        subject=d.subject, body=d.body, tone=d.tone, total_amount=d.total_amount,
        invoice_ids=[int(i) for i in d.invoice_ids.split(",") if i], source=d.source,
        warnings=d.warnings, created_at=d.created_at.isoformat())
    if d.status == DraftStatus.APPROVED:  # open in your own mail app; the system never sends
        view["mailto"] = (f"mailto:{d.to_email or ''}?subject={quote(d.subject)}"
                          f"&body={quote(d.body)}")
    return view


def _get(session: Session, draft_id: int) -> EmailDraft:
    d = session.get(EmailDraft, draft_id)
    if d is None:
        raise LookupError("Draft not found")
    return d


def edit_draft(session: Session, draft_id: int, subject: str, body: str) -> EmailDraft:
    d = _get(session, draft_id)
    if d.status != DraftStatus.DRAFT:
        raise WorkflowError(f"Only drafts can be edited (this one is {d.status.value})")
    d.subject, d.body = subject, body
    session.add(d)
    session.commit()
    return d


def move_draft(session: Session, draft_id: int, new_status: DraftStatus) -> EmailDraft:
    allowed = {DraftStatus.DRAFT: {DraftStatus.APPROVED, DraftStatus.REJECTED},
               DraftStatus.APPROVED: {DraftStatus.SENT, DraftStatus.REJECTED}}
    d = _get(session, draft_id)
    if new_status not in allowed.get(d.status, set()):
        raise WorkflowError(f"Cannot move a {d.status.value} draft to {new_status.value}")
    d.status = new_status
    d.reviewed_at = datetime.now(timezone.utc)
    session.add(d)
    session.commit()
    return d