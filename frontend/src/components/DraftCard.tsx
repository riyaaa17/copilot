import { useState } from "react";
import { api } from "../api";
import type { Draft } from "../api";
import { useAction } from "../hooks";
import { ledger, TONE } from "../format";
import { Notice, Pill } from "./ui";

/** One follow-up email. Nothing here sends anything: approving only marks it ready for you to send. */
export default function DraftCard({ draft, onChanged }: { draft: Draft; onChanged: () => void }) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const { busy, error, run } = useAction();
  const editable = draft.status === "draft";
  const dirty = subject !== draft.subject || body !== draft.body;

  const save = () => run(async () => { await api.editDraft(draft.draft_id, subject, body); onChanged(); });
  const approve = () =>
    run(async () => {
      if (dirty) await api.editDraft(draft.draft_id, subject, body);
      await api.draftAction(draft.draft_id, "approve");
      onChanged();
    });
  const act = (a: "reject" | "mark-sent") => run(async () => { await api.draftAction(draft.draft_id, a); onChanged(); });

  return (
    <article className="draft" aria-label={`Email draft for ${draft.customer ?? "customer"}`}>
      <header className="draft-head">
        <div>
          <h3>{draft.customer}</h3>
          <p className="muted">
            To {draft.to_email ?? "no address on file"}. {ledger(draft.total_amount)} across {draft.invoice_ids.length}{" "}
            {draft.invoice_ids.length === 1 ? "invoice" : "invoices"}.
          </p>
        </div>
        <div className="pills">
          <Pill tone={draft.tone === "urgent" ? "red" : draft.tone === "firm" ? "amber" : "green"}>
            {TONE[draft.tone] ?? draft.tone}
          </Pill>
          <Pill>{draft.source === "llm" ? "Written by AI, figures checked" : "Standard wording"}</Pill>
        </div>
      </header>

      {draft.warnings && (
        <details className="why">
          <summary>Why the AI wording wasn't used</summary>
          <p>{draft.warnings}</p>
        </details>
      )}

      {editable ? (
        <>
          <label className="field">
            <span>Subject</span>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="field">
            <span>Message</span>
            <textarea rows={14} value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
        </>
      ) : (
        <div className="readonly">
          <p className="readonly-subject">{draft.subject}</p>
          <pre>{draft.body}</pre>
        </div>
      )}

      {error && <Notice tone="error">{error}</Notice>}

      <footer className="actions">
        {editable && (
          <>
            <button className="btn btn-primary" disabled={busy} onClick={approve}>
              {dirty ? "Save and approve" : "Approve"}
            </button>
            {dirty && (
              <button className="btn" disabled={busy} onClick={save}>
                Save edits
              </button>
            )}
            <button className="btn btn-quiet" disabled={busy} onClick={() => act("reject")}>
              Reject
            </button>
          </>
        )}
        {draft.status === "approved" && (
          <>
            {draft.mailto && (
              <a className="btn btn-primary" href={draft.mailto}>
                Open in your email app
              </a>
            )}
            <button className="btn" disabled={busy} onClick={() => act("mark-sent")}>
              Mark as sent
            </button>
            <button className="btn btn-quiet" disabled={busy} onClick={() => act("reject")}>
              Reject
            </button>
          </>
        )}
        {(draft.status === "sent" || draft.status === "rejected") && (
          <Pill tone={draft.status === "sent" ? "green" : "plain"}>{draft.status === "sent" ? "Sent" : "Rejected"}</Pill>
        )}
      </footer>
    </article>
  );
}