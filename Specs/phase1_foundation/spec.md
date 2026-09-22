# Phase 1: System Foundation, Multi-Tier Memory Engine & Tool Architecture

## Purpose
This phase establishes the **rock-solid foundation** upon which the entire autonomous agent system operates. Based on the architectural blueprint and constraints:
1. **Zero Token Waste**: Pure deterministic code handles orchestration, routing, caching, and state transitions; LLM reasoning is deployed strictly where cognitive evaluation is mandatory.
2. **OpenRouter Free Model Orchestration & Seamless Fallback Engine**: Integration with OpenRouter's free model tier with an automated, zero-downtime fallback chain across capable models when rate limits (HTTP 429/quota limits) occur.
3. **Broad Agent Hierarchy Support**: Master Orchestrator dispatch architecture (Hybrid: code-based deterministic dispatch for standard flows, LLM reasoning for ambiguous stimuli) with clean specialist handoffs.
4. **4-Tier Stateful Memory Engine**:
   - **Session Memory (Redis)**: Ephemeral execution DAG, working state, agent handoffs, run scratchpad.
   - **Cache Memory (Redis)**: Deterministic tool result caching with TTL, rate limit token buckets.
   - **Business Memory (PostgreSQL)**: Long-term relational storage for Startups, Founders, Fit Scores, Outreach Records, and the strict "Never Contact Again" blacklist.
   - **Explicit & Implicit Memory (PostgreSQL)**: Explicit ground-truth candidate profile and standing constraints; Implicit long-term insights (founder response heuristics, communication patterns, interaction histories).
5. **Decoupled Tool Architecture**: Grouped JSON schema files per agent (OpenAI function-calling standard) paired with Python fulfillment engines returning typed `{success, result, error, error_type}` contracts.

Nothing in downstream Phases 2–6 (Harvesting, Fit Scoring, Messaging, Dashboard, Automation) can run without this foundation.

---

## Spec 1.1: Configuration, Model Fallback Engine, Candidate Knowledge Base & Tool Schema Registry

### 1. Centralized Configuration (`config/settings.py`)
Manages all environmental parameters loaded from `.env` with strict Pydantic validation:
- **OpenRouter Settings**:
  - `OPENROUTER_API_KEY`: Authentication key for OpenRouter.
  - `OPENROUTER_BASE_URL`: Defaults to `https://openrouter.ai/api/v1`.
  - `APP_NAME` & `SITE_URL`: Headers required by OpenRouter.
- **Model Catalog & Fallback Chains**:
  Configured priority lists mapped by task capability:
  - **Tier 1 (Deep Reasoning / Complex Planning)**:
    1. `nvidia/nemotron-3-ultra-550b-a55b:free`
    2. `nvidia/nemotron-3-super-120b-a12b:free`
    3. `thinkingmachines/inkling:free`
  - **Tier 2 (Structured Extraction, Tool Calling & Coding)**:
    1. `google/gemma-4-31b-it:free`
    2. `google/gemma-4-26b-a4b-it:free`
    3. `dots-studio/dots3-note-preview:free`
  - **Tier 3 (Fast Routing, Filtering & Simple Tasks)**:
    1. `inclusionai/ling-3.0-flash:free`
    2. `google/gemma-4-26b-a4b-it:free`
- **Database & Cache Connection URLs**:
  - `DATABASE_URL`: PostgreSQL connection string (e.g., `postgresql://user:password@localhost:5432/yc_outreach`).
  - `REDIS_URL`: Redis connection string (e.g., `redis://localhost:6379/0`).
  - Optional local fallback mode (`USE_SQLITE_FALLBACK=True` if local PostgreSQL is not running during early prototyping).
- **Targeting & Scoring Filters**:
  - `DEFAULT_BATCHES`: Default YC batches to scan (e.g., `["Fall 2026", "Summer 2026", "Winter 2026"]`).
  - `DEFAULT_INDUSTRIES`: Focus domains (e.g., `["B2B", "Developer Tools", "AI/ML", "Analytics"]`).
  - `FIT_THRESHOLD_HIGH`: Score threshold for immediate high-priority outreach (default: `75`).
  - `FIT_THRESHOLD_MEDIUM`: Score threshold for secondary review (default: `50`).

---

