import { useRef, useState } from "react";
import { api } from "../api";
import type { Effect, WhatIfOptions, WhatIfResult } from "../api";
import { ScenarioChart } from "../components/charts";
import { Loading, Notice, Pill } from "../components/ui";
import { change, driverName, ledger, money, pct } from "../format";
import { useAction, useData } from "../hooks";

type Draft =
  | { id: number; type: "customer_pays"; customer: string; weeks: number }
  | { id: number; type: "customer_fails"; customer: string }
  | { id: number; type: "customers_pay_later"; days: number }
  | { id: number; type: "vendors_paid_later"; days: number }
  | { id: number; type: "shift_recurring"; category: string; days: number }
  | { id: number; type: "one_off"; week: number; direction: "out" | "in"; amount: number; description: string };

type DraftType = Draft["type"];

const TYPE_LABEL: Record<DraftType, string> = {
  customer_pays: "A customer pays its overdue invoices",
  customer_fails: "A customer stops paying",
  customers_pay_later: "All customers pay later or earlier",
  vendors_paid_later: "We pay vendors later or earlier",
  shift_recurring: "Move a regular payment",
  one_off: "A one-off cash event",
};

function blank(id: number, type: DraftType, o: WhatIfOptions): Draft {
  const customer = o.customers[0] ? String(o.customers[0].id) : "";
  switch (type) {
    case "customer_pays": return { id, type, customer, weeks: 1 };
    case "customer_fails": return { id, type, customer };
    case "customers_pay_later": return { id, type, days: 10 };
    case "vendors_paid_later": return { id, type, days: 15 };
    case "shift_recurring":
      return { id, type, category: o.recurring_categories.includes("payroll") ? "payroll" : (o.recurring_categories[0] ?? ""), days: 7 };
    case "one_off": return { id, type, week: 3, direction: "out", amount: 50000, description: "Equipment purchase" };
  }
}

/** Turn what is on screen into what the API expects, or say what is missing. */
function toEffect(d: Draft): Effect | string {
  switch (d.type) {
    case "customer_pays":
      return d.customer ? { type: d.type, counterparty_id: Number(d.customer), weeks: d.weeks } : "Choose a customer.";
    case "customer_fails":
      return d.customer ? { type: d.type, counterparty_id: Number(d.customer) } : "Choose a customer.";
    case "customers_pay_later":
    case "vendors_paid_later":
      return { type: d.type, days: d.days };
    case "shift_recurring":
      return d.category ? { type: d.type, category: d.category, days: d.days } : "Choose a payment to move.";
    case "one_off":
      return d.amount > 0 && d.description.trim()
        ? { type: d.type, week: d.week, amount: d.direction === "out" ? -d.amount : d.amount, description: d.description.trim() }
        : "Enter an amount above zero and a short description.";
  }
}

const VERDICT = {
  better: { tone: "green", label: "Better for cash" },
  worse: { tone: "red", label: "Worse for cash" },
  "about the same": { tone: "plain", label: "About the same" },
} as const;

