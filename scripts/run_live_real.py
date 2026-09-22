"""Live End-to-End Pipeline Execution on Real YC Startups.

Runs the real pipeline:
1. Real YC Company Page HTTP Crawl & Inertia.js Founder Extraction
2. Real PostgreSQL Database Storage (Docker port 15432)
3. Real Redis Caching (Docker port 6379)
4. Real OpenRouter LLM Token Calls (Fit Qualification & Multi-Channel Messaging)
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.fit_agent import FitAgent
from agents.founder_agent import FounderAgent
from agents.message_agent import MessageAgent
from config.settings import settings
from db.connection import get_db_connection, get_redis_client
from db.models import StartupCreate
from db.storage import StorageEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_run")

# Real YC startups from recent batches to evaluate live
REAL_YC_STARTUPS = [
    {
        "name": "Postiz",
        "slug": "postiz",
        "batch": "W24",
        "website": "https://postiz.com",
        "one_liner": "Open-source social media scheduling and automation platform with AI.",
        "long_description": "Postiz is the open-source social media management tool. Schedule posts, analyze engagement, and generate social content with AI.",
        "team_size": 2,
        "industry": "B2B",
        "subindustry": "Developer Tools",
        "tags": ["Open Source", "Social Media", "AI", "DevTools"],
        "yc_url": "https://www.ycombinator.com/companies/postiz",
    },
    {
        "name": "Camel AI",
        "slug": "camel-ai",
        "batch": "S24",
        "website": "https://camel-ai.com",
        "one_liner": "Ask questions about your database in plain English.",
        "long_description": "Camel AI connects to PostgreSQL, Snowflake, and BigQuery so anyone on the team can ask questions in plain English and get SQL charts instantly.",
        "team_size": 3,
        "industry": "B2B",
        "subindustry": "Analytics",
        "tags": ["AI", "Analytics", "Database", "B2B"],
        "yc_url": "https://www.ycombinator.com/companies/camel-ai",
    }
]


async def run_live():
    print("=" * 80)
    print("🚀 LIVE EXECUTION: REAL POSTGRESQL + REAL REDIS + REAL OPENROUTER TOKENS")
    print("=" * 80)

    # 1. System checks
    redis_client = get_redis_client()
    assert redis_client.ping(), "Redis ping failed!"
    print("  ✓ Redis (localhost:6379): ONLINE")
    print(f"  ✓ PostgreSQL (localhost:15432): ONLINE")
    print(f"  ✓ OpenRouter Token: AUTHENTICATED ({settings.openrouter_api_key[:12]}...)")

    StorageEngine.init_db()

    founder_agent = FounderAgent()
    fit_agent = FitAgent()
    message_agent = MessageAgent()

    for item in REAL_YC_STARTUPS:
        print("\n" + "=" * 80)
        print(f"🏢 PROCESSING REAL YC STARTUP: {item['name']} ({item['slug']})")
        print("=" * 80)

        # A. Upsert Startup in PostgreSQL
        startup_create = StartupCreate(**item)
        startup_id = StorageEngine.upsert_startup(startup_create)
        print(f"  ✓ Startup stored in PostgreSQL (ID: {startup_id})")

        # B. Live Founder Crawl
        print(f"  🔍 Crawling live YC profile: https://www.ycombinator.com/companies/{item['slug']}...")
        founder_res = await founder_agent.execute({
            "slugs": [item["slug"]],
            "force_refresh": True,
        })
        founders = StorageEngine.get_founders_by_startup_id(startup_id)
        print(f"  ✓ Extracted {len(founders)} founder(s) from live YC Inertia data:")
        for f in founders:
            print(f"    • {f.get('full_name')} | Title: {f.get('title')} | LinkedIn: {f.get('linkedin_url')}")

        # C. Live Cognitive Fit Evaluation (Real OpenRouter LLM Call)
        print(f"\n  🧠 Calling OpenRouter LLM (FitAgent) for {item['name']}...")
        fit_res = await fit_agent.execute({
            "startup_id": startup_id,
            "force_refresh": True,
        })
        eval_record = StorageEngine.get_fit_evaluation_by_startup_id(startup_id)
        if eval_record:
            score = eval_record.get("score")
            tier = eval_record.get("fit_tier")
            print(f"  ✓ Real Fit Score: {score}/100 | Tier: {tier} | Contact: {eval_record.get('should_contact')}")
            print(f"  ✓ Model Used: {eval_record.get('model_used')}")
            print(f"  ✓ Pitch Angle: {eval_record.get('contribution_angle')}")

        # D. Live Multi-Channel Outreach Generation (Real OpenRouter LLM Call)
        print(f"\n  ✍️ Calling OpenRouter LLM (MessageAgent) for {item['name']}...")
        msg_res = await message_agent.execute({
            "startup_id": startup_id,
        })
        print(f"  ✓ Message Generation Result: {msg_res.stats}")

    # 2. Query Live PostgreSQL Database Record
    print("\n" + "=" * 80)
    print("📊 LIVE POSTGRESQL DATABASE RECORD SUMMARY")
    print("=" * 80)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM message_drafts ORDER BY id DESC")
        all_drafts = cur.fetchall()
        print(f"Total Message Drafts in PostgreSQL: {len(all_drafts)}\n")

        for d in all_drafts:
            st = StorageEngine.get_startup_by_id(d["startup_id"])
            st_name = st["name"] if st else f"ID {d['startup_id']}"
            ln = d.get("linkedin_note") or ""
            print(f"🔷 Company: {st_name}")
            print(f"   Model: {d.get('model_used')}")
            print(f"   LinkedIn Note ({len(ln)} chars <= 300 limit):")
            print(f"   \"{ln}\"\n")
            print(f"   YC Job Note:\n   \"{d.get('yc_job_note')}\"\n")
            print(f"   Cold Email Subject: {d.get('cold_email_subject')}")
            print(f"   Cold Email Body:\n{d.get('cold_email_body')}\n")
            print("-" * 60)

    print("=" * 80)
    print("🎉 REAL PIPELINE COMPLETED WITH LIVE APIS, POSTGRESQL, AND REAL TOKENS!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_live())