### 2. OpenRouter Model Client & Dynamic Fallback Engine (`config/llm_client.py`)
Implements an enterprise-grade OpenAI SDK wrapper targeting OpenRouter with proactive resilience:
- **OpenAI SDK Client**: Initialized with OpenRouter base URL and headers.
- **Dynamic Fallback Execution (`call_with_fallback`)**:
  - Takes a task tier (e.g., `REASONING`, `EXTRACTION`, `FAST`) and the messages/tools payload.
  - Iterates through the models designated for that tier in priority order.
  - If a model responds with HTTP 429 (Rate Limit Exceeded), 503 (Overloaded), or timeout:
    - Logs a warning with the failed model name and error code.
    - Applies exponential backoff with jitter (preventing thundering herd).
    - Automatically retries with the next model in the fallback chain.
  - Returns the validated response along with metadata indicating which model fulfilled the request and token usage.
- **Strict Temperature Discipline**: `temperature=0` for structured extraction and routing; `temperature=0.3` for creative message generation.
- **Budget / Token Guards**: Caps maximum completion tokens per call to prevent runaway generation loops.

---

### 3. Candidate Profile Knowledge Base — Explicit Memory (`config/profile.json`)
The candidate profile acts as the **Explicit Memory** of ground truth facts that all downstream matching and writing agents read from:
- `candidate_name`: Candidate's full name.
- `headline`: Professional one-liner / positioning statement.
- `email`: Contact email address.
- `linkedin_url`: Candidate LinkedIn profile.
- `portfolio_url`: GitHub / personal website / portfolio URL.
- `target_roles`: Array of titles targeted (e.g., `["Founding Engineer", "Full Stack Engineer", "AI/ML Engineer"]`).
- `skills`: Categorized skill taxonomy (`languages`, `frameworks`, `ai_ml`, `cloud_devops`, `databases`).
- `experience_highlights`: Chronological key accomplishments with quantifiable metrics.
- `featured_projects`: Real-world projects with technical stack, architecture role, and demo URLs.
- `target_startup_criteria`:
  - `preferred_stages`: (e.g., `["Seed", "Series A", "Early Stage"]`).
  - `preferred_team_size_max`: Maximum team size (e.g., 25).
  - `preferred_industries`: (e.g., `["Developer Tools", "AI Agents", "B2B SaaS"]`).
- `pitch_angles`: Value propositions tailored to different startup archetypes (e.g., "Rapid 0-to-1 builder", "AI agent systems specialist", "High-scale backend engineer").

---

### 4. Tool Schema Registry & Python Engine (`tools/`)
In accordance with the Decoupled Tool Contract (agent-architecture-advisor principles):
- **Schemas in Grouped JSON Files (`tools/schemas/`)**:
  - `discovery_tools.json`: Tool definitions for searching YC directories, filtering batches, and fetching startup metadata.
  - `founder_tools.json`: Tool definitions for scraping founder profiles, bio extraction, and social verification.
  - `fit_tools.json`: Tool definitions for profile comparison and qualification scoring.
  - `outreach_tools.json`: Tool definitions for message drafting and blacklist lookup.
  - All schemas formatted strictly according to the **OpenAI Function Calling specification** with explicit types, field descriptions (crafted for AI understanding), and required fields.
- **Tool Fulfillment Engine (`tools/engine.py`)**:
  - Maps tool schema names to Python executable functions.
  - Re-validates all arguments with Pydantic prior to execution (never trust model output).
  - Executes tool logic inside defensive `try/except` blocks.
  - Always returns a standardized dictionary contract:
    ```python
    {
        "success": bool,
        "result": Any,          # Structured data on success
        "error": Optional[str], # Human-readable error message on failure
        "error_type": Optional[str] # "validation_error" vs "system_error"
    }
    ```
  - Integrates tool-result caching via Redis for deterministic queries.

---

## Spec 1.2: 4-Tier Memory Engine, PostgreSQL Schema & "Never Contact Again" Engine

### 1. The 4-Tier Stateful Memory Architecture

| Memory Tier | Storage Backend | Scope & Lifecycle | Purpose in System |
|---|---|---|---|
| **Session Memory** | Redis (Hash / Keys) | Run duration (TTL: 24h) | Stores active pipeline execution state, current startup queue, intermediate task tokens, and Master-to-Specialist handoff payloads. |
| **Cache Memory** | Redis (KV with TTL) | Configurable TTL (1h - 7d) | Caches deterministic tool outputs (e.g., YC company directory scrapes, Algolia responses) to save 100% of tokens on repeated requests. |
| **Business Memory** | PostgreSQL | Permanent relational | Core domain facts: discovered startups, verified founders, fit scores, generated drafts, sent messages, and the strict Blacklist. |
| **Explicit & Implicit Memory** | PostgreSQL | Permanent / Evolving | **Explicit**: Candidate ground truth facts, rules, and blacklist reasons.<br>**Implicit**: Extracted insights on founder responsiveness, startup hiring velocity, and channel effectiveness. |

