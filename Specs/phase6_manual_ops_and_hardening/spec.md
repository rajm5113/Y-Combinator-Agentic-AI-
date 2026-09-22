# Phase 6: Manual Operations & Production Hardening

## Executive Summary

Phase 6 hardens the YC Founder Outreach Agent into an exceptionally robust, operator-controlled, production-grade application.

Rather than implementing a background cron daemon that runs automated scans on a fixed schedule (e.g. daily at 8:00 AM), Phase 6 explicitly pivots to a **Manual Operations & Production Hardening** architecture. In talent outreach, founder timing, judgment, and high conviction supersede arbitrary periodic automation. The operator decides when to scan, what parameters to target, inspects the live results, and initiates manual delivery.

```text
                 OPERATOR (YOU)
                       │
                       ▼
               Open Dashboard
                       │
                       ▼
       ┌───────────────────────────────┐
       │   [ RUN PIPELINE ]            │
       │   Last config: Fall 2026      │
       │   Limit: 20 | Min: 60 | AI/ML │
       │   [ ⚡ RUN AGAIN ]             │
       └───────────────────────────────┘
                       │
                       ▼
             FastAPI /api/pipeline/run
                       │
                       ▼
               MasterOrchestrator
         (With run history & cancel)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    Discovery       Founder          Fit
                                      ↓
                                Qualification
                                      ↓
                                   Message
                                      ↓
                             PostgreSQL Memory
                                      ↓
                              Dashboard Review
```

### Removed in Phase 6
- ❌ Cron jobs / Background daemon timers
- ❌ Fixed daily 8:00 AM scheduled scans
- ❌ Automatic un-monitored YC scraping
- ❌ Automated messaging / notification spam

### Added in Phase 6
- ✅ **Operator-Controlled Execution**: Single-click manual triggers with explicit parameters.
- ✅ **"Run Again" Configuration Memory**: Server and UI remember the last-used pipeline settings (batch, limit, industry, min score, concurrency) for instant 1-click repeated runs.
- ✅ **Run History & Cancellation**: Persistent tracking of all historical runs (`pipeline_runs` table), run duration, metrics, and active run cancellation (`POST /api/pipeline/cancel`).
- ✅ **Deep Health Diagnostics (`GET /api/health`)**: Instant checks for PostgreSQL (Docker port 15432), Redis (Docker port 6379), OpenRouter API key validity, and database integrity with live UI badge.
- ✅ **Unified Startup Launcher (`launcher.py` / `start.bat`)**: Zero-friction local launcher performing pre-flight sanity checks, running migrations, booting the server, and opening the browser.
- ✅ **Resilience & Error Hardening**: Graceful handling of 429s, API timeouts, fail-soft lead recovery, and idempotent resume capabilities.
- ✅ **Database Backup & Snapshot Engine**: 1-click snapshot creation, listing, and restoration (`POST /api/backup`, `POST /api/backups/restore`, `cli.py backup`).
- ✅ **Live Log Console**: Real-time structured log streaming in the dashboard UI (`GET /api/logs`) with log level filtering.

---

## 1. Architecture Overview

### 1.1 Architectural Principles (agent-architecture-advisor)

1. **You Are Always in Control**: Automation without supervision creates token waste and spam risks. Orchestration is deterministic, code-based, and human-initiated.
2. **Server-Authoritative State**: Application state lives in PostgreSQL and Redis. The UI is a stateless renderer. Memory of previous runs is persisted in the database, not in volatile browser memory.
3. **Observability as a First-Class Feature**: The operator must have real-time visibility into system health, log streams, and active stage progress.
4. **Fail-Soft Isolation**: An error in one company or founder extraction must never abort the entire batch.

### 1.2 Component Architecture Diagram

