"""Developer tool: grade the Anomaly detector against the planted answer key.

Run after POST /api/anomalies/scan:   uv run python -m data.grade_anomalies
The agents themselves must never read truth_anomalies.csv.
"""
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from app.db import engine
from app.models.tables import AnomalyFlag

truth = pd.read_csv(Path(__file__).parent / "synthetic" / "truth_anomalies.csv")
with Session(engine) as s:
    flags = pd.DataFrame([dict(invoice_id=f.invoice_id, kind=f.kind, score=f.score)
                          for f in s.exec(select(AnomalyFlag)).all()],
                         columns=["invoice_id", "kind", "score"])

for kind in ("duplicate", "outlier"):
    t = set(truth.loc[truth.kind == kind, "invoice_id"])
    f = set(flags.loc[flags.kind == kind, "invoice_id"])
    tp = len(t & f)
    precision = tp / len(f) if f else 0.0
    recall = tp / len(t) if t else 0.0
    print(f"{kind:<10} planted={len(t):>2} flagged={len(f):>2} caught={tp:>2} "
          f"precision={precision:.0%} recall={recall:.0%}")
    if t - f:
        print("   missed:", sorted(t - f))
    if f - t:
        print("   false alarms:", sorted(f - t))