---

### 2. Relational Database Schema (PostgreSQL / `db/`)

```sql
-- 1. Startups Table (Business Memory)
CREATE TABLE startups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    batch VARCHAR(64) NOT NULL,
    website VARCHAR(512),
    one_liner TEXT,
    long_description TEXT,
    team_size INT,
    industry VARCHAR(128),
    subindustry VARCHAR(128),
    tags JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(64) DEFAULT 'active',
    is_hiring BOOLEAN DEFAULT FALSE,
    yc_url VARCHAR(512),
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Founders Table (Business Memory)
CREATE TABLE founders (
    id SERIAL PRIMARY KEY,
    startup_id INT NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    full_name VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    bio TEXT,
    linkedin_url VARCHAR(512),
    twitter_url VARCHAR(512),
    has_email BOOLEAN DEFAULT FALSE,
    email VARCHAR(255),
    avatar_url VARCHAR(512),
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_startup_founder UNIQUE (startup_id, full_name)
);

-- 3. Fit Evaluations Table (Business Memory & Implicit Memory)
CREATE TABLE fit_evaluations (
    id SERIAL PRIMARY KEY,
    startup_id INT UNIQUE NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    score INT NOT NULL CHECK (score >= 0 AND score <= 100),
    fit_tier VARCHAR(16) NOT NULL, -- 'HIGH', 'MEDIUM', 'LOW'
    should_contact BOOLEAN NOT NULL DEFAULT FALSE,
    match_rationale JSONB NOT NULL, -- Array of specific match reasons
    contribution_angle TEXT,
    model_used VARCHAR(128),
    evaluated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 4. Message Drafts Table (Business Memory)
CREATE TABLE message_drafts (
    id SERIAL PRIMARY KEY,
    startup_id INT NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    founder_id INT NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
    linkedin_note VARCHAR(300), -- Under 300 characters
    yc_job_note TEXT,
    cold_email_subject VARCHAR(255),
    cold_email_body TEXT,
    model_used VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 5. Outreach Records Table (Stateful Human-in-the-Loop & Business Memory)
CREATE TABLE outreach_records (
    id SERIAL PRIMARY KEY,
    startup_id INT NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    founder_id INT NOT NULL REFERENCES founders(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'draft', -- 'draft', 'approved', 'sent', 'replied', 'rejected', 'blacklisted'
    active_channel VARCHAR(32) DEFAULT 'linkedin', -- 'linkedin', 'yc_job', 'email'
    selected_message TEXT,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_outreach_target UNIQUE (startup_id, founder_id)
);

-- 6. Blacklist / "Never Contact Again" Engine (Business & Explicit Memory)
CREATE TABLE blacklist (
    id SERIAL PRIMARY KEY,
    identifier_type VARCHAR(32) NOT NULL, -- 'founder_name', 'linkedin_url', 'company_name', 'domain'
    identifier_value VARCHAR(512) NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 7. Implicit Long-Term Insights Table (Implicit Memory)
CREATE TABLE implicit_insights (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(64) NOT NULL, -- 'startup', 'founder', 'industry'
    entity_key VARCHAR(255) NOT NULL,
    insight_key VARCHAR(128) NOT NULL,
    insight_value JSONB NOT NULL,
    confidence_score FLOAT DEFAULT 1.0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_entity_insight UNIQUE (entity_type, entity_key, insight_key)
);
```

---

### 3. Pydantic Runtime Contracts (`db/models.py`)
All internal data structures pass through strict Pydantic models with auto-generated schemas:
- `CandidateProfileModel`: Validates `profile.json` structure and nested sub-objects.
- `StartupModel` & `StartupCreateModel`: Input/output validation for company data.
- `FounderModel`: Input/output validation for founder information.
- `FitEvaluationModel`: Output contract for fit scoring (enforces integer score [0-100] and non-empty rationale).
- `MessageDraftsModel`: Enforces length constraints (e.g. `linkedin_note <= 300` chars).
- `OutreachRecordModel`: Validates state machine transitions.
- `BlacklistEntryModel`: Enforces non-empty identifier and reason.
- `MemoryItemModel`: Generic contract for session and implicit memory objects with ISO timestamps.

---