function EffectRow({ draft, options, onChange, onRemove }: {
  draft: Draft; options: WhatIfOptions; onChange: (d: Draft) => void; onRemove: () => void;
}) {
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch } as Draft);
  const customers = (
    <label className="field">
      <span>Customer</span>
      <select value={"customer" in draft ? draft.customer : ""} onChange={(e) => set({ customer: e.target.value } as Partial<Draft>)}>
        {options.customers.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} ({money(c.overdue)} overdue)
          </option>
        ))}
      </select>
    </label>
  );
  const days = (hint: string) => (
    <label className="field">
      <span>Days (use a minus sign for earlier)</span>
      <input type="number" step={1} value={"days" in draft ? draft.days : 0} onChange={(e) => set({ days: Number(e.target.value) } as Partial<Draft>)} />
      <small className="hint">{hint}</small>
    </label>
  );
  const weekSelect = (value: number, label: string) => (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => set({ [draft.type === "one_off" ? "week" : "weeks"]: Number(e.target.value) } as Partial<Draft>)}>
        {Array.from({ length: 13 }, (_, i) => i + 1).map((w) => (
          <option key={w} value={w}>
            {w === 1 ? "Week 1 (next week)" : `Week ${w}`}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <li className="effect">
      <label className="field">
        <span>Change</span>
        <select
          value={draft.type}
          onChange={(e) => onChange({ ...blank(draft.id, e.target.value as DraftType, options) })}
        >
          {(Object.keys(TYPE_LABEL) as DraftType[]).map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </label>
      <div className="effect-fields">
        {draft.type === "customer_pays" && <>{customers}{weekSelect(draft.weeks, "Pays in")}</>}
        {draft.type === "customer_fails" && customers}
        {draft.type === "customers_pay_later" && days("Positive means customers pay later than usual.")}
        {draft.type === "vendors_paid_later" && days("Positive means we pay vendors later than usual.")}
        {draft.type === "shift_recurring" && (
          <>
            <label className="field">
              <span>Payment</span>
              <select value={draft.category} onChange={(e) => set({ category: e.target.value } as Partial<Draft>)}>
                {options.recurring_categories.map((c) => (
                  <option key={c} value={c}>
                    {driverName(c)}
                  </option>
                ))}
              </select>
            </label>
            {days("Positive moves the payment later.")}
          </>
        )}
        {draft.type === "one_off" && (
          <>
            {weekSelect(draft.week, "In")}
            <label className="field">
              <span>Direction</span>
              <select value={draft.direction} onChange={(e) => set({ direction: e.target.value } as Partial<Draft>)}>
                <option value="out">Money goes out</option>
                <option value="in">Money comes in</option>
              </select>
            </label>
            <label className="field">
              <span>Amount ($)</span>
              <input type="number" min={0} step={1000} value={draft.amount} onChange={(e) => set({ amount: Number(e.target.value) } as Partial<Draft>)} />
            </label>
            <label className="field">
              <span>What is it</span>
              <input value={draft.description} onChange={(e) => set({ description: e.target.value } as Partial<Draft>)} />
            </label>
          </>
        )}
      </div>
      <button className="btn btn-quiet" onClick={onRemove} aria-label="Remove this change">
        Remove
      </button>
    </li>
  );
}

export default function WhatIf({ version }: { version: number }) {
  const options = useData(api.whatIfOptions, [version]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const nextId = useRef(1);

  if (options.error) return <Notice tone="error">{options.error}</Notice>;
  if (!options.data) return <Loading />;
  const o = options.data;

  async function execute(list: Draft[]) {
    const effects: Effect[] = [];
    for (const d of list) {
      const e = toEffect(d);
      if (typeof e === "string") {
        setFormError(e);
        return;
      }
      effects.push(e);
    }
    if (effects.length === 0) {
      setFormError("Add at least one change.");
      return;
    }
    setFormError(null);
    const r = await run(() => api.whatIf(effects));
    if (r) setResult(r);
  }

  const newDraft = (type: DraftType) => blank(nextId.current++, type, o);
  const top = o.customers[0];
  const presets: { label: string; build: () => Draft[] }[] = [
    ...(top ? [{ label: `${top.name} pays its overdue invoices next week`,
      build: () => [{ ...newDraft("customer_pays"), customer: String(top.id), weeks: 1 } as Draft] }] : []),
    ...(o.recurring_categories.includes("payroll") ? [{ label: "Payroll is paid a week later",
      build: () => [{ ...newDraft("shift_recurring"), category: "payroll", days: 7 } as Draft] }] : []),
    { label: "Customers pay 10 days slower", build: () => [{ ...newDraft("customers_pay_later"), days: 10 } as Draft] },
    { label: "We pay vendors 15 days later", build: () => [{ ...newDraft("vendors_paid_later"), days: 15 } as Draft] },
    { label: "We spend $50,000 on equipment in week 3", build: () => [newDraft("one_off")] },
  ];

  const h = result?.headline;
  return (
    <div className="stack">
      <section className="section" aria-labelledby="test-h">
        <h2 id="test-h">Test a change</h2>
        <p className="lede">
          Pick one or more changes. The same 13-week forecast runs with and without them, using identical simulations, so any
          difference comes only from your change.
        </p>
        <div className="presets" role="group" aria-label="Quick starts">
          {presets.map((p) => (
            <button key={p.label} className="chip" disabled={busy} onClick={() => { const d = p.build(); setDrafts(d); void execute(d); }}>
              {p.label}
            </button>
          ))}
        </div>

        {drafts.length > 0 && (
          <ul className="effects">
            {drafts.map((d) => (
              <EffectRow key={d.id} draft={d} options={o}
                onChange={(next) => setDrafts((all) => all.map((x) => (x.id === d.id ? next : x)))}
                onRemove={() => setDrafts((all) => all.filter((x) => x.id !== d.id))} />
            ))}
          </ul>
        )}
        <div className="toolbar">
          <button className="btn" onClick={() => setDrafts((all) => [...all, newDraft("customer_pays")])}>
            {drafts.length === 0 ? "Build your own change" : "Add another change"}
          </button>
          {drafts.length > 0 && (
            <>
              <button className="btn btn-primary" disabled={busy} onClick={() => void execute(drafts)}>
                {busy ? "Running both forecasts…" : "Run"}
              </button>
              <button className="btn btn-quiet" disabled={busy} onClick={() => { setDrafts([]); setResult(null); setFormError(null); }}>
                Clear
              </button>
            </>
          )}
        </div>
        {formError && <Notice tone="warn">{formError}</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
      </section>

      {result && h && (
        <section className="section" aria-labelledby="result-h">
          <div className="section-head">
            <h2 id="result-h">What happens</h2>
            <Pill tone={VERDICT[h.verdict].tone}>{VERDICT[h.verdict].label}</Pill>
          </div>
          <p className="result-sentence">{result.summary}</p>
          <ul className="assumptions" aria-label="Assumptions used">
            {result.assumptions.map((a) => <li key={a}>{a}</li>)}
          </ul>

          <table className="ledger">
            <thead>
              <tr>
                <th>Measure</th>
                <th className="r">Without the change</th>
                <th className="r">With the change</th>
                <th className="r">Difference</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Cash at week 13 (base case)</td>
                <td className="r num">{ledger(h.week13.baseline)}</td>
                <td className="r num">{ledger(h.week13.scenario)}</td>
                <td className={`r num ${h.week13.difference < -1 ? "neg" : ""}`}>{change(h.week13.difference)}</td>
              </tr>
              <tr>
                <td>Lowest downside balance</td>
                <td className="r num">{ledger(h.lowest_downside.baseline)}, week {h.lowest_downside.baseline_week}</td>
                <td className="r num">{ledger(h.lowest_downside.scenario)}, week {h.lowest_downside.scenario_week}</td>
                <td className={`r num ${h.lowest_downside.difference < -1 ? "neg" : ""}`}>{change(h.lowest_downside.difference)}</td>
              </tr>
              <tr>
                <td>Chance of cash going negative</td>
                <td className="r num">{pct(h.chance_negative.baseline)}</td>
                <td className="r num">{pct(h.chance_negative.scenario)}</td>
                <td className="r num">{Math.abs(h.chance_negative.scenario - h.chance_negative.baseline) < 0.05 ? "No change" : `${(h.chance_negative.scenario - h.chance_negative.baseline).toFixed(1)} points`}</td>
              </tr>
              <tr>
                <td>Receivables unlikely to be collected</td>
                <td className="r num">{ledger(h.receivables_at_risk.baseline)}</td>
                <td className="r num">{ledger(h.receivables_at_risk.scenario)}</td>
                <td className="r num">{change(h.receivables_at_risk.scenario - h.receivables_at_risk.baseline)}</td>
              </tr>
            </tbody>
          </table>

          <ScenarioChart result={result} />

          {result.drivers.length > 0 && (
            <>
              <h3 className="sub">Where the difference comes from (13-week totals)</h3>
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Driver</th>
                    <th className="r">Without</th>
                    <th className="r">With</th>
                    <th className="r">Difference</th>
                  </tr>
                </thead>
                <tbody>
                  {result.drivers.map((d) => (
                    <tr key={d.driver}>
                      <td>{driverName(d.driver)}</td>
                      <td className="r num">{ledger(d.baseline)}</td>
                      <td className="r num">{ledger(d.scenario)}</td>
                      <td className={`r num ${d.difference < 0 ? "neg" : ""}`}>{change(d.difference)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {result.drivers.length === 0 && (
            <Notice>The total over 13 weeks is unchanged. The change only moves cash between weeks, so check the chart and the lowest balance.</Notice>
          )}
        </section>
      )}
    </div>
  );
}