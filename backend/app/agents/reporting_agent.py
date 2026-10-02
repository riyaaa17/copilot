"""Reporting Agent: gathers verified figures, asks an LLM to word two paragraphs, checks them."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Callable

import pandas as pd
from sqlmodel import Session, select

from app.agents import collections_agent as ca
from app.models.tables import AnomalyFlag, AppMeta, Briefing, DraftStatus, EmailDraft
from app.tools import analytics as an
from app.tools import collections_tools as col
from app.tools import forecast as fx
from app.tools import reporting as rep

LLMFn = Callable[[str, str], str]

SYSTEM_PROMPT = """You are a finance analyst writing two short paragraphs for a weekly CFO briefing.
Rules:
- Use ONLY the facts in the JSON. Copy every dollar amount, percentage, day count and week number
  EXACTLY as written there (for example "$747,934", "10.3%", "45.3 days", "week 9"). Never round,
  abbreviate (no "k" or "M"), convert, add up, or invent a figure. If unsure about a number, leave it out.
- cash_today is the balance right now. forecast_week13_base_case is a PROJECTION of the future. Say what
  the forecast predicts ("is forecast to fall to ..."). Never say today's cash is "down from" a forecast.
- Reuse the wording in ready_made_sentences where it fits.
- Plain English a non-finance reader can follow. No markdown, no bullet points, no jargon.
- Be direct about risk and about good news. Do not give advice; actions are handled separately.
Respond with ONLY a JSON object:
{"summary": "<under 110 words: cash today, the 13-week outlook, the biggest risk>",
 "variance_commentary": "<under 90 words: last week's actual vs forecast, what explains the gap, week-over-week moves>"}"""


def smart_llm() -> LLMFn | None:
    """The stronger Groq model (briefings are read by a CFO), or None without an API key."""
    from app.config import get_settings
    if not get_settings().groq_api_key:
        return None
    from app import llm
    return lambda system, prompt: llm.chat(prompt, system=system, fast=False, max_tokens=3000)


def _parse(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            d = json.loads(candidate)
            if isinstance(d, dict) and d.get("summary") and d.get("variance_commentary"):
                return dict(summary=str(d["summary"]).strip(),
                            variance_commentary=str(d["variance_commentary"]).strip())
        except (ValueError, TypeError):
            continue
    return None


def narrate(facts: dict, llm_fn: LLMFn | None, attempts: int = 2) -> tuple[dict, str, str | None]:
    """(narrative, source, warnings): the model's wording if every figure checks out, else a template."""
    warnings: list[str] = []
    facts_text = json.dumps(facts, indent=1)
    if llm_fn is not None:
        for n in range(attempts):
            try:
                parsed = _parse(llm_fn(SYSTEM_PROMPT, "FACTS:\n" + facts_text))
            except Exception as exc:
                warnings.append(f"attempt {n + 1}: LLM call failed ({type(exc).__name__})")
                continue
            if parsed is None:
                warnings.append(f"attempt {n + 1}: reply was not valid JSON")
                continue
            problems = rep.check_narrative(facts_text, parsed)
            if not problems:
                return parsed, "llm", None
            warnings.append(f"attempt {n + 1}: " + "; ".join(problems))
    return rep.template_narrative(facts), "template", " | ".join(warnings) or None