### 4. Persistence & Memory Manager (`db/storage.py` & `db/memory.py`)
- **`MemoryManager` (Redis interface)**:
  - `set_session_state(session_id, key, value, ttl=86400)`
  - `get_session_state(session_id, key)`
  - `cache_tool_result(tool_name, params_hash, result, ttl=3600)`
  - `get_cached_tool_result(tool_name, params_hash)`
  - `clear_session(session_id)`
- **`StorageEngine` (PostgreSQL / Relational interface)**:
  - `init_db()`: Initializes tables, indexes, and constraints.
  - `upsert_startup(startup)`: Deduplicates and stores startup entities.
  - `upsert_founder(founder)`: Deduplicates and links founders to startups.
  - `save_fit_evaluation(fit)`: Stores scored evaluations.
  - `save_message_drafts(drafts)`: Stores 3-channel drafts and initializes outreach records.
  - `is_blacklisted(founder_name, linkedin_url, company_name, website)`:
    - **Proactive Memory Check**: Instantly queries the `blacklist` table against all 4 identifiers.
    - If a match is found, immediately short-circuits the pipeline for that target with zero tokens spent.
  - `add_to_blacklist(identifier_type, identifier_value, reason)`:
    - Adds the entry to the blacklist table.
    - Cascades the status update to any matching active `outreach_records` (marking them as `blacklisted`).
  - `get_dashboard_stats()`: Fast aggregate counts for the UI.
  - `get_review_leads(status, tier)`: Multi-table JOIN fetching all contextual details needed for human review.

---

## Files to Create in Phase 1

```
config/
├── __init__.py
├── settings.py           # Central config + environment variables
├── llm_client.py         # OpenRouter OpenAI client + model tiering & fallback engine
└── profile.json          # Candidate explicit knowledge base

tools/
├── __init__.py
├── engine.py             # Tool dispatcher & execution engine
└── schemas/
    ├── __init__.py
    ├── discovery_tools.json  # Tool schemas for Phase 2
    ├── founder_tools.json    # Tool schemas for Phase 2
    ├── fit_tools.json        # Tool schemas for Phase 3
    └── outreach_tools.json   # Tool schemas for Phase 3

db/
├── __init__.py
├── connection.py         # PostgreSQL connection pool + Redis client (with SQLite local fallback)
├── models.py             # Pydantic schemas & runtime contracts
├── memory.py             # Session memory & Cache manager (Redis)
└── storage.py            # Business memory, DB operations & Blacklist engine (PostgreSQL)

tests/
├── __init__.py
├── test_config.py        # Config & profile validation test
├── test_fallback.py      # LLM model fallback test (mocked HTTP 429)
├── test_memory.py        # Session & cache memory test
└── test_storage.py       # DB schema, CRUD, and Blacklist test
```

---

## Dependencies (`requirements.txt`)
- `openai>=1.20.0` (Client for OpenRouter completions and function calling)
- `pydantic>=2.0.0` (Runtime contracts and validation)
- `python-dotenv>=1.0.0` (Environment variable loading)
- `psycopg2-binary>=2.9.0` (PostgreSQL adapter)
- `redis>=5.0.0` (Redis client for session & cache memory)
- `pytest>=7.0.0` (Automated verification)

---

## Verification Criteria & Tests (Spec 1.1 & 1.2)
1. **Config & Profile Verification**:
   - `test_config.py` passes: `settings.py` loads default values and `.env` cleanly; `profile.json` parses into `CandidateProfileModel` without validation errors.
2. **Model Fallback Verification (Mocked)**:
   - `test_fallback.py` passes: When the primary model in a tier triggers a simulated HTTP 429 or network timeout, the client automatically logs the incident, falls back to the secondary model, and returns the expected result. No live API credits consumed in tests.
3. **Memory Engine Verification**:
   - `test_memory.py` passes: Sets and gets session states in Redis; verifies tool call caching returns cached payload without re-executing.
4. **Relational Database & Blacklist Verification**:
   - `test_storage.py` passes:
     - `init_db()` provisions all tables and unique constraints cleanly.
     - Inserts a sample startup and founder.
     - Adding a founder or company to `blacklist` causes `is_blacklisted()` to return `True` for that entity.
     - Clean entities return `False`.
     - `get_dashboard_stats()` returns accurate counts.

---

## Next Step
Once you review and approve this reworked Phase 1 spec, we will proceed to execute **Spec 1.1 first**, test and verify it, and then implement **Spec 1.2**.
Waiting for your nod to begin building!
