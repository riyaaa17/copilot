"""Deterministic collections logic: who to chase, in what order, in what tone.

No LLM here. The agent only asks a model to word the email; priorities are rules.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

BEHAVIOR_RISK = {"prompt": 0.0, "average": 0.33, "slow": 0.66, "chronic": 1.0,
                 "insufficient_data": 0.5}
PRIORITY_COLS = ["invoice_id", "invoice_no", "counterparty_id", "customer", "amount", "due_date",
                 "days_overdue", "p_collect_13w", "expected_pay_date", "behavior", "risk",
                 "exposure", "tier", "tone", "hold_reason"]
HOLD_MESSAGE = "Amount flagged as unusual - verify the invoice before contacting the customer"
FORBIDDEN = ("legal", "lawsuit", "attorney", "lawyer", "late fee", "interest", "penalty",
             "collections agency", "credit bureau")


def tier_for(days_overdue: int) -> tuple[str, str]:
    """(tier, tone) by how late the invoice is."""
    if days_overdue <= 14:
        return "reminder", "friendly"
    if days_overdue <= 45:
        return "follow_up", "firm"
    if days_overdue <= 90:
        return "escalation", "urgent"
    return "final_notice", "urgent"


def prioritize(open_ar: pd.DataFrame, inv: pd.DataFrame, profiles: pd.DataFrame,
               names: dict[int, str], held_ids=frozenset()) -> pd.DataFrame:
    """Rank overdue invoices by risk-weighted exposure = amount x risk.

    risk = 40% chance it will not be collected within 13 weeks (from the forecast)
         + 40% how long it is overdue (capped at 120 days)
         + 20% the customer's payment history
    """
    df = open_ar.merge(inv[["id", "due_date"]], left_on="invoice_id", right_on="id", how="left")
    df = df[df["days_overdue"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=PRIORITY_COLS)
    behavior = dict(zip(profiles["counterparty_id"], profiles["payment_behavior"]))
    df["customer"] = df["counterparty_id"].map(names)
    df["behavior"] = df["counterparty_id"].map(behavior).fillna("insufficient_data")
    df["risk"] = (0.4 * (1 - df["p_collect_13w"])
                  + 0.4 * np.minimum(df["days_overdue"], 120) / 120
                  + 0.2 * df["behavior"].map(BEHAVIOR_RISK)).round(3)
    df["exposure"] = (df["amount"] * df["risk"]).round(2)
    tiers = df["days_overdue"].map(tier_for)
    df["tier"] = tiers.map(lambda t: t[0])
    df["tone"] = tiers.map(lambda t: t[1])
    df["hold_reason"] = df["invoice_id"].isin(held_ids).map({True: HOLD_MESSAGE, False: None})
    df["due_date"] = pd.to_datetime(df["due_date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values("exposure", ascending=False)[PRIORITY_COLS].reset_index(drop=True)


def group_by_customer(prio: pd.DataFrame) -> list[dict]:
    """One entry per customer (one email, not one per invoice), biggest exposure first.

    Held invoices (flagged as unusual) are never included in an email.
    """
    out = []
    for cid, g in prio.groupby("counterparty_id"):
        ready = g[g["hold_reason"].isna()]
        if ready.empty:
            continue
        worst = int(ready["days_overdue"].max())
        tier, tone = tier_for(worst)
        out.append(dict(
            counterparty_id=int(cid), customer=str(ready.iloc[0]["customer"]),
            behavior=str(ready.iloc[0]["behavior"]), tier=tier, tone=tone,
            max_days_overdue=worst, total_amount=round(float(ready["amount"].sum()), 2),
            exposure=round(float(ready["exposure"].sum()), 2),
            invoices=[dict(invoice_id=int(r.invoice_id), invoice_no=r.invoice_no,
                           amount=round(float(r.amount), 2), due_date=r.due_date,
                           days_overdue=int(r.days_overdue))
                      for r in ready.sort_values("days_overdue", ascending=False).itertuples()]))
    return sorted(out, key=lambda c: c["exposure"], reverse=True)


# ---------- email drafting: template and safety checks ----------
def money(x: float) -> str:
    return f"${x:,.2f}"


_TONE = {
    "friendly": ("I hope you're well. This is a friendly reminder that the following "
                 "invoice(s) have passed their due date:",
                 "If payment is already on its way, please disregard this note and thank you."),
    "firm": ("I'm following up on the invoice(s) below, which are now past due:",
             "Could you confirm the payment date, or let us know if anything is holding it up?"),
    "urgent": ("I'm writing about the overdue invoice(s) below, which remain unpaid:",
               "Please confirm by return when payment will be made. If there is a dispute or "
               "a problem with any invoice, tell us today so we can resolve it quickly."),
}


def render_template(facts: dict) -> dict:
    intro, close = _TONE[facts["tone"]]
    lines = [f"- {i['invoice_no']}: {money(i['amount'])} (due {i['due_date']}, "
             f"{i['days_overdue']} days overdue)" for i in facts["invoices"]]
    body = (f"Hi {facts['customer']} team,\n\n{intro}\n\n" + "\n".join(lines)
            + f"\n\nTotal outstanding: {money(facts['total_amount'])}\n\n{close}\n\n"
            f"Kind regards,\n{facts['sender_name']}\n{facts['company_name']}")
    n = len(facts["invoices"])
    subject = (f"{'Payment reminder' if facts['tone'] == 'friendly' else 'Overdue invoice'}"
               f"{'s' if n > 1 else ''}: {money(facts['total_amount'])} outstanding")
    return dict(subject=subject, body=body)


def check_draft(facts: dict, subject: str, body: str) -> list[str]:
    """Guardrails: the email may only contain facts we gave it. Returns a list of problems."""
    problems = []
    text = f"{subject}\n{body}"
    for i in facts["invoices"]:
        if i["invoice_no"] not in body:
            problems.append(f"missing invoice number {i['invoice_no']}")
    allowed = {round(i["amount"], 2) for i in facts["invoices"]} | {round(facts["total_amount"], 2)}
    for raw in re.findall(r"\$\s?[\d,]+(?:\.\d+)?", text):
        value = round(float(raw.replace("$", "").replace(",", "").strip()), 2)
        if all(abs(value - a) > 0.005 for a in allowed):
            problems.append(f"amount {raw} is not one of the supplied amounts")
    if money(facts["total_amount"]) not in text and f"${facts['total_amount']:,.0f}" not in text:
        problems.append("total outstanding amount not stated")
    known_dates = {i["due_date"] for i in facts["invoices"]}
    for d in set(re.findall(r"\d{4}-\d{2}-\d{2}", text)):
        if d not in known_dates:
            problems.append(f"date {d} was not supplied")
    for word in FORBIDDEN:
        if word in text.lower():
            problems.append(f"contains forbidden wording '{word}'")
    n_words = len(body.split())
    if n_words < 20 or n_words > 250:
        problems.append(f"length {n_words} words is outside 20-250")
    return problems