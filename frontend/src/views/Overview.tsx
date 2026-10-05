import { useState } from "react";
import { api } from "../api";
import type { Forecast, Kpis } from "../api";
import { ForecastChart, NetFlowChart } from "../components/charts";
import { Amount, Loading, Notice } from "../components/ui";
import { longDate, ledger, money, pct } from "../format";
import { useData } from "../hooks";

/** One plain-English paragraph built only from figures the API returned. */
function statement(f: Forecast, k: Kpis | null): string {
  const s = f.summary;
  const change = s.change_pct ?? 0;
  let text =
    `Cash is ${money(f.opening_cash)} today. In the base case it is ${money(s.closing_p50_week13)} by week 13, ` +
    `${pct(Math.abs(change))} ${change < 0 ? "lower" : "higher"}`;
  text +=
    s.lowest_p10_balance < f.opening_cash
      ? `, and in a downside case it could fall to ${money(s.lowest_p10_balance)} in week ${s.lowest_p10_week}.`
      : ".";
  if (k) text += ` ${money(k.overdue_ar)} of receivables (${pct(k.overdue_ar_pct)}) is overdue.`;
  return text;
}

export default function Overview({ version }: { version: number }) {
  const forecast = useData(api.forecast, [version]);
  const kpis = useData(api.kpis, [version]);
  const [kind, setKind] = useState<"AR" | "AP">("AR");
  const aging = useData(() => api.aging(kind), [version, kind]);

  if (forecast.error) return <Notice tone="error">{forecast.error}</Notice>;
  if (!forecast.data) return <Loading>Running 1,000 simulations of the next 13 weeks…</Loading>;

  const f = forecast.data;
  const s = f.summary;
  const k = kpis.data;
  const change = s.change_pct ?? 0;
  const maxAging = Math.max(...(aging.data?.buckets.map((b) => b.amount) ?? [1]), 1);

  return (
    <div className="stack">
      <p className="statement">{statement(f, k)}</p>

      <dl className="strip">
        <div className="strip-cell">
          <dt>Cash today</dt>
          <dd className="figure num">{ledger(f.opening_cash)}</dd>
          <p>Figures as of {longDate(f.as_of)}</p>
        </div>
        <div className="strip-cell">
          <dt>Base case, week 13</dt>
          <dd className="figure num">{ledger(s.closing_p50_week13)}</dd>
          <p>
            {pct(Math.abs(change))} {change < 0 ? "lower" : "higher"} than today
          </p>
        </div>
        <div className="strip-cell">
          <dt>Lowest downside</dt>
          <dd className="figure num">{ledger(s.lowest_p10_balance)}</dd>
          <p>
            Week {s.lowest_p10_week}. Chance of going negative: {pct(s.prob_negative_cash_pct)}
          </p>
        </div>
        <div className="strip-cell">
          <dt>Overdue receivables</dt>
          <dd className="figure num">{k ? ledger(k.overdue_ar) : "…"}</dd>
          <p>{k ? `${pct(k.overdue_ar_pct)} of ${money(k.open_ar)} open` : ""}</p>
        </div>
        <div className="strip-cell">
          <dt>Days to collect (DSO)</dt>
          <dd className="figure num">{k?.dso != null ? k.dso.toFixed(1) : "n/a"}</dd>
          <p>
            {k?.avg_days_beyond_terms != null
              ? `Customers pay about ${Math.round(k.avg_days_beyond_terms)} days after the due date`
              : ""}
          </p>
        </div>
      </dl>

      <section className="section" aria-labelledby="runway-h">
        <h2 id="runway-h">Cash runway, next 13 weeks</h2>
        <ForecastChart forecast={f} />
      </section>

      <section className="section" aria-labelledby="net-h">
        <h2 id="net-h">What moves cash each week</h2>
        <NetFlowChart weeks={f.weeks} />
      </section>

      <section className="section" aria-labelledby="aging-h">
        <div className="section-head">
          <h2 id="aging-h">Who owes whom, by age</h2>
          <div className="seg" role="group" aria-label="Choose receivables or payables">
            <button type="button" aria-pressed={kind === "AR"} onClick={() => setKind("AR")}>
              Customers owe us
            </button>
            <button type="button" aria-pressed={kind === "AP"} onClick={() => setKind("AP")}>
              We owe vendors
            </button>
          </div>
        </div>
        {aging.error && <Notice tone="error">{aging.error}</Notice>}
        {aging.data && (
          <table className="ledger">
            <thead>
              <tr>
                <th>Days overdue</th>
                <th className="r">Invoices</th>
                <th className="r">Amount</th>
                <th className="r">Share</th>
                <th aria-hidden="true" />
              </tr>
            </thead>
            <tbody>
              {aging.data.buckets.map((b) => (
                <tr key={b.bucket}>
                  <td>{b.bucket}</td>
                  <td className="r num">{b.count}</td>
                  <td className="r num">{ledger(b.amount)}</td>
                  <td className="r num">{pct(b.pct)}</td>
                  <td className="bar-cell" aria-hidden="true">
                    <span
                      className={b.bucket === "Not yet due" ? "bar bar-ok" : "bar bar-late"}
                      style={{ width: `${(b.amount / maxAging) * 100}%` }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>Total open</td>
                <td />
                <td className="r num">
                  <Amount value={aging.data.total_open} />
                </td>
                <td className="r num">{pct(aging.data.overdue_pct)} overdue</td>
                <td />
              </tr>
            </tfoot>
          </table>
        )}
      </section>
    </div>
  );
}