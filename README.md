# 🚀 Y Combinator Founder Outreach — Agentic AI System & Review Dashboard

An enterprise-grade, deterministic multi-agent pipeline designed to discover Y Combinator startups, extract verified founder profiles, assess ideal candidate profile (ICP) alignment, and generate grounded, channel-aware outreach messages with a dedicated human-in-the-loop review dashboard.

---

## 🏗 System Architecture

```text
                               ┌────────────────────────────────┐
                               │   Dashboard UI / CLI Control   │
                               │  (FastAPI + Vanilla JS SPA)    │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                              ┌──────────────────────────────────┐
                              │     Master Orchestrator          │
                              │ (Deterministic Stage Execution)  │
                              └────────────────┬─────────────────┘
                                               │
         ┌──────────────────┬──────────────────┼──────────────────┬──────────────────┐
         ▼                  ▼                  ▼                  ▼                  ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Discovery Agent │ │  Founder Agent  │ │    Fit Agent    │ │  Message Agent  │ │ Memory / Storage│
│ • Algolia Search│ │ • Public Scrape │ │ • ICP Evaluation│ │ • LinkedIn Note │ │ • PostgreSQL /  │
│ • Dynamic Key   │ │ • Inertia.js    │ │ • Score (0-100) │ │   (<= 300 char) │ │   SQLite        │
│ • YC-OSS Fallback │ • LinkedIn Match│ │ • Tier (H/M/L)  │ │ • YC Job Note   │ │ • Redis / In-Mem│
│                 │ │ • Jobs Parsing  │ │ • LLM Reasoning │ │ • Cold Email    │ │ • Blacklist     │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘
```

---

## ✨ Key Features

### 1. 🔍 Autonomous Multi-Stage Pipeline
- **Discovery Agent**: Queries Algolia with automatic runtime credential discovery, with fallback to the official YC-OSS catalog.
- **Founder Extraction Agent**: Scrapes public YC company profiles, parses Inertia.js props, extracts founder details, verifies LinkedIn profiles, and collects active job postings.
- **Fit Evaluation Agent**: Evaluates startup fit against candidate background (`profile.json`) using multi-factor criteria (industry match, technical scope, stage).
- **Message Drafting Agent**: Produces grounded, hyper-personalized outreach drafts across 3 channels:
  - **LinkedIn Connection Note** (strictly enforced $\le$ 300 characters with schema validation and auto-repair)
  - **Work at a Startup / YC Job Application Note**
  - **Cold Email** (Subject line + compelling value proposition)

### 2. 🛡 Dual-Tier Resilience & Fallbacks
- **Durable Storage**: Primary PostgreSQL storage with automatic failover to local SQLite (`data/outreach.db`).
- **Session Caching**: Primary Redis cache with automatic failover to thread-safe in-memory cache.
- **Model Fallback Chain**: Multi-tiered OpenRouter LLM fallback with exponential backoff and jitter.

### 3. 🖥 Human-in-the-Loop Review Dashboard
- **Overview Analytics**: Real-time funnel metrics (Discovered $\to$ Founders $\to$ Fit $\to$ Qualified $\to$ Drafts).
- **Interactive Lead Table**: Filter by status (`pending_review`, `approved`, `sent`, `replied`), batch, and fit tier.
- **Lead Detail Dossier**: Side-by-side company profile, founder information, fit analysis, and editable message drafts.
- **Safety First**: Messages are **never sent automatically**; approved messages copy to clipboard or open LinkedIn directly.
- **Blacklist Manager**: Instant domain, LinkedIn URL, or company blacklisting with reason tracking.
- **Live Terminal & Health**: Real-time log streaming, health check heartbeats, database snapshots, and 1-click re-run.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11+
- Git
- *(Optional)* Docker (for PostgreSQL & Redis; SQLite & in-memory cache will automatically activate if Docker is absent)

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/rajm5113/Y-Combinator-Agentic-AI-.git
cd Y-Combinator-Agentic-AI-

python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example `.env` file and supply your API credentials:

```bash
copy .env.example .env
```

Edit `.env`:
```ini
OPENROUTER_API_KEY=sk-or-v1-your-key-here
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/yc_outreach
REDIS_URL=redis://localhost:6379/0
```

> **Note:** If PostgreSQL or Redis are not running, the system will automatically fall back to local SQLite (`data/outreach.db`) and in-memory cache.

### 3. Launch Dashboard

```bash
python launcher.py
```
Open **`http://127.0.0.1:8501`** in your browser.

---

## 💻 CLI Usage

You can also run the agent pipeline directly from the command line:

```bash
# Run discovery, founder extraction, fit evaluation, and message generation
python cli.py run --batch "Fall 2026" --limit 5 --min-score 50

# Dry-run (discovery only, no LLM calls)
python cli.py run --batch "Fall 2026" --dry-run

# View database funnel statistics
python cli.py stats

# Export qualified leads
python cli.py export --format csv --min-score 50 --output leads.csv

# Manage blacklist
python cli.py blacklist add --type domain --value spamcompany.com --reason "Not hiring"
python cli.py blacklist list
```

---

## 🧪 Automated Test Suite

The project includes an extensive test suite verifying handlers, agents, fallbacks, storage adapters, message length constraints, and dashboard endpoints:

```bash
python -m pytest -v tests/
```

**Test Coverage Highlights:**
- `test_discovery.py`: Dynamic Algolia key extraction & fallback mechanisms.
- `test_founder.py`: Inertia.js parsing, LinkedIn URL normalization, blacklist checking.
- `test_fit.py`: ICP alignment scoring and threshold filtering.
- `test_message.py`: LinkedIn 300-char limit enforcement, schema auto-repair, fallback models.
- `test_dashboard.py`: REST API endpoints, filtering, sequential navigation, and health checks.
- `test_backup.py`: Database snapshots and point-in-time restore.

---

## 📜 License & Ethical Scraping Notice

This tool is designed for targeted, respectful founder outreach:
- Implements polite request delays and respects robots/rate limits.
- Never sends unsolicited messages automatically; all outreach requires explicit human review and approval.
