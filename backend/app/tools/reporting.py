"""Deterministic building blocks for the weekly CFO briefing. No LLM in this file.

Every number in the briefing is computed here. The model may only word two paragraphs,
and check_narrative() rejects any figure that was not in the facts we gave it.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pandas as pd

from app.tools import analytics as an
from app.tools.forecast import INVOICE_CATEGORIES

BUFFER_RATIO = 0.5  # flag a cash dip when the downside (P10) low is below half of today's cash
NUM_TOKEN = re.compile(r"-?\$\s?\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?%")
SOURCES = ("/api/forecast, /api/analytics/kpis, /api/analytics/aging, /api/collections/priorities, "
           "/api/anomalies")


# ---------- formatting ----------
def money(x) -> str:
    x = float(x)
    return f"{'-' if round(x) < 0 else ''}${abs(round(x)):,.0f}"


def signed_money(x) -> str:
    x = float(x)
    return ("+" if round(x) > 0 else "") + money(x)


def pct(x) -> str:
    return f"{float(x):.1f}%"


def label(name: str) -> str:
    """'Recurring: payroll' -> 'Payroll' (driver names read better without the prefix)."""
    name = name.replace("Recurring: ", "")
    return name[:1].upper() + name[1:]


def _num(x, nd=1) -> str:
    return "n/a" if x is None else f"{float(x):.{nd}f}"


# ---------- data ----------
def rewind(inv: pd.DataFrame, bank: pd.DataFrame, cutoff) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The books as they stood on `cutoff`: later invoices gone, later payments undone."""
    c = pd.Timestamp(cutoff)
    inv_c = inv[inv["issue_date"] <= c].copy()
    later = inv_c["paid_date"] > c
    inv_c.loc[later, "status"] = "open"
    inv_c.loc[later, "paid_date"] = pd.NaT
    return inv_c, bank[bank["txn_date"] <= c]


def kpi_snapshot(inv: pd.DataFrame, cash: float, as_of) -> dict:
    ar, ap = an.aging_report(inv, as_of, "AR"), an.aging_report(inv, as_of, "AP")
    wc = an.working_capital_days(inv, as_of)
    return dict(cash=round(float(cash), 2), open_ar=ar["total_open"], overdue_ar=ar["total_overdue"],
                overdue_pct=ar["overdue_pct"],
                ar_90_plus=next(b["amount"] for b in ar["buckets"] if b["bucket"] == "90+"),
                open_ap=ap["total_open"], dso=wc["dso"], dpo=wc["dpo"], aging=ar["buckets"])


def variance_table(fc_week: dict, actual: pd.DataFrame) -> list[dict]:
    """Last week's forecast (made a week ago) against what really hit the bank."""
    d = fc_week["drivers"]

    def fsum(prefix): return sum(v for n, v in d.items() if n.startswith(prefix))

    def asum(mask): return float(actual.loc[mask, "amount"].sum())

    cat = actual["category"]
    lines = [
        ("Customer collections", fsum("Collections"), asum(cat == "customer_receipt")),
        ("Vendor payments", fsum("Vendor"), asum(cat == "vendor_payment")),
        ("Payroll, rent, tax and fees", fsum("Recurring"),
         asum(~cat.isin(INVOICE_CATEGORIES) & cat.notna())),
    ]
    lines.append(("Net cash flow", sum(f for _, f, _ in lines), sum(a for _, _, a in lines)))
    return [dict(name=n, forecast=round(f, 2), actual=round(a, 2), variance=round(a - f, 2),
                 variance_pct=None if abs(f) < 1 else round((a - f) / abs(f) * 100, 1))
            for n, f, a in lines]


def outflow_weeks(weeks: list[dict], n: int = 3) -> list[dict]:
    """The weeks with the weakest net cash flow, and what drives each."""
    out = []
    for w in sorted(weeks, key=lambda w: w["net"])[:n]:
        negatives = sorted(((k, v) for k, v in w["drivers"].items() if v < 0), key=lambda kv: kv[1])
        out.append(dict(week=w["week"], start=w["start"], end=w["end"], net=w["net"],
                        drivers=[dict(name=k, amount=v) for k, v in negatives[:3]]))
    return sorted(out, key=lambda w: w["week"])