```mermaid
flowchart TD
    subgraph UI["DASHBOARD UI (Vanilla HTML/CSS/JS)"]
        CONTROL["Pipeline Control View"]
        LAST_CFG["'Run Again' Quick Launcher"]
        HEALTH_BADGE["System Health Indicator"]
        LOG_CONSOLE["Live Log Console"]
        BACKUP_UI["Backup & Export Manager"]
    end

    subgraph API["FASTAPI SERVER (dashboard/server.py)"]
        EP_HEALTH["GET /api/health"]
        EP_RUN["POST /api/pipeline/run"]
        EP_CANCEL["POST /api/pipeline/cancel"]
        EP_HISTORY["GET /api/pipeline/history"]
        EP_LOGS["GET /api/logs"]
        EP_BACKUP["POST /api/backup"]
        EP_RESTORE["POST /api/backups/restore"]
    end

    subgraph Core["OPERATIONAL HARDENING ENGINES"]
        ORCH["MasterOrchestrator (pipeline.py)"]
        LOG_BUFFER["RingBufferLogHandler (logging)"]
        HEALTH_SVC["HealthChecker (diagnostics)"]
        BACKUP_SVC["BackupEngine (storage)"]
    end

    subgraph Storage["BUSINESS & SESSION MEMORY"]
        PG[("PostgreSQL (15432)")]
        REDIS[("Redis (6379)")]
        FS[("data/backups/ (Snapshots)")]
    end

    CONTROL --> EP_RUN
    LAST_CFG --> EP_RUN
    CONTROL --> EP_CANCEL
    HEALTH_BADGE --> EP_HEALTH
    LOG_CONSOLE --> EP_LOGS
    BACKUP_UI --> EP_BACKUP

    EP_HEALTH --> HEALTH_SVC
    EP_RUN --> ORCH
    EP_CANCEL --> ORCH
    EP_LOGS --> LOG_BUFFER
    EP_BACKUP --> BACKUP_SVC

    ORCH --> PG
    ORCH --> REDIS
    BACKUP_SVC --> PG
    BACKUP_SVC --> FS
```

---

## 2. Manual Run Management & "Run Again" Feature

### 2.1 Schema: `pipeline_runs` Table
All manual runs are recorded in PostgreSQL to provide auditability and run memory:

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) UNIQUE NOT NULL,
    batch VARCHAR(64) NOT NULL,
    industry VARCHAR(128),
    startup_limit INTEGER NOT NULL DEFAULT 5,
    min_fit_score INTEGER NOT NULL DEFAULT 50,
    max_concurrency INTEGER NOT NULL DEFAULT 5,
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'running', -- 'running', 'completed', 'cancelled', 'failed'
    progress_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds REAL,
    stats_json JSONB DEFAULT '{}'::jsonb,
    error_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at ON pipeline_runs(started_at DESC);
