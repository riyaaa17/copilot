import { useCallback, useState } from "react";
import { api } from "./api";
import Chat from "./components/Chat";
import { Loading, Notice } from "./components/ui";
import { longDate } from "./format";
import { useAction, useData } from "./hooks";
import Anomalies from "./views/Anomalies";
import Briefing from "./views/Briefing";
import Collections from "./views/Collections";
import Overview from "./views/Overview";
import WhatIf from "./views/WhatIf";

type View = "overview" | "whatif" | "collections" | "anomalies" | "briefing";

const NAV: { id: View; label: string; note: string }[] = [
  { id: "overview", label: "Cash outlook", note: "Forecast and key figures" },
  { id: "whatif", label: "What if", note: "Test a change before you make it" },
  { id: "collections", label: "Collections", note: "Who to chase, emails to approve" },
  { id: "anomalies", label: "Invoice checks", note: "Duplicates and odd amounts" },
  { id: "briefing", label: "Weekly briefing", note: "The CFO summary" },
];

function Onboarding({ onLoaded }: { onLoaded: () => void }) {
  const { busy, error, run } = useAction();
  return (
    <div className="onboard">
      <h1>No data yet</h1>
      <p>
        Load the sample company to explore: about 660 invoices and 615 bank transactions across 50 customers and 15 vendors. Loading
        checks every row, then scans for duplicate and unusually large invoices.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <button
        className="btn btn-primary"
        disabled={busy}
        onClick={async () => {
          await run(async () => {
            await api.loadSample();
            onLoaded();
          });
        }}
      >
        {busy ? "Loading and checking…" : "Load the sample company"}
      </button>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [version, setVersion] = useState(0);
  // On a phone or small laptop the assistant is an overlay, so start with it closed.
  const [chatOpen, setChatOpen] = useState(
    () => typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 1281px)").matches,
  );
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const summary = useData(api.summary, [version]);

  const hasData = (summary.data?.invoices ?? 0) > 0;
  const asOf = summary.data?.meta.as_of;

  return (
    <div className={`app ${chatOpen && hasData ? "with-chat" : ""}`}>
      <nav className="nav" aria-label="Sections">
        <p className="brand">CFO Copilot</p>
        <ul>
          {NAV.map((n) => (
            <li key={n.id}>
              <button className="nav-item" aria-current={view === n.id ? "page" : undefined} onClick={() => setView(n.id)}>
                <span>{n.label}</span>
                <small>{n.note}</small>
              </button>
            </li>
          ))}
        </ul>
        {asOf && <p className="nav-foot">Data as of {longDate(asOf)}</p>}
      </nav>

      <main className="main">
        {summary.error && (
          <div className="onboard">
            <h1>Can't reach the backend</h1>
            <Notice tone="error">{summary.error}</Notice>
            <p>
              Start it from the <code>backend</code> folder with <code>uv run uvicorn app.main:app --reload --port 8002</code>, then
              try again.
            </p>
            <button className="btn btn-primary" onClick={refresh}>
              Try again
            </button>
          </div>
        )}
        {!summary.error && !summary.data && <Loading>Connecting…</Loading>}
        {summary.data && !hasData && <Onboarding onLoaded={refresh} />}
        {hasData && (
          <>
            <header className="page-head">
              <h1>{NAV.find((n) => n.id === view)?.label}</h1>
              {!chatOpen && (
                <button className="btn" onClick={() => setChatOpen(true)}>
                  Ask about your numbers
                </button>
              )}
            </header>
            {view === "overview" && <Overview version={version} />}
            {view === "whatif" && <WhatIf version={version} />}
            {view === "collections" && <Collections version={version} onChanged={refresh} />}
            {view === "anomalies" && <Anomalies version={version} onChanged={refresh} />}
            {view === "briefing" && <Briefing version={version} />}
          </>
        )}
      </main>

      {chatOpen && hasData && <Chat onDataChanged={refresh} onClose={() => setChatOpen(false)} />}
    </div>
  );
}