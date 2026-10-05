import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatReply } from "../api";
import { errorMessage } from "../hooks";
import { Notice } from "./ui";

interface Msg {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta?: { source: ChatReply["source"]; verified: boolean; tools: string[] };
}

const TOOL_LABEL: Record<string, string> = {
  explain_cash_change: "Forecast drivers",
  get_cash_forecast: "Cash forecast",
  get_kpis: "Key figures",
  get_aging_report: "Aging report",
  get_top_overdue_customers: "Collections priorities",
  get_customer: "Customer lookup",
  get_anomalies: "Invoice checks",
  get_recommended_actions: "Weekly actions",
  draft_collection_emails: "Email drafting",
  what_if_customer_pays: "What-if forecast",
  what_if_customer_never_pays: "What-if forecast",
  what_if_customers_pay_later: "What-if forecast",
  what_if_pay_vendors_later: "What-if forecast",
  what_if_move_payment: "What-if forecast",
  what_if_one_off_cash: "What-if forecast",
};

const SUGGESTIONS = [
  "Why is cash down next month?",
  "Who should we chase first?",
  "Any suspicious invoices?",
  "What should I focus on this week?",
  "What if we delay payroll by a week?",
];

function provenance(meta: NonNullable<Msg["meta"]>): string {
  if (meta.source === "fallback") return "Built-in answer from the same figures";
  return meta.verified ? "Every figure checked against the data" : "Some figures could not be verified";
}

export default function Chat({ onDataChanged, onClose }: { onDataChanged: () => void; onClose: () => void }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, busy]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    const history = messages.slice(-10).map((m) => ({ role: m.role, content: m.content }));
    setMessages((m) => [...m, { id: nextId.current++, role: "user", content: question }]);
    setInput("");
    setError(null);
    setBusy(true);
    try {
      const r = await api.chat(question, history);
      setMessages((m) => [
        ...m,
        {
          id: nextId.current++,
          role: "assistant",
          content: r.answer,
          meta: { source: r.source, verified: r.verified, tools: r.trace.map((t) => t.tool) },
        },
      ]);
      if (r.trace.some((t) => t.tool === "draft_collection_emails" && t.ok)) onDataChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="chat" aria-label="Ask the assistant">
      <header className="chat-head">
        <h2>Ask about your numbers</h2>
        <button className="btn btn-quiet" onClick={onClose} aria-label="Close the assistant">
          Close
        </button>
      </header>

      <div className="chat-log" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>
              Answers come from your forecast, receivables and invoice checks. Every dollar amount in an answer is checked against
              them before you see it.
            </p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg msg-${m.role}`}>
            <p>{m.content}</p>
            {m.meta && (
              <p className="msg-meta">
                {provenance(m.meta)}.
                {m.meta.tools.length > 0 && ` Looked at: ${[...new Set(m.meta.tools)].map((t) => TOOL_LABEL[t] ?? t).join(", ")}.`}
              </p>
            )}
          </div>
        ))}
        {busy && <p className="msg-meta" role="status">Working it out…</p>}
        {error && <Notice tone="error">{error}</Notice>}
        <div ref={endRef} />
      </div>

      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <label className="sr-only" htmlFor="chat-input">
          Your question
        </label>
        <textarea
          id="chat-input"
          rows={2}
          value={input}
          placeholder="Ask about cash, customers or invoices"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !input.trim()}>
          Ask
        </button>
      </form>
    </aside>
  );
}