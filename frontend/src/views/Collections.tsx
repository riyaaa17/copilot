import { useState } from "react";
import { api } from "../api";
import type { DraftBatch } from "../api";
import DraftCard from "../components/DraftCard";
import { Amount, Loading, Notice, Pill } from "../components/ui";
import { BEHAVIOR, TONE, ledger } from "../format";
import { useAction, useData } from "../hooks";

export default function Collections({ version, onChanged }: { version: number; onChanged: () => void }) {
  const priorities = useData(api.priorities, [version]);
  const drafts = useData(api.drafts, [version]);
  const [top, setTop] = useState(5);
  const [useAi, setUseAi] = useState(true);
  const [batch, setBatch] = useState<DraftBatch | null>(null);
  const { busy, error, run } = useAction();

  async function createDrafts() {
    const result = await run(() => api.createDrafts(top, useAi));
    if (result) {
      setBatch(result);
      onChanged();
    }
  }

  const all = drafts.data ?? [];
  const waiting = all.filter((d) => d.status === "draft");
  const approved = all.filter((d) => d.status === "approved");
  const done = all.filter((d) => d.status === "sent" || d.status === "rejected");

  return (
    <div className="stack">
      <section className="section" aria-labelledby="chase-h">
        <h2 id="chase-h">Who to chase first</h2>
        <p className="lede">
          Customers are ranked by risk-weighted exposure: the amount, multiplied by how likely it is to stay unpaid, how
          late it is, and how the customer has paid before.
        </p>
        {priorities.error && <Notice tone="error">{priorities.error}</Notice>}
        {!priorities.data && !priorities.error && <Loading>Ranking overdue customers…</Loading>}
        {priorities.data && priorities.data.customers.length === 0 && <Notice>Nothing is overdue right now.</Notice>}
        {priorities.data && priorities.data.customers.length > 0 && (
          <table className="ledger">
            <thead>
              <tr>
                <th>Customer</th>
                <th className="r">Overdue</th>
                <th className="r">Invoices</th>
                <th className="r">Oldest</th>
                <th>Payment history</th>
                <th>Suggested approach</th>
              </tr>
            </thead>
            <tbody>
              {priorities.data.customers.map((c) => (
                <tr key={c.counterparty_id}>
                  <td>{c.customer}</td>
                  <td className="r num">{ledger(c.total_amount)}</td>
                  <td className="r num">{c.invoices.length}</td>
                  <td className="r num">{c.max_days_overdue} days</td>
                  <td>{BEHAVIOR[c.behavior] ?? c.behavior}</td>
                  <td>
                    <Pill tone={c.tone === "urgent" ? "red" : c.tone === "firm" ? "amber" : "green"}>
                      {TONE[c.tone] ?? c.tone}
                    </Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {priorities.data && priorities.data.held.length > 0 && (
          <Notice tone="warn">
            {priorities.data.held.length} overdue {priorities.data.held.length === 1 ? "invoice is" : "invoices are"} held
            back from emails until an unusual amount is checked:{" "}
            {priorities.data.held.map((h) => `${h.invoice_no} (${h.customer})`).join(", ")}.
          </Notice>
        )}
      </section>

      <section className="section" aria-labelledby="draft-h">
        <h2 id="draft-h">Follow-up emails</h2>
        <p className="lede">Drafts are never sent for you. You review, edit and approve each one, then send it from your own email.</p>
        <div className="toolbar">
          <label className="inline">
            Draft emails for the top
            <select value={top} onChange={(e) => setTop(Number(e.target.value))}>
              {[3, 5, 10].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            customers
          </label>
          <label className="inline">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            Use AI wording
          </label>
          <button className="btn btn-primary" onClick={createDrafts} disabled={busy}>
            {busy ? "Writing drafts…" : "Write drafts"}
          </button>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {batch && (
          <Notice>
            {batch.created.length === 0
              ? "No new drafts were needed."
              : `${batch.created.length} new ${batch.created.length === 1 ? "draft is" : "drafts are"} ready for review.`}{" "}
            {batch.skipped.length > 0 &&
              `${batch.skipped.length} ${batch.skipped.length === 1 ? "customer was" : "customers were"} skipped because a draft is already waiting.`}
            {useAi && !batch.llm_used && " AI wording isn't available, so standard wording was used."}
          </Notice>
        )}

        {drafts.error && <Notice tone="error">{drafts.error}</Notice>}
        <h3 className="sub">Waiting for your review ({waiting.length})</h3>
        {waiting.length === 0 && <p className="muted">No drafts are waiting. Write some above.</p>}
        <div className="draft-list">
          {waiting.map((d) => (
            <DraftCard key={d.draft_id} draft={d} onChanged={onChanged} />
          ))}
        </div>

        {approved.length > 0 && (
          <>
            <h3 className="sub">Approved, ready to send ({approved.length})</h3>
            <div className="draft-list">
              {approved.map((d) => (
                <DraftCard key={d.draft_id} draft={d} onChanged={onChanged} />
              ))}
            </div>
          </>
        )}

        {done.length > 0 && (
          <details className="done">
            <summary>Finished ({done.length})</summary>
            <ul className="plain-list">
              {done.map((d) => (
                <li key={d.draft_id}>
                  {d.customer}: <Amount value={d.total_amount} /> <Pill tone={d.status === "sent" ? "green" : "plain"}>{d.status === "sent" ? "Sent" : "Rejected"}</Pill>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
    </div>
  );
}