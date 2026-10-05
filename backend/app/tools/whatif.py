"""What-if analysis: run the same forecast twice, once with a change, and explain the difference.

Baseline and scenario use the same random draws, so every difference comes from the change itself
and not from simulation noise. No LLM here.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.tools import reporting as rep
from app.tools.forecast import HORIZON_WEEKS, Forecast, Scenario


class CustomerPays(BaseModel):
    type: Literal["customer_pays"]
    counterparty_id: int
    weeks: int = Field(ge=1, le=HORIZON_WEEKS)


class CustomerFails(BaseModel):
    type: Literal["customer_fails"]
    counterparty_id: int


class CustomersPayLater(BaseModel):
    type: Literal["customers_pay_later"]
    days: int = Field(ge=-30, le=90)


class VendorsPaidLater(BaseModel):
    type: Literal["vendors_paid_later"]
    days: int = Field(ge=-30, le=90)


class ShiftRecurring(BaseModel):
    type: Literal["shift_recurring"]
    category: str = Field(min_length=1, max_length=40)
    days: int = Field(ge=-30, le=60)


class OneOff(BaseModel):
    type: Literal["one_off"]
    week: int = Field(ge=1, le=HORIZON_WEEKS)
    amount: float = Field(ge=-10_000_000, le=10_000_000)
    description: str = Field(min_length=1, max_length=60)


Effect = Annotated[
    Union[CustomerPays, CustomerFails, CustomersPayLater, VendorsPaidLater, ShiftRecurring, OneOff],
    Field(discriminator="type"),
]


def build_scenario(effects: list) -> Scenario:
    pay: dict[int, int] = {}
    fail: set[int] = set()
    ar = ap = 0
    shifts: dict[str, int] = {}
    one_offs: list[tuple[int, float, str]] = []
    for e in effects:
        if isinstance(e, CustomerPays):
            pay[e.counterparty_id] = e.weeks
        elif isinstance(e, CustomerFails):
            fail.add(e.counterparty_id)
        elif isinstance(e, CustomersPayLater):
            ar += e.days
        elif isinstance(e, VendorsPaidLater):
            ap += e.days
        elif isinstance(e, ShiftRecurring):
            shifts[e.category] = shifts.get(e.category, 0) + e.days
        elif isinstance(e, OneOff):
            one_offs.append((e.week, e.amount, e.description))
    for cid in fail:          # a customer who never pays cannot also pay in week N
        pay.pop(cid, None)
    return Scenario(pay_in_weeks=tuple(sorted(pay.items())), fail_customers=frozenset(fail),
                    ar_shift_days=ar, ap_shift_days=ap, recurring_shift_days=tuple(sorted(shifts.items())),
                    one_offs=tuple(one_offs))


def validate_effects(effects: list, customers: dict[int, str], categories: list[str]) -> None:
    """Raise ValueError (with a message for the user) if an effect refers to something that does not exist."""
    for e in effects:
        if isinstance(e, (CustomerPays, CustomerFails)) and e.counterparty_id not in customers:
            raise ValueError(f"Customer {e.counterparty_id} was not found among customers with open invoices.")
        if isinstance(e, ShiftRecurring) and e.category not in categories:
            raise ValueError(f"'{e.category}' is not a recurring payment in your data. Choose one of: "
                             f"{', '.join(categories)}.")


def _days(n: int) -> str:
    return f"{abs(n)} day{'s' if abs(n) != 1 else ''}"


def describe(effects: list, names: dict[int, str]) -> list[str]:
    """The assumptions, in plain English, so nobody has to guess what was simulated."""
    out = []
    for e in effects:
        if isinstance(e, CustomerPays):
            out.append(f"{names.get(e.counterparty_id, e.counterparty_id)} pays its already-overdue invoices in "
                       f"week {e.weeks}. Its other invoices follow its usual pattern.")
        elif isinstance(e, CustomerFails):
            out.append(f"{names.get(e.counterparty_id, e.counterparty_id)} never pays its open invoices "
                       f"and sends no new business.")
        elif isinstance(e, CustomersPayLater):
            out.append("Every customer pays as usual." if e.days == 0 else
                       f"Every customer pays {_days(e.days)} {'later' if e.days > 0 else 'earlier'} than usual.")
        elif isinstance(e, VendorsPaidLater):
            out.append("We pay vendors as usual." if e.days == 0 else
                       f"We pay every vendor {_days(e.days)} {'later' if e.days > 0 else 'earlier'} than usual.")
        elif isinstance(e, ShiftRecurring):
            label = rep.label(e.category).replace("_", " ").lower()
            out.append(f"The {label} payment is moved " + ("by 0 days." if e.days == 0 else
                       f"{_days(e.days)} {'later' if e.days > 0 else 'earlier'}."))
        elif isinstance(e, OneOff):
            what = "receipt of" if e.amount >= 0 else "payment of"
            out.append(f"A one-off {what} {rep.money(abs(e.amount))} in week {e.week}: {e.description}.")
    return out


def _driver_totals(fc: Forecast) -> dict[str, float]:
    totals: dict[str, float] = {}
    for w in fc.weeks:
        for name, v in w["drivers"].items():
            totals[name] = totals.get(name, 0.0) + v
    return totals


def compare(base: Forecast, alt: Forecast) -> dict:
    """Baseline against scenario: headline figures, week by week, and which drivers moved."""
    b, a = base.summary, alt.summary
    tb, ta = _driver_totals(base), _driver_totals(alt)
    drivers = sorted(
        (dict(driver=n, baseline=round(tb.get(n, 0.0), 2), scenario=round(ta.get(n, 0.0), 2),
              difference=round(ta.get(n, 0.0) - tb.get(n, 0.0), 2))
         for n in set(tb) | set(ta) if abs(ta.get(n, 0.0) - tb.get(n, 0.0)) >= 1),
        key=lambda d: -abs(d["difference"]))
    weeks = [dict(week=x["week"], start=x["start"], end=x["end"], baseline_p50=x["closing_p50"],
                  scenario_p10=y["closing_p10"], scenario_p50=y["closing_p50"], scenario_p90=y["closing_p90"],
                  difference=round(y["closing_p50"] - x["closing_p50"], 2))
             for x, y in zip(base.weeks, alt.weeks)]
    diff13 = round(a["closing_p50_week13"] - b["closing_p50_week13"], 2)
    threshold = 0.005 * max(abs(b["opening_cash"]), 1.0)
    verdict = "about the same" if abs(diff13) < threshold else ("better" if diff13 > 0 else "worse")
    headline = dict(
        cash_today=b["opening_cash"], verdict=verdict,
        week13=dict(baseline=b["closing_p50_week13"], scenario=a["closing_p50_week13"], difference=diff13),
        lowest_downside=dict(baseline=b["lowest_p10_balance"], baseline_week=b["lowest_p10_week"],
                             scenario=a["lowest_p10_balance"], scenario_week=a["lowest_p10_week"],
                             difference=round(a["lowest_p10_balance"] - b["lowest_p10_balance"], 2)),
        chance_negative=dict(baseline=b["prob_negative_cash_pct"], scenario=a["prob_negative_cash_pct"]),
        receivables_at_risk=dict(baseline=b["open_ar_at_risk_amount"], scenario=a["open_ar_at_risk_amount"]))
    return dict(headline=headline, weeks=weeks, drivers=drivers)


def summary_sentence(result: dict) -> str:
    """One correct sentence about the outcome, built from the numbers (never from a model)."""
    h = result["headline"]
    w13, low = h["week13"], h["lowest_downside"]
    if abs(w13["difference"]) < 1:
        first = f"Base-case cash at week 13 is unchanged at {rep.money(w13['scenario'])}."
    elif h["verdict"] == "about the same":
        first = (f"This change makes little difference: base-case cash at week 13 is {rep.money(w13['scenario'])}, "
                 f"against {rep.money(w13['baseline'])} without it.")
    else:
        direction = "higher" if w13["difference"] > 0 else "lower"
        first = (f"With this change, base-case cash at week 13 is {rep.money(w13['scenario'])}, "
                 f"{rep.money(abs(w13['difference']))} {direction} than the {rep.money(w13['baseline'])} "
                 f"without it.")
    if abs(low["difference"]) >= 1:
        move = "higher" if low["difference"] > 0 else "lower"
        first += (f" The lowest downside balance is {rep.money(low['scenario'])} in week {low['scenario_week']}, "
                  f"{rep.money(abs(low['difference']))} {move} than before.")
    return first