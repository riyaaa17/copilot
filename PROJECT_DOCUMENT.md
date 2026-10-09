# Cash Flow & Working Capital Copilot

**An "Office of the CFO" assistant built from six cooperating agents.**
A CFO or finance manager loads invoices, receivables and bank transactions, and the system forecasts cash for 13 weeks, ranks who to chase, flags suspicious invoices, writes a weekly briefing, tests "what if" scenarios, and answers questions in plain English. Every number is calculated by code. The language model only words things, and its wording is checked.

*Built as a portfolio project for Practus (CFO-office services for PE-backed and mid-size companies). Development environment: Windows 10, PowerShell, VS Code.*

---

## Contents

1. [Summary](#1-summary)
2. [Current status](#2-current-status)
3. [Architecture](#3-architecture)
4. [Technology and why](#4-technology-and-why)
5. [Repository layout](#5-repository-layout)
6. [Data model](#6-data-model)
7. [Synthetic data](#7-synthetic-data)
8. [The engines and agents](#8-the-engines-and-agents)
9. [The trust model (guardrails)](#9-the-trust-model-guardrails)
10. [API reference](#10-api-reference)
11. [The dashboard](#11-the-dashboard)
12. [Testing and verification](#12-testing-and-verification)
13. [Setup and running on Windows](#13-setup-and-running-on-windows)
14. [Configuration](#14-configuration)
15. [Troubleshooting](#15-troubleshooting)
16. [Bugs found and what they taught](#16-bugs-found-and-what-they-taught)
17. [Known limitations](#17-known-limitations)
18. [Roadmap](#18-roadmap)
19. [Demo script and talking points](#19-demo-script-and-talking-points)
20. [Continuing in a new conversation](#20-continuing-in-a-new-conversation)

---

## 1. Summary

**What it does**

| Capability | How |
|---|---|
| Load and clean data | CSV validation with row-level quarantine and bank-to-invoice reconciliation |
| Forecast cash, 13 weeks | Driver-based Monte Carlo (1,000 simulations): open receivables, open payables, new billings, recurring payments. Gives P10 / P50 / P90 bands |
| Rank collections | Risk-weighted exposure per customer; drafts one follow-up email per customer, for human approval |
| Detect anomalies | Duplicate invoices and unusually large amounts, each with a plain-English reason; a person confirms or dismisses |
| Weekly CFO briefing | All figures, tables and actions calculated; AI writes only two short paragraphs, each figure verified |
| What-if analysis | "What if Ironwood pays next week?", "What if we delay payroll a week?" using identical random draws, so differences come only from the change |
| Chat | A tool-calling assistant; every dollar amount, percentage, day and week count in an answer is checked against the data before it is shown |
| Live agent trace | The chat shows which agent is working and how long each step takes (built, see status) |

**The one idea behind the design:** finance people will not trust a number an AI "estimated". So code does all the arithmetic, the model only writes, and anything the model writes is verified or replaced by a safe template.

**Headline numbers on the sample company** (data as of 2026-09-29)

| Measure | Value |
|---|---|
| Cash today | $896,868 |
| Open receivables (duplicates excluded) | $575,398, of which $180,776 (31.4%) overdue |
| DSO | 45.3 days |
| Base-case cash at week 13 | $747,935 (16.6% lower) |
| Lowest downside (P10) | $361,006 in week 9 |
| Chance cash goes negative | 0.0% |
| Receivables unlikely to be collected in 13 weeks | $94,874 across 5 invoices |
| Largest overdue customer | Ironwood Industries, $74,274 across 3 invoices, up to 101 days late |
| Planted anomalies caught | 8 of 8 duplicates, 6 of 6 outliers, no false alarms |

---

## 2. Current status

| # | Item | Status |
|---|---|---|
| 1 | Backend: ingestion, analytics, forecast, anomalies, collections, reporting, orchestrator chat | **Done, confirmed working on the user's machine** |
| 2 | React dashboard: Cash outlook, Collections, Invoice checks, Weekly briefing, chat panel | **Done, confirmed working** |
| 3 | What-if analysis (engine, API, chat tools, "What if" page) | **Done, confirmed working** |
| 4 | Live agent trace in the chat (streaming) | **Built and tested in the development sandbox. Code not yet applied on the user's machine.** The complete code is in `APPENDIX_live_trace_code.md` |
| 5 | Real-file upload (CSVs and invoice PDFs) with an Ingestion Agent | **Not started** (plan in section 18) |
| 6 | Deployment, README polish | Not started |

Test counts: backend **151** on the user's machine (before item 4), **168** after item 4 is applied. Frontend UI tests (18) exist only in the development sandbox and were not shipped.

---

## 3. Architecture

```
                      React dashboard (Vite, port 5173)
   Cash outlook | What if | Collections | Invoice checks | Weekly briefing | Chat
                                   |  JSON over HTTP, and SSE for the live trace
                                   v
                      FastAPI backend (port 8002)
   +--------------------------------------------------------------------+
   |  api/        thin routes: ingest, analytics, forecast, anomalies,   |
   |              collections, reports, chat, whatif                     |
   |                                                                      |
   |  agents/     collections_agent   reporting_agent   orchestrator      |
   |              (LLM wording + approval workflow + the tool loop)       |
   |                                                                      |
   |  tools/      deterministic maths, no LLM:                            |
   |              ingestion  analytics  forecast  anomalies               |
   |              collections_tools  reporting  chat_tools  whatif        |
   |                                                                      |
   |  models/     SQLModel tables        llm.py   Groq wrapper            |
   +--------------------------------------------------------------------+
            |                                   |
      SQLite (or Postgres)                  Groq API
   invoices, bank, drafts, flags,     openai/gpt-oss-120b (smart)
   briefings, metadata                openai/gpt-oss-20b  (fast)
```

**The pattern every agent follows**

1. A deterministic tool computes the facts (pandas, NumPy, rules).
2. The model, if used, turns facts into words.
3. A checker compares every figure in the words with the facts.
4. If the check fails, the model gets one retry, then a built-in template or answer takes over.
5. Anything that touches the outside world (sending an email) waits for a human.

**How the agents map to the original brief**

| Brief | Implementation |
|---|---|
| Data Ingestion Agent | `tools/ingestion.py` (CSV today; upload and PDF are item 5) |
| Forecasting Agent | `tools/forecast.py`, `tools/whatif.py` |
| Collections Agent | `tools/collections_tools.py`, `agents/collections_agent.py` |
| Anomaly Agent | `tools/anomalies.py` |
| Reporting Agent | `tools/reporting.py`, `agents/reporting_agent.py` |
| Orchestrator Agent | `agents/orchestrator.py`, `tools/chat_tools.py` |

---

## 4. Technology and why

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic v2 | Typed, automatic OpenAPI docs at `/docs` |
| Packages | `uv` | Fast, reproducible installs on Windows |
| Database | SQLite by default, Postgres via Docker Compose file | Zero setup for a demo |
| ORM | SQLModel | Tables and API types in one place |
| Data | pandas, NumPy, SciPy | Forecast and analytics |
| Validation | Pandera | Row-level rules with line numbers |
| Matching | RapidFuzz | Invoice-number similarity, customer name lookup |
| LLM | Groq: `openai/gpt-oss-120b` (briefing, chat) and `openai/gpt-oss-20b` (email drafts) | Tool calling, low latency |
| Orchestration | A plain tool-calling loop | Easy to explain and test |
| Frontend | React 19, Vite 8, TypeScript 5.9, Recharts 3, react-markdown | Strict types, good charts |
| Styling | Hand-written CSS (no framework) | A deliberate "ledger" look |
| Tests | pytest (backend); Vitest + Testing Library + Playwright (sandbox checks) | |

**Why not Prophet for the forecast:** about 500 invoices give a thin weekly series and a model that cannot explain itself. The driver-based approach can say *why* cash dips (payroll and tax landing together) and can be decomposed by customer, invoice and category.

---

## 5. Repository layout

```
copilot/
  .gitignore                      .venv, .env, *.db, node_modules, dist, backend/data/synthetic/
  README.md
  backend/
    pyproject.toml                dependencies (uv)
    .env                          secrets and settings (never committed)
    app/
      main.py                     app, CORS, router registration
      config.py                   settings from .env
      db.py                       engine and sessions
      llm.py                      Groq client wrapper
      models/tables.py            all SQLModel tables
      tools/                      ingestion  analytics  forecast  anomalies
                                  collections_tools  reporting  chat_tools  whatif
      agents/                     collections_agent  reporting_agent  orchestrator
      api/                        ingest analytics forecast anomalies
                                  collections reports chat whatif
    data/
      generate_synthetic.py       makes the sample company
      grade_anomalies.py          scores the anomaly detector against the answer key
      synthetic/                  generated CSVs and truth files (git-ignored)
    tests/                        test_health test_ingestion test_analytics test_forecast
                                  test_anomalies test_collections test_reporting
                                  test_chat test_whatif (+ test_stream, item 4)
  frontend/
    package.json  vite.config.ts  tsconfig.json  index.html
    src/
      main.tsx  App.tsx  api.ts  format.ts  hooks.ts  styles.css
      components/                 ui  charts  DraftCard  Chat
      views/                      Overview  WhatIf  Collections  Anomalies  Briefing
  docker-compose.yml              optional Postgres and Redis
```

---

## 6. Data model

| Table | Purpose | Key fields |
|---|---|---|
| `Counterparty` | Customers and vendors | id, name, kind, email, payment_terms_days |
| `Invoice` | AR and AP in one table | invoice_no, type (AR/AP), counterparty_id, issue/due/paid dates, amount, currency, category, status (open/paid/void) |
| `BankTransaction` | Bank feed | txn_date, description, amount (+in/-out), category, matched_invoice_id |
| `EmailDraft` | One follow-up email per customer | counterparty_id, to_email, subject, body, tone, invoice_ids, total_amount, source (llm/template), warnings, status (draft/approved/rejected/sent) |
| `AnomalyFlag` | A suspicious invoice | invoice_id, related_invoice_id, kind (duplicate/outlier), score, severity, explanation, status (open/confirmed/dismissed) |
| `Briefing` | A saved weekly briefing | as_of, markdown, data_json (every figure, for audit), source, warnings |
| `AppMeta` | Key/value | as_of, start, opening_balance, current_balance |

**Rule:** if a table definition changes, delete `backend\copilot.db`, restart, then re-run the load and scan (section 13). This was needed when `AnomalyFlag` and `EmailDraft` changed.

Timestamps are timezone-aware (`utcnow()` in `tables.py`); the SQLModel version in use rejects naive datetimes.

---

## 7. Synthetic data

`data/generate_synthetic.py` builds a reproducible sample company (seed 42):

- 50 customers with hidden payment personalities (prompt 30%, average 35%, slow 20%, chronic 10%, disputer 5%) and 15 vendors.
- About 500 customer invoices and 150 vendor bills over 365 days, with realistic delays and some disputed invoices that go 120+ days late.
- A bank feed: customer receipts, vendor payments, payroll (month-end), rent (1st), quarterly tax (15th of Jan/Apr/Jul/Oct) and bank fees.
- **Planted problems with an answer key:** 8 duplicate invoices (some with altered numbers like `INV 01339` or `-A`) and 6 inflated amounts (8x to 15x).
- `truth_anomalies.csv` and `truth_personalities.csv` are the answer key. **No agent may read them.** Only `data/grade_anomalies.py` does.

Result on load: 65 counterparties, 659 invoices, 615 bank transactions, 575 bank lines reconciled to invoices, 0 rows rejected.

---

## 8. The engines and agents

### 8.1 Ingestion (`tools/ingestion.py`)

- Reads `counterparties.csv`, `invoices.csv`, `bank_transactions.csv`, normalises text, numbers and dates, then validates with Pandera: positive amounts, due date not before issue date, a `paid` invoice must have a paid date, an `open` one must not, valid currency, existing counterparty, AR invoices only on customers.
- **Bad rows are quarantined, never loaded and never fatal.** Each problem is reported with its CSV line number.
- Look-alike duplicates are valid rows and are kept; detecting them is the Anomaly Agent's job.
- Bank lines are matched to paid invoices by the invoice number in the description.
- Loading clears and reloads, so it is safe to repeat.

### 8.2 Analytics (`tools/analytics.py`)

- **Aging:** Not yet due, 1-30, 31-60, 61-90, 90+ days overdue, for AR or AP.
- **DSO and DPO:** open balance divided by billing in the last 90 days, times 90. Also amount-weighted days to pay and days beyond terms.
- **Customer payment profiles:** average, median and 90th percentile lateness, share paid late. Unpaid overdue invoices count as "at least this late" so slow payers do not look better than they are. Labels: prompt, average, slow, chronic. Measured against the hidden answer key, the labels match 74% of customers; most misses are good payers with one disputed invoice.
- All results use "as of" from the data (`AppMeta`), not the computer clock.
- Flagged duplicates are left out of the dashboard, briefing, forecast and chat figures so every screen agrees.

### 8.3 Forecasting (`tools/forecast.py`)

Cash flow is built from four drivers and simulated 1,000 times (fixed seed 42, so results repeat):

1. **Open receivables.** Each unpaid customer invoice gets a payment-date distribution from that customer's history, blended with the portfolio (a customer's own history gets weight n / (n + 5)).
2. **Open payables.** The same method for vendor bills. An overdue bill nobody has history for is assumed paid next week.
3. **New billings.** Invoices not yet issued, at each counterparty's historical rate, size and terms. Without these the forecast could only shrink.
4. **Recurring items.** Payroll, rent, tax and fees: cadence (monthly or quarterly), day of month or month-end, and size detected from bank history.

**Key statistical choice:** payment delays use a **Kaplan-Meier estimator with right-censoring**. Unpaid invoices are treated as "paid at least this late", conditioned on how overdue they already are. Looking only at paid invoices would make every customer look faster than they are.

Outputs per week: P10 / P50 / P90 closing balance, net flow, and the mean of each driver (so "why" can be answered from real causes). Summary: lowest downside and its week, chance of negative cash, receivables unlikely to be collected, expected collections.

**Backtest** (`GET /api/forecast/backtest`): rewinds the books 13 weeks, forecasts using only what was known then, compares with what happened. On the sample data, after duplicates are removed and outliers are kept out of learning: total collections forecast within 1.1% of actual, 11 of 13 weeks inside the P10-P90 band, week-13 balance error 9.5%. One window on synthetic data, so evidence the method works, not proof of accuracy. Vendor payments were forecast about 31% too high, traced to a quiet quarter (27 new bills against 41 expected) and not to a defect.

**Exclusions:** `exclude_ids` removes duplicates everywhere. `learn_exclude_ids` keeps outliers in open receivables and payables (they may be real) but stops them teaching the model what a normal invoice size is.

### 8.4 Anomaly detection (`tools/anomalies.py`)

- **Duplicates:** same counterparty and exact amount, then scored: 0.4 for the amount match, up to 0.4 for invoice-number similarity (exact, or the same digits with a cosmetic change such as `INV 01042` or `INV-01042-A`; neighbouring numbers and credit notes score zero), up to 0.2 for dates within 7 days (0.1 within 30). Flag at 0.6 or more. If both invoices are already paid, severity is high and the explanation says to check whether cash can be recovered.
- **Outliers:** median and MAD of `log(amount)` per counterparty (at least 5 invoices), flagged at robust z of 3.5 or more **and** at least 2.5x the typical amount. Explanation example: "$92,558.43 is 10.9x this AR counterparty's typical invoice ($8,528.72)".
- **Human review:** flags are open, confirmed or dismissed. Re-scanning keeps earlier decisions. Dismissed flags no longer affect the forecast.
- Graded against the answer key: 8/8 duplicates, 6/6 outliers, no false alarms. These planted problems are blatant; real data will be harder, so 100% is not a general accuracy claim.

### 8.5 Collections (`tools/collections_tools.py`, `agents/collections_agent.py`)

- **Priority** per invoice: `risk = 0.4 x (1 - chance of collection in 13 weeks) + 0.4 x min(days overdue, 120) / 120 + 0.2 x customer history risk`; `exposure = amount x risk`. A smaller but riskier invoice can outrank a larger safe one.
- **Tone** from the oldest invoice: up to 14 days friendly reminder, 15-45 firm follow-up, 46-90 urgent, over 90 final notice.
- **One email per customer.** Invoices flagged as unusually large are held back and never emailed.
- **Wording:** the model gets verified facts as JSON and returns `{subject, body}`. The draft must contain every invoice number, only supplied dollar amounts and dates, no threats (legal, late fee, interest, penalty, collections agency), and a sensible length. Two attempts, then a standard template. The draft records `source` (llm or template) and `warnings`.
- **Workflow:** draft, then approved or rejected; approved then sent. **Nothing is ever sent automatically.** Approving produces a `mailto:` link to open in the user's own email app; "mark as sent" is bookkeeping.

### 8.6 Weekly briefing (`tools/reporting.py`, `agents/reporting_agent.py`)

- All tables, key figures, week-over-week changes, the forecast outlook, the weakest weeks and their drivers, aging, who to chase, anomaly totals and the **recommended actions** are calculated. The actions are rules ("Hold payment on 1 flagged duplicate vendor bill ($4,083) until verified"), so they always carry real numbers.
- **Last week's variance** is measured: the books are rewound 7 days, a forecast is made from that state, and week 1 is compared with the real bank activity.
- The model writes only two paragraphs: the summary and the variance commentary. The checker accepts a paragraph only if every dollar amount, percentage, day count and week number appears in the supplied facts, no amount is abbreviated ("$897k"), and cash is not described as moving "from" a forecast (a real mistake the model once made). Otherwise a template paragraph is used. AI-written wording is labelled in the document.
- Saved in `Briefing` with all figures as JSON for audit.

### 8.7 Orchestrator and chat (`agents/orchestrator.py`, `tools/chat_tools.py`)

- A plain tool-calling loop (maximum 6 rounds). The model picks tools; **it never calculates**. There are 15 tools: forecast, `explain_cash_change`, key figures, aging, top overdue customers, customer lookup, anomalies, recommended actions, draft emails, and six what-if tools.
- `explain_cash_change` splits the expected change over N weeks into drivers that add up exactly, with a P10-P90 range, and compares any percentage in the user's question with the forecast ("down 15%" versus the real 29.4%). The percentage is read from the question **in code**, not by the model.
- **Every tool parameter is required.** Groq validates tool calls strictly and rejects a null for an optional number. Tools use defaults and clamp silly values.
- **Customer matching** is case-insensitive and never guesses: if several customers match ("Ironwood" matches Group, Holdings and Industries) the assistant asks which one.
- **Permission gate:** the one tool with a side effect (drafting emails) only runs when the user's message asks for drafts or emails.
- **Answer check:** every figure must come from tool results, the user's own words or earlier turns. One repair attempt, then a built-in answer built from the same figures (cash, overdue customers, anomalies; follow-ups inherit the previous topic). If no AI service is available the built-in answer is used and the response says so.
- Responses include `source` (llm or fallback), `verified`, `warnings` and a `trace` of the tools run.

### 8.8 What-if analysis (`tools/whatif.py`, `forecast.py` Scenario)

- Changes: a customer pays its overdue invoices in week N; a customer never pays (and sends no new business); all customers pay N days later or earlier; vendors paid later or earlier; a recurring payment moved by N days; a one-off cash event. Changes can be combined.
- **Same random draws for baseline and scenario**, so a "change nothing" scenario shows exactly zero difference in every week, and any other difference comes only from the change.
- Output: a plain-English sentence, the assumptions in words, a table (cash at week 13, lowest downside, chance of negative cash, receivables at risk), the weekly comparison, and which drivers moved (the driver differences add up to the change in expected cash).
- Available as a page, an API and six chat tools.
- Sample results: Ironwood Industries paying its overdue invoices next week gives week-13 cash of $783,467 (+$35,532) and a lowest downside $55,795 higher. Paying payroll a week later leaves week 13 unchanged at $747,935 but moves the low point to week 10 and lifts it by $55,056.

### 8.9 Live agent trace (item 4; code in the appendix)

- `POST /api/chat/stream` returns Server-Sent Events: `step_start`, `step_end` (with duration), `note`, then `answer`, or `error`. A keep-alive comment is sent when quiet.
- **The answer words do not stream.** Every figure is verified before it is shown, and text cannot be verified while it is still being written. What streams is the work: "Forecasting agent: breaking cash movement into its drivers… 1.0 s", "Checking every figure against the data", "Correcting the answer".
- The work runs on a worker thread with its own database session. Failures become a friendly error event without technical detail.
- Verified against a real server and a real HTTP client: first event at 0.06 s, each step as it started, verified answer at 3.39 s. (The framework's test client buffers whole responses, so it cannot show this.)
- The chat panel shows steps live, then keeps them under "How this was worked out".

---

## 9. The trust model (guardrails)

| Risk | Control |
|---|---|
| The model invents or mis-states a figure | Figures are produced by code; model text is checked token by token (dollars, percentages, days, weeks); one repair attempt; then a template or built-in answer |
| Right figures, wrong meaning ("down from the forecast") | Ready-made correct sentences supplied; a pattern check rejects the known mistake; honest labelling of AI-written text. **The checker verifies figures, not reasoning**, and this is stated openly |
| An email goes out wrongly | Drafts only; edit, approve, then send from your own email app. No send code exists |
| Emails make threats or wrong claims | Forbidden-word check; only supplied amounts and dates; invoices flagged as unusual are never emailed |
| The model calls a tool it should not | Drafting is gated on the user actually asking; unknown tools return an error, never a crash |
| Wrong customer | Ambiguous names ask which one |
| Bad input data | Quarantine with line numbers; nothing silently dropped |
| Duplicates distort the books | Excluded from every figure until a person dismisses the flag |
| Two screens disagree | One set of rules everywhere; half-up rounding in the API matches the browser |
| AI service down | Every AI feature has a deterministic fallback and says which one was used |
| Secrets | API key in `.env`, ignored by Git |

**Data sent to the AI service:** customer names, invoice numbers, amounts and dates that appear in the tool results, email facts and briefing facts. Keep that in mind before loading real data.

---

## 10. API reference

Interactive docs: `http://localhost:8002/docs`. All `/api/*` routes need data loaded first (otherwise 409 with a message).

| Method and path | Purpose |
|---|---|
| `GET /health` | Service status and whether an LLM key is set |
| `POST /api/ingest/sample` | Validate and load the synthetic CSVs |
| `GET /api/ingest/summary` | Counts, open AR/AP, metadata |
| `GET /api/analytics/kpis` | Cash, open and overdue AR, DSO, DPO |
| `GET /api/analytics/aging?kind=AR\|AP` | Aging buckets |
| `GET /api/analytics/customers` | Payment profiles |
| `GET /api/forecast?sims=&seed=` | 13-week forecast with drivers and bands |
| `GET /api/forecast/open-ar` | Payment prediction per unpaid customer invoice |
| `GET /api/forecast/backtest` | Rewind-and-compare accuracy test |
| `POST /api/anomalies/scan` | Detect duplicates and outliers (keeps earlier decisions) |
| `GET /api/anomalies?status=open\|confirmed\|dismissed\|all` | List flags |
| `POST /api/anomalies/{id}/review?status=confirmed\|dismissed` | Human decision |
| `GET /api/collections/priorities` | Ranked customers and held invoices |
| `POST /api/collections/drafts?top=&use_llm=` | Write drafts for top customers |
| `GET /api/collections/drafts?status=` | Approval queue |
| `PUT /api/collections/drafts/{id}` | Edit subject and body (drafts only) |
| `POST /api/collections/drafts/{id}/approve \| reject \| mark-sent` | Workflow (cannot skip approval) |
| `POST /api/reports/weekly?use_llm=` | Build a briefing |
| `GET /api/reports`, `/api/reports/{id}`, `/api/reports/{id}/markdown` | List, read, plain text |
| `GET /api/whatif/options` | Customers and recurring payments that can be changed |
| `POST /api/whatif` | Body `{"effects":[{...}]}`; effect types: `customer_pays`, `customer_fails`, `customers_pay_later`, `vendors_paid_later`, `shift_recurring`, `one_off` |
| `POST /api/chat` | `{message, history, use_llm}` returns answer, source, verified, warnings, trace |
| `POST /api/chat/stream` | Same, as Server-Sent Events (item 4) |

---

## 11. The dashboard

Design idea: a **ledger**. Negatives in red parentheses, totals under a double rule, tabular figures, hairline rules instead of boxes, sentence-case labels. Palette: ledger-paper green-grey, blue-black ink, ledger green for actions, margin red for risk, pencil amber for caution. Type: Literata (headings and figures) and Public Sans (interface), loaded from Google Fonts with system fallbacks.

| Page | Contents |
|---|---|
| **Cash outlook** | A plain-English opening sentence built from the verified figures; a one-row figures strip; the 13-week cash runway chart (base case with the P10-P90 band, the worst point marked in red); weekly net flow bars (hover a bar for its real drivers); aging table for receivables or payables |
| **What if** | One-click scenarios and a builder that combines changes; verdict, assumptions, with/without table, dashed-versus-green chart, driver table |
| **Collections** | Ranked customers; write drafts for the top 3, 5 or 10; edit, approve or reject each draft; open approved ones in your email app; mark sent |
| **Invoice checks** | Flags with reasons; confirm or dismiss; scan again |
| **Weekly briefing** | The briefing rendered with tables; build a new one; copy as text |
| **Chat panel** | Suggested questions; history; provenance line under each answer; live agent trace and "How this was worked out" |

Responsive: on screens narrower than about 1280 px the chat is an overlay and starts closed; on phones the navigation becomes a top bar. The dashboard talks to `http://localhost:8002` (override with `VITE_API_URL`). Open it at `localhost:5173`, not `127.0.0.1` (CORS allows only `localhost`).

# DEPLOYMENT LINKS 
BACKEND_URL=https://copilot-backend-2kcl.onrender.com/docs
FRONTEND_URL=https://copilot-5rl43sf3b-riyas-projects-e3a657d1.vercel.app