```

### 2.2 "Remember Last Configuration" & One-Click "Run Again"
1. **Server-Side Tracking**:
   - `GET /api/pipeline/last-config` returns the configuration of the most recent pipeline run:
     ```json
     {
       "batch": "Fall 2026",
       "industries": ["B2B Software"],
       "limit": 20,
       "min_fit_score": 60,
       "max_concurrency": 5,
       "dry_run": false,
       "started_at": "2026-09-22T10:00:00Z"
     }
     ```
2. **Dashboard UI UX**:
   - The Pipeline view prominently displays the **Last Run Summary**:
     ```text
     ┌──────────────────────────────────────────────────────────────┐
     │ ⚡ LAST RUN CONFIGURATION                                    │
     │ Batch: Fall 2026 • Limit: 20 • Min Score: 60 • B2B Software │
     │ Status: Completed (4m 12s ago)                              │
     │                                                              │
     │ [ ⚡ RUN AGAIN WITH THESE SETTINGS ]   [ Edit Config ]        │
     └──────────────────────────────────────────────────────────────┘
     ```
   - Clicking `[ ⚡ RUN AGAIN ]` immediately submits the cached parameters to `POST /api/pipeline/run` with zero input friction.

### 2.3 Active Run Cancellation
- If a long batch is running and the operator needs to stop it:
  - Endpoint: `POST /api/pipeline/cancel`
  - Implementation: An `asyncio.Event` cancellation flag in `MasterOrchestrator` checked between stages and company processing loops.
  - State transitions cleanly to `status = 'cancelled'`, logs are preserved, and partial work remains safely committed in PostgreSQL.

---

## 3. Health Checks & Pre-Flight Diagnostics

### 3.1 Endpoint: `GET /api/health`
Provides structured diagnostics in under 200ms:

```json
{
  "status": "healthy",
  "timestamp": "2026-09-22T23:45:00Z",
  "version": "1.0.0",
  "components": {
    "database": {
      "status": "up",
      "type": "postgresql",
      "url": "postgresql://postgres:***@localhost:15432/yc_outreach",
      "latency_ms": 3.4,
      "counts": {
        "startups": 84,
        "founders": 142,
        "evaluations": 84,
        "drafts": 28,
        "blacklist": 2
      }
    },
    "redis": {
      "status": "up",
      "url": "redis://localhost:6379/0",
      "latency_ms": 1.1,
      "connected_clients": 2
    },
    "llm_client": {
      "status": "configured",
      "provider": "OpenRouter",
      "primary_model": "anthropic/claude-3.5-sonnet",
      "fallback_model": "openai/gpt-4o-mini"
    },
    "pipeline": {
      "is_running": false,
      "last_run_status": "completed",
      "last_run_at": "2026-09-22T22:30:00Z"
    }
  }
}
```

### 3.2 UI Health Indicator
- Top-right corner of the dashboard navbar features a live status badge:
  - 🟢 **SYSTEM READY** (Postgres + Redis + LLM active)
  - 🟡 **FALLBACK MODE** (SQLite/InMemory fallback active)
  - 🔴 **SYSTEM ERROR** (Database unreachable)
- Clicking the badge opens a compact diagnostics modal with ping latencies and connection parameters.

---

## 4. Production Launcher & Pre-Flight Checks

### 4.1 `launcher.py`
A single script to boot the entire system safely:

```bash
python launcher.py [--port 8501] [--no-browser] [--skip-checks]
```

### 4.2 Automated Pre-Flight Check Pipeline
Before starting Uvicorn, `launcher.py` executes:
1. **Python Environment Verification**: Asserts Python >= 3.10 and all packages from `requirements.txt` are imported without errors.
2. **Container / Port Availability**:
   - Tests port 15432 for PostgreSQL. If not responding, emits an alert: `⚠️ PostgreSQL on port 15432 unreachable. Check Docker: docker ps`.
   - Tests port 6379 for Redis. If not responding, activates InMemoryCache fallback seamlessly.
3. **Database Schema Sync**:
   - Executes `StorageEngine.init_db()` to create any missing tables (`pipeline_runs`, `message_drafts`, etc.) or missing indexes.
4. **Browser Auto-Open**:
   - Polls `http://127.0.0.1:8501/api/health` until HTTP 200, then triggers default browser open.

### 4.3 Windows Batch Launcher: `start.bat`
```batch
@echo off
title YC Founder Outreach Dashboard
echo Starting YC Founder Outreach System...
python launcher.py
pause
```

---

## 5. Error Hardening & Resilience

### 5.1 Fail-Soft Company Isolation
In `MasterOrchestrator`, individual company failures are isolated within `try...except` blocks:
- If company X throws an unexpected 500 or Inertia parse error during founder extraction, the orchestrator logs a warning, appends company X to `errors`, and continues immediately to company X+1.
- No single malformed company page can abort a 50-lead batch run.

### 5.2 Idempotent Batch Resume
- When running a batch with `--min-score 50`, `MasterOrchestrator` skips companies that have already been discovered, founders already extracted, and fit evaluations already completed in PostgreSQL (unless `--force-refresh` is explicitly passed).
- Re-running the pipeline on the same batch only consumes LLM tokens for newly discovered or un-evaluated startups.

### 5.3 Exponential Backoff with Jitter for OpenRouter 429s
Enhance `ResilientLLMClient`:
- Backoff delays: $T = \text{base} \times 2^{\text{attempt}} + \text{uniform}(0, 1)$
- Specifically handle OpenRouter `429 - Provider returned error` by immediately attempting the configured Tier fallback model (e.g. falling back from Sonnet to GPT-4o-mini or Nemotron).

---

## 6. Database Backup, Snapshot & Restore

### 6.1 Requirements
In production, user-edited drafts, custom notes, fit evaluations, and blacklisted entities must be securely snapshot-able before any bulk run.

### 6.2 Snapshot Storage Structure
Backups are saved to `data/backups/outreach_snapshot_YYYYMMDD_HHMMSS.json.gz`:
```text
data/
└── backups/
    ├── outreach_snapshot_20260922_200000.json.gz
    └── outreach_snapshot_20260922_230000.json.gz
```

