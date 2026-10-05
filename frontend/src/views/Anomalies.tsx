import { useState } from "react";
import { api } from "../api";
import type { Flag, FlagStatus } from "../api";
import { Loading, Notice, Pill } from "../components/ui";
import { ledger, longDate } from "../format";
import { useAction, useData } from "../hooks";

const KIND: Record<Flag["kind"], string> = { duplicate: "Possible duplicate", outlier: "Unusually large" };
const FILTERS: { id: FlagStatus; label: string }[] = [
  { id: "open", label: "To review" },
  { id: "confirmed", label: "Confirmed problems" },
  { id: "dismissed", label: "Dismissed" },
];

export default function Anomalies({ version, onChanged }: { version: number; onChanged: () => void }) {
  const [status, setStatus] = useState<FlagStatus>("open");
  const flags = useData(() => api.flags(status), [version, status]);
  const scan = useAction();
  const review = useAction();
  const [note, setNote] = useState<string | null>(null);

  async function rescan() {
    const r = await scan.run(api.scan);
    if (r) {
      setNote(`Scan finished: ${r.duplicates} possible duplicates and ${r.outliers} unusually large invoices in total.`);
      onChanged();
    }
  }
  async function decide(f: Flag, s: "confirmed" | "dismissed") {
    const ok = await review.run(() => api.reviewFlag(f.flag_id, s));
    if (ok) onChanged();
  }

  return (
    <div className="stack">
      <section className="section" aria-labelledby="checks-h">
        <h2 id="checks-h">Flagged invoices</h2>
        <p className="lede">
          Rules look for repeated invoices and amounts far above what a counterparty normally bills. Confirm a flag if it is a real
          problem; dismiss it if the invoice is fine. Duplicates you leave unreviewed or confirm are kept out of the forecast.
        </p>
        <div className="toolbar">
          <div className="seg" role="group" aria-label="Filter flags">
            {FILTERS.map((f) => (
              <button key={f.id} type="button" aria-pressed={status === f.id} onClick={() => setStatus(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          <button className="btn" onClick={rescan} disabled={scan.busy}>
            {scan.busy ? "Scanning…" : "Scan invoices again"}
          </button>
        </div>
        {note && <Notice>{note}</Notice>}
        {scan.error && <Notice tone="error">{scan.error}</Notice>}
        {review.error && <Notice tone="error">{review.error}</Notice>}
        {flags.error && <Notice tone="error">{flags.error}</Notice>}
        {flags.loading && !flags.data && <Loading />}
        {flags.data && flags.data.length === 0 && <p className="muted">Nothing here.</p>}
        <ul className="flags">
          {(flags.data ?? []).map((f) => (
            <li key={f.flag_id} className="flag">
              <div className="flag-main">
                <div className="pills">
                  <Pill tone={f.severity === "high" ? "red" : "amber"}>{f.severity === "high" ? "High" : "Medium"}</Pill>
                  <Pill>{KIND[f.kind]}</Pill>
                </div>
                <h3>
                  {f.invoice_no} <span className="muted">{f.type === "AP" ? "vendor bill" : "customer invoice"}</span>
                </h3>
                <p>{f.explanation}</p>
                <p className="muted">
                  {f.counterparty}. {f.amount != null && ledger(f.amount)}
                  {f.issue_date && `, issued ${longDate(f.issue_date)}`}.
                </p>
              </div>
              {f.status === "open" ? (
                <div className="flag-actions">
                  <button className="btn" disabled={review.busy} onClick={() => decide(f, "confirmed")}>
                    Confirm problem
                  </button>
                  <button className="btn btn-quiet" disabled={review.busy} onClick={() => decide(f, "dismissed")}>
                    Dismiss
                  </button>
                </div>
              ) : (
                <Pill tone={f.status === "confirmed" ? "red" : "plain"}>{f.status === "confirmed" ? "Confirmed" : "Dismissed"}</Pill>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}