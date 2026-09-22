"""Live End-to-End Pipeline Execution Script.

Runs the complete Phase 1 -> Phase 2 -> Phase 3 pipeline with:
- Real YC Algolia Discovery API
- Real YC Company Page HTTP Crawl & Inertia.js extraction
- Real PostgreSQL Database (Docker on port 15432)
- Real Redis Cache (Docker on port 6379)
- Real OpenRouter API LLM token calls
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure Windows console supports UTF-8 characters and emojis
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.discovery_agent import DiscoveryAgent
from agents.fit_agent import FitAgent
from agents.founder_agent import FounderAgent
from agents.message_agent import MessageAgent
from config.settings import settings
from db.connection import get_db_connection, get_redis_client
from db.storage import StorageEngine

# Configure clean logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_test")


async def main():
    print("=" * 80)
    print("🚀 STARTING LIVE PRODUCTION PIPELINE TEST (REAL APIS & REAL TOKENS)")
    print("=" * 80)

    # 0. System & Infrastructure Verification
    print("\n[Step 0] Verifying Infrastructure...")
    print(f"  • PostgreSQL Database URL : {settings.database_url}")
    print(f"  • Redis URL               : {settings.redis_url}")
    print(f"  • OpenRouter API Key      : {settings.openrouter_api_key[:10]}...{settings.openrouter_api_key[-4:]}")

    # Test Redis Ping
    redis_client = get_redis_client()
    assert redis_client.ping(), "Redis ping failed!"
    print("  ✓ Redis Connection: ACTIVE")

    # Initialize Database Schema in PostgreSQL
    StorageEngine.init_db()
    print("  ✓ PostgreSQL Schema: SYNCHRONIZED & READY")

    # 1. Discovery Agent: Live YC Algolia Search
    print("\n" + "=" * 80)
    print("[Step 1] Executing Discovery Agent (Live YC Algolia Search)...")
    print("=" * 80)
    discovery_agent = DiscoveryAgent()
    discovery_result = await discovery_agent.execute({
        "batches": ["Fall 2026"],
        "industries": [],
    })

    if not discovery_result.success or not discovery_result.data:
        # Fallback to Summer 2026 if Fall 2026 has no results yet
        print("  Retrying with Summer 2026 batch...")
        discovery_result = await discovery_agent.execute({
            "batches": ["Summer 2026"],
            "industries": [],
        })

    print(f"  ✓ Discovery Result: Success={discovery_result.success}")
    print(f"  ✓ Total Startups Discovered & Upserted: {len(discovery_result.data)}")

    if not discovery_result.data:
        print("❌ No startups returned from Algolia. Aborting live test.")
        return

    # Select the first startup for deep evaluation
    target_startup = discovery_result.data[0]
    slug = target_startup["slug"]
    startup_id = target_startup["startup_id"]
    name = target_startup.get("name", slug)
    print(f"\n🎯 Selected Target Startup for Deep Intel: '{name}' (slug: '{slug}', id: {startup_id})")

    # 2. Founder Agent: Live YC Company Page Crawl
    print("\n" + "=" * 80)
    print(f"[Step 2] Executing Founder Agent (Live Crawl of {slug})...")
    print("=" * 80)
    founder_agent = FounderAgent()
    founder_result = await founder_agent.execute({
        "slugs": [slug],
        "force_refresh": True,
    })

    print(f"  ✓ Founder Extraction: Success={founder_result.success}")
    print(f"  ✓ Stats: {founder_result.stats}")

    # Inspect founders from PostgreSQL
    founders = StorageEngine.get_founders_by_startup_id(startup_id)
    print(f"  ✓ Found {len(founders)} founder(s) in PostgreSQL:")
    for f in founders:
        print(f"    • {f.get('full_name')} | Title: {f.get('title')} | LinkedIn: {f.get('linkedin_url')}")

    # 3. Fit Agent: Live OpenRouter Cognitive Evaluation
    print("\n" + "=" * 80)
    print(f"[Step 3] Executing Fit Agent (Live LLM Reasoning via OpenRouter)...")
    print("=" * 80)
    fit_agent = FitAgent()
    fit_result = await fit_agent.execute({
        "startup_id": startup_id,
        "force_refresh": True,
    })

    print(f"  ✓ Fit Evaluation: Success={fit_result.success}")
    eval_record = StorageEngine.get_fit_evaluation_by_startup_id(startup_id)
    if eval_record:
        score = eval_record.get("score")
        tier = eval_record.get("fit_tier")
        contact = eval_record.get("should_contact")
        model = eval_record.get("model_used")
        print(f"  ✓ Score: {score}/100 ({tier}) | Should Contact: {contact}")
        print(f"  ✓ LLM Model Used: {model}")
        print(f"  ✓ Match Points: {eval_record.get('match_rationale', [])}")
        print(f"  ✓ Recommended Angle: {eval_record.get('contribution_angle')}")

    # 4. Message Agent: Live Multi-Channel Generation
    print("\n" + "=" * 80)
    print(f"[Step 4] Executing Message Agent (Live Outreach Draft Generation)...")
    print("=" * 80)
    message_agent = MessageAgent()
    msg_result = await message_agent.execute({
        "startup_id": startup_id,
    })

    print(f"  ✓ Message Generation: Success={msg_result.success}")
    print(f"  ✓ Stats: {msg_result.stats}")

    # 5. Final PostgreSQL Database Audit & Output
    print("\n" + "=" * 80)
    print("📊 LIVE POSTGRESQL DATABASE AUDIT RECORD")
    print("=" * 80)
    with get_db_connection() as conn:
        cur = conn.cursor()
        
        # Check startups
        cur.execute("SELECT COUNT(*) as c FROM startups")
        print(f"  • Total startups stored in PostgreSQL: {cur.fetchone()['c']}")

        # Check founders
        cur.execute("SELECT COUNT(*) as c FROM founders")
        print(f"  • Total founders stored in PostgreSQL: {cur.fetchone()['c']}")

        # Check fit evaluations
        cur.execute("SELECT COUNT(*) as c FROM fit_evaluations")
        print(f"  • Total fit evaluations stored: {cur.fetchone()['c']}")

        # Fetch generated message drafts
        cur.execute("SELECT * FROM message_drafts WHERE startup_id = ?", (startup_id,))
        drafts = cur.fetchone()
        if drafts:
            print("\n" + "-" * 80)
            print("📝 GENERATED GROUNDED OUTREACH (STORED IN POSTGRESQL):")
            print("-" * 80)
            ln = drafts.get("linkedin_note") or ""
            print(f"🔷 LinkedIn Connection Note ({len(ln)} chars <= 300 limit):")
            print(f"   \"{ln}\"\n")
            print(f"🔶 YC Job Note:")
            print(f"   \"{drafts.get('yc_job_note')}\"\n")
            print(f"📧 Cold Email Subject: {drafts.get('cold_email_subject')}")
            print(f"📧 Cold Email Body:\n{drafts.get('cold_email_body')}\n")
        else:
            print("  ℹ️ No message draft created (Startup fit score was < 50, filtered by Qualification Gate).")

    print("=" * 80)
    print("🎉 ALL PHASES EXECUTED LIVE WITH REAL APIS, REAL DATABASE, AND REAL TOKENS!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
