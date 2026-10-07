

// src/api.ts

// Automatically pulls VITE_API_BASE_URL from your Vercel environment settings,
// or defaults to localhost during local development.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Universal helper function for making API requests across your views and components.
 */
export async function apiRequest(endpoint: string, options: RequestInit = {}) {
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const url = `${API_BASE_URL}${cleanEndpoint}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API Error (${response.status}): ${errorText || response.statusText}`);
  }

  // Parse JSON if response has content, otherwise return null
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
// ---------- types (they mirror the backend's JSON) ----------
export interface IngestSummary {
  counterparties: number;
  invoices: number;
  bank_transactions: number;
  open_ar_total: number;
  open_ap_total: number;
  meta: Record<string, string>;
}

export interface Week {
  week: number;
  start: string;
  end: string;
  drivers: Record<string, number>;
  inflows: number;
  outflows: number;
  net: number;
  closing_p10: number;
  closing_p50: number;
  closing_p90: number;
}

export interface ForecastSummary {
  opening_cash: number;
  closing_p50_week13: number;
  change_pct: number | null;
  lowest_p10_balance: number;
  lowest_p10_week: number;
  prob_negative_cash_pct: number;
  open_ar_at_risk_amount: number;
  open_ar_at_risk_count: number;
  expected_collections_from_open_ar: number;
}

export interface Forecast {
  as_of: string;
  opening_cash: number;
  n_sims: number;
  summary: ForecastSummary;
  weeks: Week[];
}

export interface Kpis {
  as_of: string;
  cash_balance: number | null;
  open_ar: number;
  overdue_ar: number;
  overdue_ar_pct: number;
  open_ap: number;
  dso: number | null;
  dpo: number | null;
  avg_days_to_pay: number | null;
  avg_days_beyond_terms: number | null;
}

export interface AgingBucket {
  bucket: string;
  count: number;
  amount: number;
  pct: number;
}

export interface Aging {
  as_of: string;
  type: "AR" | "AP";
  buckets: AgingBucket[];
  total_open: number;
  total_overdue: number;
  overdue_pct: number;
}

export interface PriorityInvoice {
  invoice_id: number;
  invoice_no: string;
  amount: number;
  due_date: string;
  days_overdue: number;
}

export interface PriorityCustomer {
  counterparty_id: number;
  customer: string;
  behavior: string;
  tier: string;
  tone: string;
  max_days_overdue: number;
  total_amount: number;
  exposure: number;
  invoices: PriorityInvoice[];
}

export interface HeldInvoice {
  invoice_id: number;
  invoice_no: string;
  customer: string;
  amount: number;
  days_overdue: number;
}

export interface Priorities {
  as_of: string;
  customers: PriorityCustomer[];
  held: HeldInvoice[];
}

export type DraftStatus = "draft" | "approved" | "rejected" | "sent";

export interface Draft {
  draft_id: number;
  status: DraftStatus;
  customer: string | null;
  to_email: string | null;
  subject: string;
  body: string;
  tone: string;
  total_amount: number;
  invoice_ids: number[];
  source: "llm" | "template";
  warnings: string | null;
  created_at: string;
  mailto?: string;
}

export interface DraftBatch {
  as_of: string;
  llm_used: boolean;
  created: Draft[];
  skipped: { customer: string; reason: string }[];
}

export type FlagStatus = "open" | "confirmed" | "dismissed";

export interface Flag {
  flag_id: number;
  kind: "duplicate" | "outlier";
  severity: "medium" | "high";
  score: number;
  status: FlagStatus;
  explanation: string;
  invoice_id: number;
  related_invoice_id: number | null;
  invoice_no: string | null;
  type: "AR" | "AP" | null;
  counterparty: string | null;
  amount: number | null;
  issue_date: string | null;
}

export interface BriefingListItem {
  briefing_id: number;
  as_of: string;
  source: "llm" | "template";
  warnings: string | null;
  created_at: string;
}

export interface Briefing extends BriefingListItem {
  markdown: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatReply {
  answer: string;
  source: "llm" | "fallback";
  verified: boolean;
  warnings: string[];
  trace: { tool: string; arguments: Record<string, unknown>; ok: boolean }[];
}
export type Effect =
  | { type: "customer_pays"; counterparty_id: number; weeks: number }
  | { type: "customer_fails"; counterparty_id: number }
  | { type: "customers_pay_later"; days: number }
  | { type: "vendors_paid_later"; days: number }
  | { type: "shift_recurring"; category: string; days: number }
  | { type: "one_off"; week: number; amount: number; description: string };

export interface WhatIfOptions {
  customers: { id: number; name: string; open: number; overdue: number }[];
  recurring_categories: string[];
}

export interface WhatIfResult {
  as_of: string;
  assumptions: string[];
  summary: string;
  headline: {
    cash_today: number;
    verdict: "better" | "worse" | "about the same";
    week13: { baseline: number; scenario: number; difference: number };
    lowest_downside: {
      baseline: number;
      baseline_week: number;
      scenario: number;
      scenario_week: number;
      difference: number;
    };
    chance_negative: { baseline: number; scenario: number };
    receivables_at_risk: { baseline: number; scenario: number };
  };
  weeks: {
    week: number;
    start: string;
    end: string;
    baseline_p50: number;
    scenario_p10: number;
    scenario_p50: number;
    scenario_p90: number;
    difference: number;
  }[];
  drivers: { driver: string; baseline: number; scenario: number; difference: number }[];
}
// ---------- calls ----------
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  summary: () => request<IngestSummary>("/api/ingest/summary"),
  async loadSample(): Promise<void> {
    await post("/api/ingest/sample");
    await post("/api/anomalies/scan");
  },
  kpis: () => request<Kpis>("/api/analytics/kpis"),
  forecast: () => request<Forecast>("/api/forecast"),
  aging: (kind: "AR" | "AP") => request<Aging>(`/api/analytics/aging?kind=${kind}`),

  priorities: () => request<Priorities>("/api/collections/priorities"),
  createDrafts: (top: number, useLlm: boolean) =>
    post<DraftBatch>(`/api/collections/drafts?top=${top}&use_llm=${useLlm}`),
  drafts: () => request<Draft[]>("/api/collections/drafts?status=all"),
  editDraft: (id: number, subject: string, body: string) =>
    request<Draft>(`/api/collections/drafts/${id}`, { method: "PUT", body: JSON.stringify({ subject, body }) }),
  draftAction: (id: number, action: "approve" | "reject" | "mark-sent") =>
    post<Draft>(`/api/collections/drafts/${id}/${action}`),

  scan: () => post<{ total: number; duplicates: number; outliers: number }>("/api/anomalies/scan"),
  flags: (status: FlagStatus) => request<Flag[]>(`/api/anomalies?status=${status}`),
  reviewFlag: (id: number, status: "confirmed" | "dismissed") =>
    post<{ flag_id: number; status: string }>(`/api/anomalies/${id}/review?status=${status}`),

  reports: () => request<BriefingListItem[]>("/api/reports"),
  report: (id: number) => request<Briefing>(`/api/reports/${id}`),
  createReport: (useLlm: boolean) => post<Briefing>(`/api/reports/weekly?use_llm=${useLlm}`),
  whatIfOptions: () => request<WhatIfOptions>("/api/whatif/options"),
  whatIf: (effects: Effect[]) => post<WhatIfResult>("/api/whatif", { effects }),
  chat: (message: string, history: ChatTurn[]) =>
    post<ChatReply>("/api/chat", { message, history, use_llm: true }),
};