# ---------- recommended actions (rules, so they are always grounded) ----------
def build_actions(data: dict) -> list[dict]:
    k, f, col, an_, dr = (data["kpis"], data["forecast"], data["collections"],
                          data["anomalies"], data["drafts_pending"])
    acts: list[dict] = []

    def add(priority, text): acts.append(dict(priority=priority, text=text))

    if f["prob_negative_cash_pct"] >= 5:
        add("high", f"Cash shortfall risk: {pct(f['prob_negative_cash_pct'])} of simulations end a week "
                    f"below zero. Line up funding or defer payments before week {f['lowest_p10_week']}.")
    elif k["cash"] > 0 and f["lowest_p10_balance"] < BUFFER_RATIO * k["cash"]:
        add("medium", f"Plan for a cash dip: in the downside case cash falls to "
                      f"{money(f['lowest_p10_balance'])} in week {f['lowest_p10_week']} "
                      f"(today {money(k['cash'])}). Protect that week's funds.")
    if data["outflow_weeks"]:
        w = min(data["outflow_weeks"], key=lambda w: w["net"])
        main = label(w["drivers"][0]["name"]).lower() if w["drivers"] else "outflows"
        add("medium", f"Largest net outflow ahead is week {w['week']} ({w['start']} to {w['end']}): "
                      f"{money(w['net'])}, mainly {main}. Make sure funds are in place.")
    if dr["count"]:
        add("high", f"Review and approve {dr['count']} collection email draft(s) covering "
                    f"{money(dr['amount'])}; nothing is sent until you approve.")
    elif col["top_customers"]:
        t = col["top_customers"][0]
        add("high", f"Generate collection drafts: {money(k['overdue_ar'])} is overdue "
                    f"({pct(k['overdue_pct'])} of open receivables). Start with {t['customer']} "
                    f"({money(t['total_amount'])}, up to {t['max_days_overdue']} days late).")
    for t in [c for c in col["top_customers"] if c["tier"] == "final_notice"][:2]:
        add("high", f"Call {t['customer']}: {t['invoice_count']} invoice(s), {money(t['total_amount'])}, "
                    f"up to {t['max_days_overdue']} days overdue. Agree a payment date or consider a credit hold.")
    if an_["dup_ap_count"]:
        add("high", f"Hold payment on {an_['dup_ap_count']} flagged duplicate vendor bill(s) "
                    f"({money(an_['dup_ap_amount'])}) until verified.")
    if an_["dup_ar_count"]:
        add("medium", f"Verify or void {an_['dup_ar_count']} duplicate customer invoice(s) "
                      f"({money(an_['dup_ar_amount'])}); receivables are overstated until resolved.")
    if an_["double_paid_count"]:
        add("high", f"Possible duplicate payments: {an_['double_paid_count']} invoice(s), "
                    f"{money(an_['double_paid_amount'])}. Request a refund or credit.")
    if an_["outlier_count"]:
        add("medium", f"Verify {an_['outlier_count']} unusually large invoice(s) "
                      f"({money(an_['outlier_amount'])}) before chasing or paying them.")
    if data["kpis_prev"]["overdue_pct"] is not None and \
            k["overdue_pct"] - data["kpis_prev"]["overdue_pct"] >= 2:
        add("medium", f"The overdue share rose from {pct(data['kpis_prev']['overdue_pct'])} to "
                      f"{pct(k['overdue_pct'])} in a week; tighten follow-up on 15 to 45 day invoices.")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(acts, key=lambda a: order[a["priority"]])


