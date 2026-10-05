"""Pure helpers for the chat orchestrator: cash-change decomposition, name matching, answer checks.

No LLM and no database access here, so everything is unit-testable.
"""
from __future__ import annotations

import re

import numpy as np
from rapidfuzz import fuzz, process, utils

from app.tools import reporting as rep
from app.tools.forecast import HORIZON_WEEKS

DRAFT_WORDS = ("draft", "email", "e-mail", "write", "remind", "follow up", "follow-up")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DOWN = re.compile(r"\b(down|fall|falls|fell|falling|drop|drops|dropped|decline|declines|declining|"
                   r"decrease|decreases|lower|shrink|shrinks|dip|dips)\b", re.IGNORECASE)
_UP = re.compile(r"\b(up|rise|rises|rose|rising|increase|increases|grow|grows|growth|higher|climb)\b",
                 re.IGNORECASE)
def claimed_pct_from_message(message: str) -> float | None:
    """A percentage the user states, signed by the direction word next to it.

    'Why is cash down 15% next month?' -> -15.0.  No percentage, or no direction -> None.
    Done in code, not by the model: models get this wrong and strict tool schemas reject blanks.
    """
    m = _PCT.search(message)
    if not m:
        return None
    value = float(m.group(1))
    if _DOWN.search(message):
        return -value
    if _UP.search(message):
        return value
    return None
def clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def explain_change(fc, weeks: int = 4, claimed_change_pct: float | None = None) -> dict:
    """Why will cash move over the next `weeks`? Driver amounts add up to the expected change."""
    weeks = clamp(weeks, 1, HORIZON_WEEKS, 4)
    opening = float(fc.opening_cash)
    sums = {name: float(arr[:, :weeks].sum(axis=1).mean()) for name, arr in fc.components.items()}
    expected_change = sum(sums.values())
    close = opening + expected_change
    change_pct = None if opening <= 0 else expected_change / opening * 100
    p10, p50, p90 = np.percentile(fc.balance[:, weeks - 1], [10, 50, 90])

    out = dict(
        horizon=f"{weeks} weeks", cash_today=rep.money(opening),
        expected_cash_at_end=rep.money(close), expected_change=rep.money(expected_change),
        direction="down" if expected_change < 0 else "up",
        change_pct=None if change_pct is None else rep.pct(abs(change_pct)),
        range_at_end=dict(downside_p10=rep.money(p10), base_p50=rep.money(p50),
                          upside_p90=rep.money(p90)),
        drivers=[dict(driver=driver_label(n), kind="outflow" if v < 0 else "inflow",
                      amount=rep.money(v))
                 for n, v in sorted(sums.items(), key=lambda kv: kv[1]) if abs(v) >= 1],
        weekly=[dict(week=f"week {w['week']}", dates=f"{w['start']} to {w['end']}",
                     net=rep.money(w["net"]),
                     main_driver=driver_label(max(w["drivers"].items(), key=lambda kv: abs(kv[1]))[0]))
                for w in fc.weeks[:weeks]],
        note="Driver amounts are averages across simulations and add up to expected_change.")
    if claimed_change_pct is not None and change_pct is not None:
        try:
            claimed = float(claimed_change_pct)
            gap = abs(claimed - change_pct)
            out["claim_check"] = ("The user's figure is about right." if gap <= 1.5 else
                                  f"The forecast shows cash {out['direction']} {out['change_pct']} over "
                                  f"{out['horizon']}, which differs from the user's {rep.pct(abs(claimed))}.")
        except (TypeError, ValueError):
            pass
    return out


def match_customer(query: str, names: dict[int, str], threshold: int = 70,
                   tie_margin: int = 5) -> list[tuple[int, str]]:
    """Fuzzy lookup that never guesses between look-alikes.

    'cedar' -> one match. 'ironwood' when both 'Ironwood Industries' and 'Ironwood Group' exist ->
    both, so the caller can ask which one was meant. Nothing close -> empty list.
    """
    if not names or not query.strip():
        return []
        # default_process ignores case and punctuation, so 'acme' finds 'Acme Corp'
    ranked = process.extract(query, names, scorer=fuzz.WRatio, processor=utils.default_process,
                             limit=5)  # [(name, score, id), ...]
    if not ranked or ranked[0][1] < threshold:
        return []
    cutoff = max(threshold, ranked[0][1] - tie_margin)
    return [(i, n) for n, score, i in ranked if score >= cutoff]


def driver_label(name: str) -> str:
    """'Recurring: bank_fees' -> 'Bank fees'."""
    return rep.label(name).replace("_", " ")


def user_wants_drafts(message: str) -> bool:
    """Permission gate for the only tool with a side effect."""
    m = message.lower()
    return any(w in m for w in DRAFT_WORDS)


def check_answer(answer: str, allowed_text: str) -> list[str]:
    """Every dollar amount, percentage, day and week figure must come from the tool results,
    the user's own words, or earlier turns."""
    if not answer.strip():
        return ["empty answer"]
    allowed = rep._tokens(allowed_text) | rep.ALWAYS_OK
    problems = [f"figure {t} is not in the tool results" for t in sorted(rep._tokens(answer) - allowed)]
    if rep.REVERSED.search(answer):
        problems.append("describes today's cash as moving from a forecast")
    if re.search(r"\$\s?[\d.,]+\s?[kKmMbB]\b", answer):
        problems.append("abbreviated amount; exact figures are required")
    if len(answer.split()) > 250:
        problems.append("answer is too long")
    return problems