### 6.3 Backup Endpoints & CLI
- `POST /api/backup`: Triggers an atomic read of all tables (`startups`, `founders`, `fit_evaluations`, `message_drafts`, `outreach_records`, `blacklist`, `pipeline_runs`) and writes a compressed snapshot.
- `GET /api/backups`: Lists existing snapshots with file sizes, timestamps, and record counts.
- `POST /api/backups/restore`: Restores state from a chosen snapshot.
- CLI:
  ```bash
  python cli.py backup [--output data/backups/my_snapshot.json]
  python cli.py restore --file data/backups/my_snapshot.json
  ```

---

## 7. Live Log Streaming & Observability

### 7.1 RingBufferLogHandler
An in-memory circular buffer (`collections.deque(maxlen=1000)`) attached to the Python `logging` root:
- Retains the last 1,000 log records formatted as structured objects:
  ```json
  {
    "timestamp": "2026-09-22T23:50:12Z",
    "level": "INFO",
    "logger": "pipeline",
    "message": "Discovered 5 startups from Fall 2026"
  }
  ```

### 7.2 Endpoint: `GET /api/logs`
- Query parameters:
  - `limit`: number of lines (default: 100, max: 1000)
  - `level`: filter by level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
  - `since`: timestamp filter for live polling

### 7.3 Dashboard Log Console
In the Pipeline Control view:
- Expandable **Live Console** terminal with dark background, monospaced font, colored log levels (Green for INFO, Amber for WARNING, Red for ERROR), auto-scroll toggle, and copy logs button.

---

## 8. API Specification Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Comprehensive system health & dependency latency |
| `POST` | `/api/pipeline/run` | Launch manual pipeline run (background task) |
| `POST` | `/api/pipeline/cancel` | Abort/cancel active pipeline run |
| `GET` | `/api/pipeline/status` | Current status, progress message, active stage |
| `GET` | `/api/pipeline/last-config` | Configuration of the most recent pipeline run |
| `GET` | `/api/pipeline/history` | Paginated list of historical manual runs |
| `GET` | `/api/logs` | Real-time structured log buffer |
| `POST` | `/api/backup` | Create atomic database backup snapshot |
| `GET` | `/api/backups` | List available backup snapshots |
| `POST` | `/api/backups/restore` | Restore database state from a backup snapshot |

---

## 9. Dashboard UI Additions

1. **Pipeline View — "Run Again" Card**:
   - Prominently showcases the parameters of the last execution.
   - Single-click `[ ⚡ Run Again ]` button.
2. **Pipeline View — Cancel Button & Live Log Console**:
   - `[ 🛑 Cancel Run ]` button appears while pipeline is running.
   - Embedded expandable real-time terminal log viewer.
3. **Navbar — System Health Indicator**:
   - Live status dot (🟢/🟡/🔴) with ping latencies on hover/click.
4. **Overview View — Backup Quick Action**:
   - 1-click `[ 💾 Create Database Backup ]` button with toast feedback.

---

## 10. CLI Additions

- `python cli.py backup [--output PATH]`: Exports full database to backup snapshot.
- `python cli.py restore --file PATH`: Restores database from snapshot.
- `python cli.py health`: Prints diagnostic health report to console (Postgres, Redis, LLM, Tables).
- `python launcher.py`: Unified system bootstrapper with pre-flight checks.

---

## 11. Verification & Testing Plan

### Automated Test Suite (Target: 140+ Passing Tests)
1. `tests/test_health.py`:
   - Health check with Postgres/Redis active
   - Health check with fallback mode
   - Diagnostics latency & payload validation
2. `tests/test_run_management.py`:
   - Persistence of `pipeline_runs` records
   - `last-config` endpoint retrieval
   - Pipeline run cancellation handling
3. `tests/test_backup.py`:
   - Atomic backup creation
   - Backup listing
   - Snapshot restore integrity & validation
4. `tests/test_logging.py`:
   - Ring buffer captures formatted log lines
   - Level filtering (`ERROR`, `WARNING`, `INFO`)
   - Limit & offset validation
5. Full regression pass across Phases 1–5:
   - Ensure all 119 existing tests continue to pass without regression.
