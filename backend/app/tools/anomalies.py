"""Deterministic anomaly detection: duplicate invoices and unusual amounts.

Rules and robust statistics, not an LLM: every flag comes with a plain-English reason
a finance person can check in seconds.
"""
from __future__ import annotations

import re
from itertools import combinations

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from sqlmodel import Session, delete, select

from app.models.tables import AnomalyFlag

DUP_THRESHOLD = 0.6     # minimum similarity to raise a duplicate flag
HIGH_CONFIDENCE = 0.85
FLAG_COLS = ["invoice_id", "related_invoice_id", "kind", "score", "severity", "explanation"]


# ---------- duplicates ----------
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s).lstrip("0")


def number_similarity(a, b) -> float:
    """1.0 same number; 0.9 same digits with a cosmetic change (INV-1042 / INV 01042 / INV-1042-A).

    Neighbouring numbers (INV-1042 vs INV-1043) score 0: sequential invoices are normal.
    """
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    if _digits(na) and _digits(na) == _digits(nb) and fuzz.ratio(na, nb) >= 85:
        return 0.9
    return 0.0


def _date_score(gap_days: int) -> float:
    return 1.0 if gap_days <= 7 else 0.5 if gap_days <= 30 else 0.0


def find_duplicates(inv: pd.DataFrame, names: dict[int, str]) -> pd.DataFrame:
    """Same counterparty and exact amount, then scored on invoice number and date proximity."""
    best: dict[int, dict] = {}
    live = inv[inv["status"] != "void"]
    for _, g in live.groupby(["type", "counterparty_id", "amount"]):
        if len(g) < 2:
            continue
        for a, b in combinations(g.sort_values("id").itertuples(), 2):
            gap = abs((b.issue_date - a.issue_date).days)
            num = number_similarity(a.invoice_no, b.invoice_no)
            score = round(0.4 + 0.4 * num + 0.2 * _date_score(gap), 3)
            if score < DUP_THRESHOLD:
                continue
            both_paid = a.status == "paid" and b.status == "paid"
            who = names.get(int(b.counterparty_id), str(b.counterparty_id))
            why = (f"{who}: {b.invoice_no} repeats {a.invoice_no} - same amount "
                   f"(${b.amount:,.2f}), issued {gap} day(s) apart")
            why += (", identical invoice number" if num == 1.0
                    else ", near-identical invoice number" if num > 0 else "")
            if both_paid:
                why += ". Both are paid: possible duplicate payment, check if cash can be recovered"
            row = dict(invoice_id=int(b.id), related_invoice_id=int(a.id), kind="duplicate",
                       score=score, severity="high" if score >= HIGH_CONFIDENCE or both_paid
                       else "medium", explanation=why)
            if int(b.id) not in best or score > best[int(b.id)]["score"]:
                best[int(b.id)] = row
    return pd.DataFrame(list(best.values()), columns=FLAG_COLS)


# ---------- outliers ----------
def find_outliers(inv: pd.DataFrame, names: dict[int, str], min_history: int = 5,
                  z_threshold: float = 3.5, min_ratio: float = 2.5) -> pd.DataFrame:
    """Amounts far above what this counterparty normally invoices.

    Uses the median and MAD on log(amount), so one huge invoice cannot hide itself by
    dragging the average up. It must also be at least min_ratio times the typical amount,
    so tiny statistical wobbles on very regular invoices are not flagged.
    """
    rows = []
    live = inv[inv["status"] != "void"]
    for (kind, cid), g in live.groupby(["type", "counterparty_id"]):
        if len(g) < min_history:
            continue
        logs = np.log(g["amount"].to_numpy(dtype=float))
        med = float(np.median(logs))
        mad = max(float(np.median(np.abs(logs - med))), 0.05)
        z = 0.6745 * (logs - med) / mad
        typical = float(np.exp(med))
        for inv_id, amount, zi in zip(g["id"], g["amount"], z):
            ratio = float(amount) / typical
            if zi >= z_threshold and ratio >= min_ratio:
                score = round(min(1.0, float(zi) / 10), 3)
                who = names.get(int(cid), str(cid))
                rows.append(dict(
                    invoice_id=int(inv_id), related_invoice_id=None, kind="outlier", score=score,
                    severity="high" if score >= HIGH_CONFIDENCE else "medium",
                    explanation=(f"{who}: ${amount:,.2f} is {ratio:.1f}x this {kind} "
                                 f"counterparty's typical invoice (${typical:,.2f})")))
    return pd.DataFrame(rows, columns=FLAG_COLS)


def scan(inv: pd.DataFrame, names: dict[int, str]) -> pd.DataFrame:
    flags = pd.concat([find_duplicates(inv, names), find_outliers(inv, names)],
                      ignore_index=True)
    return flags.sort_values("score", ascending=False).reset_index(drop=True)


# ---------- persistence ----------
def save_flags(session: Session, flags: pd.DataFrame) -> int:
    """Replace stored flags, but keep decisions a person already made."""
    decided = {(f.invoice_id, f.kind): f.status for f in session.exec(select(AnomalyFlag)).all()}
    session.exec(delete(AnomalyFlag))
    for r in flags.itertuples():
        session.add(AnomalyFlag(
            invoice_id=int(r.invoice_id),
            related_invoice_id=None if pd.isna(r.related_invoice_id) else int(r.related_invoice_id),
            kind=r.kind, score=float(r.score), severity=r.severity, explanation=r.explanation,
            status=decided.get((int(r.invoice_id), r.kind), "open")))
    session.commit()
    return len(flags)