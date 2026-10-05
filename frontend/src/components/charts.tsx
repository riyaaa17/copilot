import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Forecast, WhatIfResult, Week } from "../api";
import { axisMoney, change, driverName, ledger, money, shortDate } from "../format";

/** Evenly spaced, round ticks that fully contain [lo, hi]. */
function niceScale(lo: number, hi: number, count = 6): { domain: [number, number]; ticks: number[] } {
  const raw = Math.max(hi - lo, 1) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(v);
  return { domain: [min, max], ticks };
}

interface FanRow {
  label: string;
  dates: string;
  p10: number;
  p50: number;
  p90: number;
  band: number;
}

function FanTip({ active, row }: { active?: boolean; row?: FanRow }) {
  if (!active || !row) return null;
  return (
    <div className="tip">
      <p className="tip-title">
        {row.label}
        {row.dates ? `, ${row.dates}` : ""}
      </p>
      <dl>
        <dt>Upside (P90)</dt>
        <dd className="num">{money(row.p90)}</dd>
        <dt>Base case (P50)</dt>
        <dd className="num">{money(row.p50)}</dd>
        <dt>Downside (P10)</dt>
        <dd className="num">{money(row.p10)}</dd>
      </dl>
    </div>
  );
}

/** The cash runway: base case with the range of 80% of simulated outcomes shaded around it. */
export function ForecastChart({ forecast }: { forecast: Forecast }) {
  const cash = forecast.opening_cash;
  const rows: FanRow[] = [
    { label: "Today", dates: "", p10: cash, p50: cash, p90: cash, band: 0 },
    ...forecast.weeks.map((w) => ({
      label: `Wk ${w.week}`,
      dates: `${shortDate(w.start)} to ${shortDate(w.end)}`,
      p10: w.closing_p10,
      p50: w.closing_p50,
      p90: w.closing_p90,
      band: w.closing_p90 - w.closing_p10,
    })),
  ];
  const lo = Math.min(...rows.map((r) => r.p10));
  const hi = Math.max(...rows.map((r) => r.p90));
  const { domain, ticks } = niceScale(lo, hi);

  const low = forecast.weeks.find((w) => w.week === forecast.summary.lowest_p10_week);
  const showLow = low !== undefined && forecast.summary.lowest_p10_balance < cash;

  return (
    <figure className="chart" aria-label="Cash runway forecast for the next 13 weeks">
      <ResponsiveContainer width="100%" height={340}>
        <ComposedChart data={rows} margin={{ top: 14, right: 20, bottom: 4, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--rule)" />
          <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--ink-3)" }} tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
          <YAxis
            type="number"
            domain={domain}
            ticks={ticks}
            allowDataOverflow
            tickFormatter={axisMoney}
            tickLine={false}
            axisLine={false}
            width={62}
            tick={{ fill: "var(--ink-2)", fontSize: 12 }}
          />
          <Tooltip content={({ active, payload }) => <FanTip active={active} row={payload?.[0]?.payload as FanRow | undefined} />} />
          <Area type="monotone" dataKey="p10" stackId="fan" stroke="none" fill="transparent" isAnimationActive={false} />
          <Area type="monotone" dataKey="band" stackId="fan" stroke="none" fill="var(--green)" fillOpacity={0.18} isAnimationActive={false} />
          <Line type="monotone" dataKey="p50" stroke="var(--green)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--green)" }} isAnimationActive={false} />
          {showLow && (
            <ReferenceDot x={`Wk ${low.week}`} y={low.closing_p10} r={6} fill="var(--red)" stroke="var(--paper)" strokeWidth={2} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <figcaption>
        The line is the base case. The shaded band holds 80% of the {forecast.n_sims.toLocaleString("en-US")} simulated outcomes.
        {showLow && " The red dot marks the lowest point of the downside case."}
      </figcaption>
    </figure>
  );
}

interface NetRow {
  label: string;
  net: number;
  week: Week;
}

function NetTip({ active, row }: { active?: boolean; row?: NetRow }) {
  if (!active || !row) return null;
  const w = row.week;
  const drivers = Object.entries(w.drivers)
    .filter(([, v]) => Math.abs(v) >= 1)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return (
    <div className="tip">
      <p className="tip-title">
        {row.label}, {shortDate(w.start)} to {shortDate(w.end)}
      </p>
      <dl>
        {drivers.map(([name, v]) => (
          <div key={name} className="tip-row">
            <dt>{driverName(name)}</dt>
            <dd className={v < 0 ? "num neg" : "num"}>{ledger(v)}</dd>
          </div>
        ))}
        <div className="tip-row tip-total">
          <dt>Net cash flow</dt>
          <dd className={w.net < 0 ? "num neg" : "num"}>{ledger(w.net)}</dd>
        </div>
      </dl>
    </div>
  );
}

