# AI-Powered Secure KPI-Driven ETL Pipeline Generator

A multi-agent, multi-client data engineering assistant that converts natural-language
business requirements into validated, documented, and visualized ETL pipelines —
while enforcing strict per-client data isolation and role-based access control.

Instead of a data engineer manually writing SQL, joining tables, and building a
dashboard, a business user simply types:

> "Calculate monthly revenue and average order value for 2025, excluding cancelled
> and refunded orders."

...and the system understands the requirement, plans the transformation, generates
and validates SQL against live BigQuery data, detects anomalies, writes documentation,
and renders interactive charts — automatically scoped to only the data that user is
authorized to see.

\---

## Table of Contents

1. [Key Features](#key-features)
2. [Architecture Overview](#architecture-overview)
3. [Tech Stack](#tech-stack)
4. [Security Model](#security-model)
5. [Roles \& Permissions](#roles--permissions)
6. [The Multi-Agent Pipeline](#the-multi-agent-pipeline)
7. [Project Structure](#project-structure)
8. [Setup \& Installation](#setup--installation)
9. [Running the Project](#running-the-project)
10. [Environment Variables](#environment-variables)
11. [API Reference](#api-reference)
12. [Testing the System](#testing-the-system)
13. [Known Limitations](#known-limitations)
14. [Possible Next Steps](#possible-next-steps)

\---

## Key Features

* **Natural language → validated SQL** — a 9-agent LangGraph pipeline turns plain
English into working BigQuery SQL, with automatic self-correction if the query
fails validation.
* **Multi-client data isolation** — five fictional companies (RetailMart, FoodGo,
TravelEase, TechStore, HealthPlus), each with its own BigQuery dataset. Isolation
is enforced at three independent layers (see [Security Model](#security-model)).
* **Multi-metric KPIs** — a single query can return several related metrics as
columns in one result set (e.g. Revenue, Retention Rate, AOV side by side per
month), matching real dashboard-ready output.
* **Real role-based access control** — four roles (Viewer, Business Analyst, Data
Engineer, Admin), each with genuinely different, server-enforced capabilities.
* **Self-correcting pipeline** — if generated SQL fails, the Validation Agent's
error is fed back to the LLM, which fixes and re-validates automatically (up to
3 attempts).
* **Explainable output** — every pipeline run produces a KPI definition, a
transformation plan, the SQL, a visual flow diagram, an anomaly report, an
AI-written business insight, and full documentation.
* **Anomaly monitoring** — period-over-period changes beyond a threshold are
automatically flagged, per metric, with plain-English alerts.
* **Persistent KPI Dashboard** — users can pin generated KPIs to a shared,
per-client dashboard with 8 chart types, sparklines, comparison periods,
and PNG/PDF export.
* **Custom SQL access** — Data Engineers and Admins can run raw SQL directly,
still hard-scoped to their own dataset.
* **User management** — Admins can view and change roles for every user in
their own organization.

\---

## Architecture Overview

```
                         ┌──────────────┐
                         │   Browser    │
                         │ (React SPA)  │
                         └──────┬───────┘
                                │ REST + Firebase ID token
                                ▼
                       ┌─────────────────┐
                       │  FastAPI Backend │
                       │  (RBAC enforced) │
                       └───┬─────────┬───┘
                           │         │
              ┌────────────┘         └────────────┐
              ▼                                    ▼
     ┌──────────────────┐                ┌───────────────────┐
     │   PostgreSQL      │                │   LangGraph        │
     │ users, clients,    │                │  9-Agent Pipeline  │
     │ pipeline\\\\\\\_history,  │                │  (Groq LLM)        │
     │ dashboard\\\\\\\_widgets  │                └─────────┬──────────┘
     └────────────────────┘                          │
                                                       ▼
                                          ┌────────────────────────┐
                                          │   MCP Server            │
                                          │ (dataset-scoped tools)  │
                                          └───────────┬─────────────┘
                                                       ▼
                                          ┌────────────────────────┐
                                          │   Google BigQuery       │
                                          │ 5 isolated datasets:     │
                                          │ client\\\\\\\_retailmart        │
                                          │ client\\\\\\\_foodgo             │
                                          │ client\\\\\\\_travelease          │
                                          │ client\\\\\\\_techstore             │
                                          │ client\\\\\\\_healthplus              │
                                          └────────────────────────┘
```

**Authentication** is handled by Firebase (email/password); **authorization**
(role + client scoping) is enforced in the FastAPI layer using PostgreSQL as the
source of truth for each user's role and assigned client.

\---

## Tech Stack

|Layer|Technology|
|-|-|
|Frontend|React (Vite), Recharts, html2canvas, jsPDF|
|Backend|FastAPI (Python), Uvicorn|
|Authentication|Firebase Authentication|
|Metadata Database|PostgreSQL (users, clients, history, dashboard widgets)|
|Data Warehouse|Google BigQuery (5 isolated datasets)|
|Agent Orchestration|LangGraph|
|LLM Provider|Groq (`openai/gpt-oss-120b`)|
|Agent-to-Data Interface|MCP (Model Context Protocol)|
|Cloud Platform|Google Cloud Platform (BigQuery, Application Default Credentials)|

\---

## Security Model

Data isolation is enforced at **three independent layers**, so a bug in any one
layer does not compromise the whole system:

### 1\. Application-level RBAC (FastAPI)

Every authenticated request looks up the caller's `role` and `bigquery\\\\\\\_dataset`
from PostgreSQL, server-side. The frontend can never specify which dataset to
query — it is always taken from the verified user's identity, never from the
request body.

### 2\. MCP tool layer (BigQuery access)

All BigQuery access goes through an MCP server whose tools
(`execute\\\\\\\_authorized\\\\\\\_query`, `get\\\\\\\_dataset\\\\\\\_schema`, `get\\\\\\\_sample\\\\\\\_rows`, etc.) take
a `bigquery\\\\\\\_dataset` parameter and reject any SQL that references a different
dataset — even if a technically skilled user (e.g. a Data Engineer with raw SQL
access) deliberately tries to reach another client's tables.

### 3\. Schema-aware AI scoping (KPI Agent)

Before any SQL is generated, the KPI Understanding Agent is given **only** the
requesting user's own schema. If a requirement references a concept, entity, or
table that doesn't belong to that schema (e.g. a RetailMart user asking about
"flight bookings"), the agent returns an `out\\\\\\\_of\\\\\\\_scope` error and the pipeline
stops — before any SQL is even attempted.

> \\\\\\\*\\\\\\\*Note on infrastructure-level isolation:\\\\\\\*\\\\\\\* the current implementation uses a
> single shared BigQuery service account with the above three layers enforcing
> isolation in code. Full Google Cloud IAM-level isolation (a separate service
> account per client, each with dataset-scoped permissions) was considered but
> not implemented in this version — noted here as an explicit, honest limitation
> rather than an overstated security claim.

\---

## Roles \& Permissions

|Capability|Viewer|Business Analyst|Data Engineer|Admin|
|-|:-:|:-:|:-:|:-:|
|Browse raw data (Data Browser)|✅|✅|✅|✅|
|View shared KPI Dashboard|✅|✅|✅|✅|
|View Pipeline History|✅|✅|✅|✅|
|Generate AI pipelines|❌|✅|✅|✅|
|Save results to Dashboard|❌|✅|✅|✅|
|Run custom SQL|❌|❌|✅|✅|
|Manage users / change roles|❌|❌|❌|✅|

All restrictions are enforced **server-side** (not just hidden in the UI) — every
protected endpoint independently checks the caller's role before acting.

Data isolation itself is **identical across all four roles**: no role, including
Admin, can ever see another client's data. Role only controls what actions a user
can take within their *own* organization's data.

\---

## The Multi-Agent Pipeline

Each pipeline run passes through this LangGraph state machine:

```
Schema Agent
     │
KPI Understanding Agent ──(out of scope?)──► Fail
     │
Transformation Agent
     │
SQL Generation Agent
     │
Validation Agent ──(invalid?)──► Self-Correction Agent ──► (loop back to Validation, max 3x)
     │ (valid)
Finalize Results
     │
Documentation Agent
     │
Monitoring Agent (anomaly detection)
     │
Insights Agent (business summary)
     │
   Return to user
```

|#|Agent|Responsibility|
|-|-|-|
|1|**Schema Agent**|Fetches only the requesting user's authorized BigQuery schema|
|2|**KPI Understanding Agent**|Parses the requirement into a structured KPI definition (supports 1+ metrics); rejects out-of-scope requests|
|3|**Transformation Agent**|Plans filters, joins, aggregations, and data-quality checks before any SQL is written|
|4|**SQL Generation Agent**|Writes BigQuery SQL for all requested metrics in one query|
|5|**Validation Agent**|Executes the SQL against real BigQuery; catches errors|
|6|**Self-Correction Agent**|On failure, feeds the real BigQuery error back to the LLM and retries (≤3 times)|
|7|**Documentation Agent**|Writes a human-readable summary of data sources, filters, transformations, and caveats|
|8|**Monitoring Agent**|Detects period-over-period anomalies per metric, phrases them as plain-English alerts|
|9|**Insights Agent**|Writes an executive summary with bullet-point key findings|

\---

## Project Structure

```
ai-etl-pipeline-generator/
├── backend/
│   ├── main.py                  # FastAPI app, all routes
│   ├── database.py               # SQLAlchemy models (User, Client, PipelineHistory, DashboardWidget)
│   ├── auth\\\\\\\_dependency.py         # Firebase token verification + RBAC lookup
│   ├── authorization.py            # Dataset-access check helper
│   ├── firebase\\\\\\\_init.py             # Firebase Admin SDK init
│   ├── agents/
│   │   ├── graph.py                  # LangGraph pipeline definition
│   │   ├── kpi\\\\\\\_agent.py                # Agent 2
│   │   ├── schema\\\\\\\_agent.py              # Agent 1
│   │   ├── transformation\\\\\\\_agent.py       # Agent 3
│   │   ├── sql\\\\\\\_agent.py                   # Agent 4
│   │   ├── validation\\\\\\\_agent.py             # Agent 5
│   │   ├── documentation\\\\\\\_agent.py           # Agent 7
│   │   ├── monitoring\\\\\\\_agent.py               # Agent 8
│   │   ├── insights\\\\\\\_agent.py                  # Agent 9
│   │   └── data\\\\\\\_generation/
│   │       └── generate\\\\\\\_\\\\\\\*.py                    # Synthetic data generators (5 clients)
│   └── mcp\\\\\\\_server/
│       └── server.py                              # MCP tools (dataset-scoped BigQuery access)
├── frontend/
│   └── src/
│       ├── App.jsx                # Screen routing, split-screen auth layout
│       ├── Auth.jsx                # Login / signup
│       ├── RoleSelect.jsx           # First-time role \\\\\\\& client selection
│       ├── Dashboard.jsx             # Generator + 3-column pipeline UI
│       ├── DashboardBuilder.jsx       # Persistent KPI dashboard page
│       ├── InsightsPanel.jsx           # Chart rendering (8 types) + business insight
│       ├── PipelineFlow.jsx             # Visual pipeline flow diagram (React Flow)
│       ├── DataBrowser.jsx               # Viewer's read-only table browser
│       ├── SqlRunner.jsx                   # Data Engineer / Admin custom SQL panel
│       ├── UserManagement.jsx               # Admin user/role management
│       ├── PipelineHistory.jsx               # Org-wide run history
│       ├── Sparkline.jsx                       # Mini inline trend charts
│       └── index.css                             # Full design system
└── README.md
```

\---

## Setup \& Installation

### Prerequisites

* Python 3.11
* Node.js + npm
* PostgreSQL (local install)
* A Google Cloud Platform project with BigQuery enabled
* A Firebase project (Email/Password authentication enabled)
* A free [Groq](https://console.groq.com/keys) API key

### 1\. Clone and set up the backend

```bash
cd backend
py -3.11 -m venv venv
venv\\\\\\\\Scripts\\\\\\\\Activate.ps1        # Windows
pip install -r requirements.txt
```

### 2\. Set up Google Cloud Application Default Credentials

```bash
gcloud auth login
gcloud config set project YOUR\\\\\\\_GCP\\\\\\\_PROJECT\\\\\\\_ID
gcloud auth application-default login
```

This avoids needing a downloaded service account key file.

### 3\. Set up PostgreSQL

```sql
CREATE DATABASE etl\\\\\\\_metadata;
\\\\\\\\c etl\\\\\\\_metadata

CREATE TABLE clients (
    client\\\\\\\_id SERIAL PRIMARY KEY,
    client\\\\\\\_name VARCHAR(100) UNIQUE NOT NULL,
    bigquery\\\\\\\_dataset VARCHAR(100) NOT NULL
);

CREATE TABLE users (
    user\\\\\\\_id SERIAL PRIMARY KEY,
    firebase\\\\\\\_uid VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,
    client\\\\\\\_id INTEGER REFERENCES clients(client\\\\\\\_id),
    created\\\\\\\_at TIMESTAMP DEFAULT CURRENT\\\\\\\_TIMESTAMP
);

CREATE TABLE pipeline\\\\\\\_history (
    history\\\\\\\_id SERIAL PRIMARY KEY,
    user\\\\\\\_id INTEGER REFERENCES users(user\\\\\\\_id),
    client\\\\\\\_id INTEGER REFERENCES clients(client\\\\\\\_id),
    requirement TEXT NOT NULL,
    kpi\\\\\\\_name VARCHAR(255),
    sql\\\\\\\_query TEXT,
    row\\\\\\\_count INTEGER,
    status VARCHAR(20) NOT NULL,
    error\\\\\\\_message TEXT,
    created\\\\\\\_at TIMESTAMP DEFAULT CURRENT\\\\\\\_TIMESTAMP
);

CREATE TABLE dashboard\\\\\\\_widgets (
    widget\\\\\\\_id SERIAL PRIMARY KEY,
    user\\\\\\\_id INTEGER REFERENCES users(user\\\\\\\_id),
    client\\\\\\\_id INTEGER REFERENCES clients(client\\\\\\\_id),
    title VARCHAR(255) NOT NULL,
    requirement TEXT NOT NULL,
    chart\\\\\\\_type VARCHAR(20) DEFAULT 'bar',
    kpi\\\\\\\_definition JSONB,
    results JSONB,
    insight JSONB,
    monitoring\\\\\\\_report JSONB,
    sql\\\\\\\_query TEXT,
    bigquery\\\\\\\_dataset VARCHAR(100),
    created\\\\\\\_at TIMESTAMP DEFAULT CURRENT\\\\\\\_TIMESTAMP
);

INSERT INTO clients (client\\\\\\\_name, bigquery\\\\\\\_dataset) VALUES
('RetailMart', 'client\\\\\\\_retailmart'),
('FoodGo', 'client\\\\\\\_foodgo'),
('TravelEase', 'client\\\\\\\_travelease'),
('TechStore', 'client\\\\\\\_techstore'),
('HealthPlus', 'client\\\\\\\_healthplus');
```

### 4\. Generate synthetic data and load into BigQuery

```bash
cd backend/agents/data\\\\\\\_generation
python generate\\\\\\\_retailmart.py
python generate\\\\\\\_foodgo.py
python generate\\\\\\\_travelease.py
python generate\\\\\\\_techstore.py
python generate\\\\\\\_healthplus.py
```

Then, in the BigQuery Console, create 5 datasets matching the names in the
`clients` table above, and upload each generated CSV as a table (auto-detect
schema, skip 1 header row).

### 5\. Set up Firebase

1. Create a Firebase project (or link to your existing GCP project).
2. Enable **Email/Password** under Authentication → Sign-in method.
3. Register a Web App and copy the config into `frontend/src/firebase.js`.

### 6\. Configure environment variables

Create `backend/.env`:

```env
DATABASE\\\\\\\_URL=postgresql://postgres:YOUR\\\\\\\_PASSWORD@localhost:5432/etl\\\\\\\_metadata
GROQ\\\\\\\_API\\\\\\\_KEY=your\\\\\\\_groq\\\\\\\_api\\\\\\\_key
GOOGLE\\\\\\\_CLOUD\\\\\\\_PROJECT=your\\\\\\\_gcp\\\\\\\_project\\\\\\\_id
```

### 7\. Set up the frontend

```bash
cd frontend
npm install
```

\---

## Running the Project

You need **two terminals** running simultaneously.

**Terminal 1 — Backend:**

```bash
cd backend
venv\\\\\\\\Scripts\\\\\\\\Activate.ps1
uvicorn main:app --reload --port 8001
```

**Terminal 2 — Frontend:**

```bash
cd frontend
npm run dev
```

Open the URL Vite prints (typically `http://localhost:5173`).

### First-time use

1. Sign up with any email/password.
2. You'll land on the **role selection** screen — pick a client organization and
a role.
3. You're taken to the main dashboard, scoped entirely to that client's data.

\---

## Environment Variables

|Variable|Where|Purpose|
|-|-|-|
|`DATABASE\\\\\\\_URL`|`backend/.env`|PostgreSQL connection string|
|`GROQ\\\\\\\_API\\\\\\\_KEY`|`backend/.env`|LLM provider key for all 6 AI agents|
|`GOOGLE\\\\\\\_CLOUD\\\\\\\_PROJECT`|`backend/.env`|GCP project ID for BigQuery|

Firebase Admin credentials are handled via `gcloud auth application-default login` rather than a downloaded key file — no Firebase service-account secret
needs to be stored.

\---

## API Reference

All endpoints (except `/health` and `/clients`) require a Firebase ID token:
`Authorization: Bearer <token>`

|Method|Path|Access|Purpose|
|-|-|-|-|
|GET|`/health`|Public|Health check|
|GET|`/me`|Any authenticated user|Current user's identity + role + client|
|GET|`/clients`|Public|List of client organizations (for signup)|
|POST|`/register-user`|Authenticated, unregistered|Assign role + client on first login|
|GET|`/tables`|Any role|List tables in the user's dataset|
|GET|`/tables/{table}/preview`|Any role|Sample rows from a table|
|GET|`/tables/{table}/stats`|Any role|Row/column counts + schema|
|POST|`/run-sql`|Data Engineer, Admin|Execute raw SQL (dataset-scoped)|
|POST|`/generate-pipeline`|Business Analyst, Data Engineer, Admin|Run the full 9-agent pipeline|
|GET|`/history`|Any role|Org-wide pipeline run history|
|GET|`/dashboard/widgets`|Any role|List saved dashboard widgets|
|POST|`/dashboard/widgets`|Not Viewer|Save a KPI to the dashboard|
|DELETE|`/dashboard/widgets/{id}`|Not Viewer|Remove a widget|
|PATCH|`/dashboard/widgets/{id}/chart-type`|Not Viewer|Change a widget's chart type|
|GET|`/admin/users`|Admin only|List users in the Admin's organization|
|PATCH|`/admin/users/{id}/role`|Admin only|Change a user's role|

\---

## Testing the System

### Functional test queries (per client)

Each client has synthetic data with intentionally seeded quality issues (nulls,
negative amounts, duplicates) for realistic testing. Example queries to try:

* `Calculate monthly revenue for 2025, excluding cancelled and refunded orders.`
* `Compare total revenue of 2025 and 2026.`
* `Calculate monthly revenue and average order value for 2025.` *(multi-metric)*
* `What is the total revenue by product category?` *(category breakdown)*

### Isolation tests

* As a RetailMart user: `Show me restaurant delivery times.` → should be rejected
as out-of-scope.
* Via Custom SQL Runner, as any role: try
`SELECT \\\\\\\* FROM client\\\\\\\_healthplus.patients LIMIT 5` while logged in as a
different client → should be rejected by the MCP layer.

### Self-correction test

Manually inject an invalid column name into a query via the standalone
`agents/test\\\\\\\_self\\\\\\\_correction.py` script to watch the Validation → Correction →
Re-validation loop fire and succeed.

\---

## Known Limitations

* **No infrastructure-level IAM isolation** — a single BigQuery service account
is used; isolation is enforced entirely in application code (see
[Security Model](#security-model)).
* **Dashboard widgets are snapshots** — saved KPIs store the results at save
time; there is no live-refresh mechanism.
* **Pie/Donut charts show one metric only** — inherent to proportion charts;
multi-metric queries display a note explaining this.
* **Grouped Bar, Heatmap, and Box Plot chart types are intentionally omitted** —
the data shape (one label + one or more values per row) doesn't support them
meaningfully.
* **LLM provider has shifted during development** (OpenAI → Gemini → Groq) due
to free-tier quota constraints; Groq's `openai/gpt-oss-120b` is the current,
stable choice.

## Possible Next Steps

* Deploy to GCP (Cloud Run for backend + MCP server, Firebase Hosting for
frontend) for public accessibility.
* Per-client BigQuery service accounts with IAM-scoped permissions for
infrastructure-level isolation.
* Live dashboard widget refresh and scheduled auto-refresh.
* Multi-dimensional chart types (grouped bar, heatmap) once queries support
two-dimensional breakdowns.