# ---------- narrative: facts in, words out, numbers verified ----------
def narrative_facts(data: dict) -> dict:
    k, kp, f, c = data["kpis"], data["kpis_prev"], data["forecast"], data["collections"]
    net = next(v for v in data["variance"] if v["name"] == "Net cash flow")
    dso_move = None if k["dso"] is None or kp["dso"] is None else round(k["dso"] - kp["dso"], 1)
    return dict(
        week_ending=data["as_of"],
        cash_today=money(k["cash"]),
        cash_change_vs_last_week=money(k["cash"] - kp["cash"]),
        forecast_week13_base_case=money(f["closing_p50_week13"]),
        forecast_direction_vs_today="down" if f["change_pct"] < 0 else "up",
        forecast_change_vs_today=pct(abs(f["change_pct"])),
        downside_low_point=money(f["lowest_p10_balance"]),
        downside_low_point_week=f["lowest_p10_week"],
        chance_cash_goes_negative=pct(f["prob_negative_cash_pct"]),
        overdue_receivables=money(k["overdue_ar"]),
        overdue_share_of_open_receivables=pct(k["overdue_pct"]),
        receivables_over_90_days=money(k["ar_90_plus"]),
        receivables_unlikely_to_be_collected_in_13_weeks=money(c["at_risk_amount"]),
        dso_days=k["dso"], dso_change_vs_last_week_days=dso_move,
        last_week_vs_forecast=dict(
            forecast_net_cash_flow=money(net["forecast"]), actual_net_cash_flow=money(net["actual"]),
            variance=money(net["variance"]),
            lines=[dict(name=v["name"], forecast=money(v["forecast"]), actual=money(v["actual"]),
                        variance=money(v["variance"])) for v in data["variance"][:-1]]),
        largest_net_outflow_week=(
            dict(week=data["outflow_weeks"][0]["week"], net=money(data["outflow_weeks"][0]["net"]))
            if data["outflow_weeks"] else None))


def template_narrative(facts: dict) -> dict:
    lw = facts["last_week_vs_forecast"]
    summary = (f"Cash today is {facts['cash_today']}. The base-case forecast ends week 13 at "
               f"{facts['forecast_week13_base_case']}, {facts['forecast_direction_vs_today']} "
               f"{facts['forecast_change_vs_today']} from today; in the downside case cash falls to "
               f"{facts['downside_low_point']} in week {facts['downside_low_point_week']}, with a "
               f"{facts['chance_cash_goes_negative']} chance of going negative. Overdue receivables are "
               f"{facts['overdue_receivables']} ({facts['overdue_share_of_open_receivables']} of open "
               f"receivables), of which {facts['receivables_over_90_days']} is more than 90 days late. "
               f"{facts['receivables_unlikely_to_be_collected_in_13_weeks']} is unlikely to be collected "
               f"within 13 weeks.")
    lines = "; ".join(f"{ln['name'].lower()} {ln['actual']} against {ln['forecast']} forecast"
                      for ln in lw["lines"])
    variance = (f"Last week net cash flow was {lw['actual_net_cash_flow']} against a forecast of "
                f"{lw['forecast_net_cash_flow']} (variance {lw['variance']}): {lines}. Cash moved "
                f"{facts['cash_change_vs_last_week']} over the week.")
    return dict(summary=summary, variance_commentary=variance)


def _tokens(text: str) -> set[str]:
    return {t.replace("-", "").replace(" ", "") for t in NUM_TOKEN.findall(text)}


def check_narrative(facts_text: str, narrative: dict) -> list[str]:
    """Reject any dollar amount or percentage that was not in the supplied facts."""
    allowed, problems = _tokens(facts_text), []
    for key in ("summary", "variance_commentary"):
        text = narrative.get(key, "")
        if not text.strip():
            problems.append(f"{key} is empty")
            continue
        for tok in sorted(_tokens(text) - allowed):
            problems.append(f"{key}: figure {tok} is not in the supplied facts")
        if re.search(r"\$\s?[\d.,]+\s?[kKmMbB]\b", text):
            problems.append(f"{key}: abbreviated amount; exact figures are required")
        if len(text.split()) > 200:
            problems.append(f"{key} is too long")
    return problems


