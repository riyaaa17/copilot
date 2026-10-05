import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import type { Briefing as BriefingDoc } from "../api";
import { Loading, Notice, Pill } from "../components/ui";
import { useAction, useData } from "../hooks";

export default function Briefing({ version }: { version: number }) {
  const latest = useData(async () => {
    const list = await api.reports();
    return list.length > 0 ? api.report(list[0].briefing_id) : null;
  }, [version]);
  const [fresh, setFresh] = useState<BriefingDoc | null>(null);
  const [useAi, setUseAi] = useState(true);
  const [copied, setCopied] = useState(false);
  const { busy, error, run } = useAction();

  const doc = fresh ?? latest.data;

  async function generate() {
    const r = await run(() => api.createReport(useAi));
    if (r) setFresh(r);
  }
  async function copy() {
    if (!doc) return;
    await navigator.clipboard.writeText(doc.markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="stack">
      <section className="section" aria-labelledby="brief-h">
        <h2 id="brief-h">Latest briefing</h2>
        <p className="lede">
          Every figure, table and recommended action is calculated from your data. If AI wording is on, a model writes only
          the two short summary paragraphs, and every figure in them is checked against the calculated ones.
        </p>
        <div className="toolbar">
          <label className="inline">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            Use AI wording
          </label>
          <button className="btn btn-primary" onClick={generate} disabled={busy}>
            {busy ? "Building briefing…" : doc ? "Build a new briefing" : "Build this week's briefing"}
          </button>
          {doc && (
            <button className="btn" onClick={copy}>
              {copied ? "Copied" : "Copy as text"}
            </button>
          )}
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {latest.error && !fresh && <Notice tone="error">{latest.error}</Notice>}
        {latest.loading && !doc && <Loading />}
        {!doc && !latest.loading && !latest.error && <Notice>No briefing yet. Build the first one above.</Notice>}
        {doc && (
          <>
            <div className="pills briefing-meta">
              <Pill tone={doc.source === "llm" ? "green" : "plain"}>
                {doc.source === "llm" ? "Summary written by AI, figures checked" : "Standard summary wording"}
              </Pill>
            </div>
            {doc.warnings && (
              <details className="why">
                <summary>Why the AI wording wasn't used</summary>
                <p>{doc.warnings}</p>
              </details>
            )}
            <article className="briefing">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.markdown}</ReactMarkdown>
            </article>
          </>
        )}
      </section>
    </div>
  );
}