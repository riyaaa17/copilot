"""Synthetic data generator for the CFO Copilot.

Run from the backend folder:
    uv run python data/generate_synthetic.py
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).parent / "synthetic"

N_CUSTOMERS = 50
N_VENDORS = 15
N_AR_INVOICES = 500
N_AP_INVOICES = 150
N_DUPLICATES = 8
N_OUTLIERS = 6

# personality: (mean days late, std dev, share of customers, chance of a very late/disputed invoice)
PERSONALITIES = {
    "prompt":   (-2, 3, 0.30, 0.00),
    "average":  (6, 6, 0.35, 0.00),
    "slow":     (22, 10, 0.20, 0.03),
    "chronic":  (45, 18, 0.10, 0.08),
    "disputer": (15, 12, 0.05, 0.25),
}

PREFIX = ["Apex", "Bluewave", "Cedar", "Delta", "Everest", "Fusion", "Granite", "Harbor",
          "Ironwood", "Juniper", "Keystone", "Lumen", "Meridian", "Northstar", "Orbit",
          "Pioneer", "Quartz", "Redwood", "Summit", "Titan", "Union", "Vertex", "Willow",
          "Zenith", "Atlas", "Beacon", "Crescent", "Dynamo", "Ember", "Falcon"]
SUFFIX = ["Industries", "Logistics", "Retail", "Foods", "Systems", "Holdings", "Partners",
          "Labs", "Manufacturing", "Solutions", "Trading", "Group"]

VENDOR_CATEGORIES = ["Software & SaaS", "Marketing", "Logistics", "Raw materials",
                     "Professional services", "Utilities", "Equipment"]
AR_CATEGORIES = ["Product sales", "Services", "Subscriptions"]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def build_counterparties(rng: np.random.Generator) -> pd.DataFrame:
    used: set[str] = set()

    def new_name() -> str:
        while True:
            name = f"{rng.choice(PREFIX)} {rng.choice(SUFFIX)}"
            if name not in used:
                used.add(name)
                return name

    p_names = list(PERSONALITIES)
    p_weights = [PERSONALITIES[p][2] for p in p_names]
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        name = new_name()
        rows.append(dict(
            id=i, name=name, kind="customer",
            email=f"ap@{slugify(name)}.example.com",
            payment_terms_days=int(rng.choice([15, 30, 45, 60], p=[0.10, 0.50, 0.25, 0.15])),
            personality=str(rng.choice(p_names, p=p_weights)),
        ))
    for j in range(N_VENDORS):
        name = new_name()
        rows.append(dict(
            id=N_CUSTOMERS + 1 + j, name=name, kind="vendor",
            email=f"billing@{slugify(name)}.example.com",
            payment_terms_days=int(rng.choice([30, 45], p=[0.7, 0.3])),
            personality="vendor",
        ))
    return pd.DataFrame(rows)


def build_ar(rng, cps: pd.DataFrame, as_of: date) -> list[dict]:
    customers = cps[cps.kind == "customer"]
    counts = np.maximum(1, rng.multinomial(
        N_AR_INVOICES, rng.dirichlet(np.full(len(customers), 3.0))))
    rows = []
    for c, cnt in zip(customers.itertuples(), counts):
        mean, sd, _, dispute = PERSONALITIES[c.personality]
        base = rng.lognormal(np.log(8000), 0.7)
        for _ in range(int(cnt)):
            issue = as_of - timedelta(days=int(rng.integers(0, 365)))
            due = issue + timedelta(days=c.payment_terms_days)
            delay = rng.normal(mean, sd)
            if rng.random() < dispute:
                delay += 120
            paid = max(due + timedelta(days=int(round(delay))), issue + timedelta(days=1))
            is_open = paid > as_of
            rows.append(dict(
                type="AR", counterparty_id=c.id, counterparty_name=c.name,
                issue_date=issue, due_date=due,
                paid_date=None if is_open else paid,
                amount=round(float(base * rng.lognormal(0, 0.25)), 2),
                currency="USD", category=str(rng.choice(AR_CATEGORIES)),
                status="open" if is_open else "paid",
            ))
    return rows


def build_ap(rng, cps: pd.DataFrame, as_of: date) -> list[dict]:
    vendors = cps[cps.kind == "vendor"].reset_index(drop=True)
    counts = np.maximum(1, rng.multinomial(
        N_AP_INVOICES, rng.dirichlet(np.full(len(vendors), 3.0))))
    rows = []
    for k, (v, cnt) in enumerate(zip(vendors.itertuples(), counts)):
        category = VENDOR_CATEGORIES[k % len(VENDOR_CATEGORIES)]
        base = rng.lognormal(np.log(5000), 0.8)
        for _ in range(int(cnt)):
            issue = as_of - timedelta(days=int(rng.integers(0, 365)))
            due = issue + timedelta(days=v.payment_terms_days)
            paid = max(due + timedelta(days=int(round(rng.normal(0, 3)))), issue + timedelta(days=1))
            is_open = paid > as_of
            rows.append(dict(
                type="AP", counterparty_id=v.id, counterparty_name=v.name,
                issue_date=issue, due_date=due,
                paid_date=None if is_open else paid,
                amount=round(float(base * rng.lognormal(0, 0.2)), 2),
                currency="USD", category=category,
                status="open" if is_open else "paid",
            ))
    return rows


def assemble_invoices(ar: list[dict], ap: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(ar + ap).sort_values(["issue_date", "type"]).reset_index(drop=True)
    df.insert(0, "invoice_id", df.index + 1)
    numbers = []
    for r in df.itertuples():
        numbers.append(f"INV-{1000 + r.invoice_id:05d}" if r.type == "AR"
                       else f"V{r.counterparty_id}-{r.invoice_id:04d}")
    df.insert(1, "invoice_no", numbers)
    return df


def inject_anomalies(rng, df: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    truth = []
    dup_src = rng.choice(df.index, size=N_DUPLICATES, replace=False)
    remaining = df.index.difference(dup_src)
    out_idx = rng.choice(remaining, size=N_OUTLIERS, replace=False)

    for i in out_idx:
        factor = float(rng.uniform(8, 15))
        df.loc[i, "amount"] = round(float(df.loc[i, "amount"]) * factor, 2)
        truth.append(dict(invoice_id=int(df.loc[i, "invoice_id"]), kind="outlier",
                          related_invoice_id=None, note=f"amount inflated x{factor:.1f}"))

    next_id = int(df.invoice_id.max()) + 1
    new_rows = []
    for n, i in enumerate(dup_src):
        row = df.loc[i].copy()
        gap = df.loc[i, "due_date"] - df.loc[i, "issue_date"]
        issue = min(as_of, df.loc[i, "issue_date"] + timedelta(days=int(rng.integers(0, 4))))
        row["invoice_id"] = next_id
        row["issue_date"], row["due_date"] = issue, issue + gap
        row["paid_date"], row["status"] = None, "open"
        no = str(df.loc[i, "invoice_no"])
        row["invoice_no"] = [no, no + "-A", no.replace("-", " "), no][n % 4]
        new_rows.append(row)
        truth.append(dict(invoice_id=next_id, kind="duplicate",
                          related_invoice_id=int(df.loc[i, "invoice_id"]),
                          note="copy of another invoice, same counterparty and amount"))
        next_id += 1

    df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df, pd.DataFrame(truth)


def month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def build_bank(rng, df: pd.DataFrame, as_of: date, start: date) -> tuple[pd.DataFrame, float]:
    rows = []
    for r in df[df.paid_date.notna()].itertuples():
        is_ar = r.type == "AR"
        rows.append(dict(
            txn_date=r.paid_date,
            description=f"{'ACH CREDIT' if is_ar else 'WIRE OUT'} {r.counterparty_name} {r.invoice_no}",
            amount=r.amount if is_ar else -r.amount,
            category="customer_receipt" if is_ar else "vendor_payment"))

    monthly_rev = float(df[df.type == "AR"].amount.sum()) / 12
    for m in month_starts(start, as_of):
        month_end = (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if start <= m <= as_of:
            rows.append(dict(txn_date=m, description="RENT - HQ LEASE",
                             amount=-round(monthly_rev * 0.06, 2), category="rent"))
        if start <= month_end <= as_of:
            rows.append(dict(txn_date=month_end, description="PAYROLL RUN",
                             amount=-round(monthly_rev * 0.55 * rng.uniform(0.97, 1.03), 2),
                             category="payroll"))
            rows.append(dict(txn_date=month_end, description="BANK FEES",
                             amount=-round(float(rng.uniform(50, 200)), 2), category="bank_fees"))
        d15 = m.replace(day=15)
        if m.month in (1, 4, 7, 10) and start <= d15 <= as_of:
            rows.append(dict(txn_date=d15, description="ESTIMATED TAX PAYMENT",
                             amount=-round(monthly_rev * 0.5, 2), category="tax"))

    bank = pd.DataFrame(rows).sort_values("txn_date").reset_index(drop=True)
    running = bank.amount.cumsum()
    opening = float(max(150_000, 50_000 - running.min()))
    bank.insert(0, "txn_id", bank.index + 1)
    bank["amount"] = bank.amount.round(2)
    return bank, round(opening, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    as_of, start = args.as_of, args.as_of - timedelta(days=365)

    cps = build_counterparties(rng)
    invoices = assemble_invoices(build_ar(rng, cps, as_of), build_ap(rng, cps, as_of))
    invoices, truth = inject_anomalies(rng, invoices, as_of)
    invoices = invoices.sort_values("invoice_id").reset_index(drop=True)
    bank, opening = build_bank(rng, invoices, as_of, start)

    OUT_DIR.mkdir(exist_ok=True)
    cps.drop(columns="personality").to_csv(OUT_DIR / "counterparties.csv", index=False)
    invoices.to_csv(OUT_DIR / "invoices.csv", index=False)
    bank.to_csv(OUT_DIR / "bank_transactions.csv", index=False)
    # Ground truth for testing agents. Agents must NEVER read these two files.
    truth.to_csv(OUT_DIR / "truth_anomalies.csv", index=False)
    cps[["id", "name", "personality"]].to_csv(OUT_DIR / "truth_personalities.csv", index=False)
    meta = dict(as_of=as_of.isoformat(), start=start.isoformat(), opening_balance=opening,
                current_balance=round(opening + float(bank.amount.sum()), 2))
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

    ar_open = invoices[(invoices.type == "AR") & (invoices.status == "open")]
    overdue = ar_open[pd.to_datetime(ar_open.due_date) < pd.Timestamp(as_of)]
    print(f"Wrote {OUT_DIR}")
    print(f"  counterparties: {len(cps)}   invoices: {len(invoices)}   bank txns: {len(bank)}")
    print(f"  open AR: {len(ar_open)} invoices, ${ar_open.amount.sum():,.0f}")
    print(f"  overdue AR: {len(overdue)} invoices, ${overdue.amount.sum():,.0f}")
    print(f"  injected: {N_DUPLICATES} duplicates, {N_OUTLIERS} outliers")
    print(f"  current cash balance: ${meta['current_balance']:,.0f}")


if __name__ == "__main__":
    main()