# ---------- the document ----------
def render_markdown(data: dict, narrative: dict) -> str:
    k, kp, f, c, a = (data["kpis"], data["kpis_prev"], data["forecast"], data["collections"],
                      data["anomalies"])
    L = [f"# Weekly CFO Briefing - week ending {data['as_of']}", "", "## Summary", "",
         narrative["summary"], "", "## Key numbers", "",
         "| Metric | Now | Last week | Change |", "|---|---|---|---|",
         f"| Cash balance | {money(k['cash'])} | {money(kp['cash'])} | {signed_money(k['cash'] - kp['cash'])} |",
         f"| Open receivables | {money(k['open_ar'])} | {money(kp['open_ar'])} | {signed_money(k['open_ar'] - kp['open_ar'])} |",
         f"| Overdue receivables | {money(k['overdue_ar'])} | {money(kp['overdue_ar'])} | {signed_money(k['overdue_ar'] - kp['overdue_ar'])} |",
         f"| Overdue share | {pct(k['overdue_pct'])} | {pct(kp['overdue_pct'])} | {k['overdue_pct'] - kp['overdue_pct']:+.1f} pts |",
         f"| DSO (days) | {_num(k['dso'])} | {_num(kp['dso'])} | "
         f"{'n/a' if k['dso'] is None or kp['dso'] is None else format(k['dso'] - kp['dso'], '+.1f')} |",
         f"| Open payables | {money(k['open_ap'])} | {money(kp['open_ap'])} | {signed_money(k['open_ap'] - kp['open_ap'])} |",
         "", "## 13-week cash outlook", "",
         f"- Base case (P50) at week 13: **{money(f['closing_p50_week13'])}** "
         f"({f['change_pct']:+.1f}% vs today)",
         f"- Downside (P10) low point: **{money(f['lowest_p10_balance'])}** in week {f['lowest_p10_week']}",
         f"- Chance of cash going negative: {pct(f['prob_negative_cash_pct'])}", "",
         "**Weakest weeks ahead**", "", "| Week | Dates | Net cash flow | Main drivers |", "|---|---|---|---|"]
    for w in data["outflow_weeks"]:
        drivers = "; ".join(f"{label(d['name'])} {money(d['amount'])}" for d in w["drivers"])
        L.append(f"| {w['week']} | {w['start']} to {w['end']} | {money(w['net'])} | {drivers} |")
    L += ["", "## Last week: forecast vs actual", "",
          f"_{data['variance_period']}_", "",
          "| Line | Forecast | Actual | Variance |", "|---|---|---|---|"]
    for v in data["variance"]:
        pc = "" if v["variance_pct"] is None else f" ({v['variance_pct']:+.0f}%)"
        L.append(f"| {v['name']} | {money(v['forecast'])} | {money(v['actual'])} | "
                 f"{signed_money(v['variance'])}{pc} |")
    L += ["", narrative["variance_commentary"], "", "## Collections", "",
          "| Age (days overdue) | Invoices | Amount | Share |", "|---|---|---|---|"]
    for b in k["aging"]:
        L.append(f"| {b['bucket']} | {b['count']} | {money(b['amount'])} | {pct(b['pct'])} |")
    L += ["", f"{money(c['at_risk_amount'])} across {c['at_risk_count']} invoice(s) is unlikely to be "
              f"collected within 13 weeks."]
    if c["held_count"]:
        L.append(f"{c['held_count']} overdue invoice(s) are held back from chasing until their unusual "
                 f"amounts are verified.")
    if c["top_customers"]:
        L += ["", "**Who to chase first**", "",
              "| Customer | Overdue | Invoices | Worst (days) | Exposure | Tone |", "|---|---|---|---|---|---|"]
        for t in c["top_customers"]:
            L.append(f"| {t['customer']} | {money(t['total_amount'])} | {t['invoice_count']} | "
                     f"{t['max_days_overdue']} | {money(t['exposure'])} | {t['tone']} |")
    L += ["", "## Data quality and anomalies", "",
          f"- Duplicate customer invoices pending review: {a['dup_ar_count']} ({money(a['dup_ar_amount'])})",
          f"- Duplicate vendor bills pending review: {a['dup_ap_count']} ({money(a['dup_ap_amount'])})",
          f"- Possible duplicate payments: {a['double_paid_count']} ({money(a['double_paid_amount'])})",
          f"- Unusually large invoices pending review: {a['outlier_count']} ({money(a['outlier_amount'])})",
          "", "## Recommended actions", ""]
    for i, act in enumerate(data["actions"], 1):
        L.append(f"{i}. **{act['priority'].upper()}**: {act['text']}")
    L += ["", f"_Figures come from: {SOURCES}. The summary and commentary paragraphs are wording only; "
              f"every number in them is verified against these figures._"]
    return "\n".join(L)