# ---------- gather every figure ----------
def build_briefing_data(session: Session) -> dict:
    opening_row = session.get(AppMeta, "opening_balance")
    if opening_row is None:
        raise LookupError("No data loaded. Call POST /api/ingest/sample first.")
    opening = float(opening_row.value)
    inv, bank, as_of = an.load_invoices(session), fx.load_bank(session), an.get_as_of(session)
    cash = an.cash_balance(session)
    dup = fx.flagged_invoice_ids(session, "duplicate")
    held = fx.flagged_invoice_ids(session, "outlier")

    # now, and as the books stood a week ago
    fc = fx.run_forecast(inv, bank, as_of, cash, 1000, 42, exclude_ids=dup, learn_exclude_ids=held)
    kpis = rep.kpi_snapshot(inv[~inv["id"].isin(dup)], cash, as_of)
    prev = as_of - timedelta(days=7)
    inv_p, bank_p = rep.rewind(inv, bank, prev)
    cash_p = opening + float(bank_p["amount"].sum())
    kpis_prev = rep.kpi_snapshot(inv_p[~inv_p["id"].isin(dup)], cash_p, prev)

    # last week's forecast against what actually happened
    fc_prev = fx.run_forecast(inv_p, bank_p, prev, cash_p, 500, 7, exclude_ids=dup,
                              learn_exclude_ids=held)
    actual = bank[(bank["txn_date"] > pd.Timestamp(prev)) & (bank["txn_date"] <= pd.Timestamp(as_of))]
    variance = rep.variance_table(fc_prev.weeks[0], actual)

    # collections (same logic and defaults as /api/collections/priorities)
    prio, _ = ca.build_priorities(session, 500)
    customers = col.group_by_customer(prio)
    top = [dict(customer=c["customer"], total_amount=c["total_amount"], exposure=c["exposure"],
                max_days_overdue=c["max_days_overdue"], invoice_count=len(c["invoices"]),
                tier=c["tier"], tone=c["tone"]) for c in customers[:5]]

    # anomalies awaiting a decision
    by_id = inv.set_index("id")
    flags = session.exec(select(AnomalyFlag).where(AnomalyFlag.status != "dismissed")).all()
    a = dict(dup_ar_count=0, dup_ar_amount=0.0, dup_ap_count=0, dup_ap_amount=0.0,
             double_paid_count=0, double_paid_amount=0.0, outlier_count=0, outlier_amount=0.0)
    for f in flags:
        if f.invoice_id not in by_id.index:
            continue
        row = by_id.loc[f.invoice_id]
        if f.kind == "outlier":
            a["outlier_count"] += 1
            a["outlier_amount"] += float(row["amount"])
        elif "Both are paid" in f.explanation:
            a["double_paid_count"] += 1
            a["double_paid_amount"] += float(row["amount"])
        elif row["status"] == "open":
            key = "dup_ar" if row["type"] == "AR" else "dup_ap"
            a[key + "_count"] += 1
            a[key + "_amount"] += float(row["amount"])
    a = {k: round(v, 2) if isinstance(v, float) else v for k, v in a.items()}

    drafts = session.exec(select(EmailDraft).where(EmailDraft.status == DraftStatus.DRAFT)).all()
    data = dict(
        as_of=as_of.isoformat(), kpis=kpis, kpis_prev=kpis_prev, forecast=fc.summary,
        variance=variance,
        variance_period=f"Forecast made on {prev.isoformat()} for {(prev + timedelta(days=1)).isoformat()} "
                        f"to {as_of.isoformat()}, compared with actual bank activity.",
        outflow_weeks=rep.outflow_weeks(fc.weeks),
        collections=dict(top_customers=top, at_risk_amount=fc.summary["open_ar_at_risk_amount"],
                         at_risk_count=fc.summary["open_ar_at_risk_count"],
                         held_count=int(prio["hold_reason"].notna().sum())),
        anomalies=a, drafts_pending=dict(count=len(drafts),
                                         amount=round(sum(d.total_amount for d in drafts), 2)))
    data["actions"] = rep.build_actions(data)
    return data


def generate_briefing(session: Session, use_llm: bool = True, llm_fn: LLMFn | None = None) -> Briefing:
    data = build_briefing_data(session)
    if llm_fn is None and use_llm:
        llm_fn = smart_llm()
    narrative, source, warnings = narrate(rep.narrative_facts(data), llm_fn)
    briefing = Briefing(as_of=pd.Timestamp(data["as_of"]).date(),
                        markdown=rep.render_markdown(data, narrative, source),
                        data_json=json.dumps(data, default=float), source=source, warnings=warnings)
    session.add(briefing)
    session.commit()
    session.refresh(briefing)
    return briefing