/** Net cash flow per week. Hover a bar to see what drives it. */
export function NetFlowChart({ weeks }: { weeks: Week[] }) {
  const rows: NetRow[] = weeks.map((w) => ({ label: `Wk ${w.week}`, net: w.net, week: w }));
  const { domain, ticks } = niceScale(Math.min(0, ...rows.map((r) => r.net)), Math.max(0, ...rows.map((r) => r.net)), 5);
  return (
    <figure className="chart" aria-label="Net cash flow by week">
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={rows} margin={{ top: 10, right: 20, bottom: 4, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--rule)" />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
          <YAxis
            type="number"
            domain={domain}
            ticks={ticks}
            allowDataOverflow
            tickFormatter={axisMoney}
            tickLine={false}
            axisLine={false}
            width={62}
            tick={{ fill: "var(--ink-2)", fontSize: 12 }}
          />
          <ReferenceLine y={0} stroke="var(--ink-3)" />
          <Tooltip
            cursor={{ fill: "var(--rule)", opacity: 0.45 }}
            content={({ active, payload }) => <NetTip active={active} row={payload?.[0]?.payload as NetRow | undefined} />}
          />
          <Bar dataKey="net" isAnimationActive={false}>
            {rows.map((r) => (
              <Cell key={r.label} fill={r.net < 0 ? "var(--red)" : "var(--green)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <figcaption>Weeks that dip are where payroll, rent and the quarterly tax payment land. Hover a bar for the detail.</figcaption>
    </figure>
  );
}
interface ScenRow {
  label: string;
  dates: string;
  base: number;
  p10: number;
  p50: number;
  p90: number;
  band: number;
}

function ScenTip({ active, row }: { active?: boolean; row?: ScenRow }) {
  if (!active || !row) return null;
  const diff = row.p50 - row.base;
  return (
    <div className="tip">
      <p className="tip-title">
        {row.label}
        {row.dates ? `, ${row.dates}` : ""}
      </p>
      <dl>
        <dt>Without the change</dt>
        <dd className="num">{money(row.base)}</dd>
        <dt>With the change</dt>
        <dd className="num">{money(row.p50)}</dd>
        <dt>Difference</dt>
        <dd className={diff < 0 ? "num neg" : "num"}>{change(diff)}</dd>
      </dl>
    </div>
  );
}

/** Base case without the change (dashed) against the base case and range with it (green). */
export function ScenarioChart({ result }: { result: WhatIfResult }) {
  const cash = result.headline.cash_today;
  const rows: ScenRow[] = [
    { label: "Today", dates: "", base: cash, p10: cash, p50: cash, p90: cash, band: 0 },
    ...result.weeks.map((w) => ({
      label: `Wk ${w.week}`,
      dates: `${shortDate(w.start)} to ${shortDate(w.end)}`,
      base: w.baseline_p50,
      p10: w.scenario_p10,
      p50: w.scenario_p50,
      p90: w.scenario_p90,
      band: w.scenario_p90 - w.scenario_p10,
    })),
  ];
  const lo = Math.min(...rows.map((r) => Math.min(r.p10, r.base)));
  const hi = Math.max(...rows.map((r) => Math.max(r.p90, r.base)));
  const { domain, ticks } = niceScale(lo, hi);
  return (
    <figure className="chart" aria-label="Cash with and without the change">
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={rows} margin={{ top: 14, right: 20, bottom: 4, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--rule)" />
          <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--ink-3)" }} tick={{ fill: "var(--ink-2)", fontSize: 12 }} />
          <YAxis
            type="number"
            domain={domain}
            ticks={ticks}
            allowDataOverflow
            tickFormatter={axisMoney}
            tickLine={false}
            axisLine={false}
            width={62}
            tick={{ fill: "var(--ink-2)", fontSize: 12 }}
          />
          <Tooltip content={({ active, payload }) => <ScenTip active={active} row={payload?.[0]?.payload as ScenRow | undefined} />} />
          <Area type="monotone" dataKey="p10" stackId="fan" stroke="none" fill="transparent" isAnimationActive={false} />
          <Area type="monotone" dataKey="band" stackId="fan" stroke="none" fill="var(--green)" fillOpacity={0.16} isAnimationActive={false} />
          <Line type="monotone" dataKey="base" stroke="var(--ink-2)" strokeWidth={2} strokeDasharray="6 5" dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="p50" stroke="var(--green)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--green)" }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <figcaption>The dashed line is the base case without the change. The green line is the base case with it, and the band holds 80% of the simulated outcomes with the change.</figcaption>
    </figure>
  );
}