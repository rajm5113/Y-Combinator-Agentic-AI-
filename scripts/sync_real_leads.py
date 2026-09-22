import csv
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_db_connection
from db.models import StartupCreate, FounderCreate, FitEvaluation, MessageDrafts
from db.storage import StorageEngine

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

with get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute("DELETE FROM startups WHERE slug = 'lead-ai' OR name = 'Lead AI'")
    print("Cleaned Lead AI test fixture from PostgreSQL.")

with open("live_outreach_leads.csv", "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        s_name = row["startup_name"]
        if s_name == "Lead AI":
            continue

        slug = row["slug"]
        batch = row["batch"]
        founder_name = row["founder_name"]
        founder_title = row.get("founder_title")
        linkedin = row.get("linkedin_url")
        score = int(row["fit_score"])
        fit_tier = row["fit_tier"]
        angle = row.get("contribution_angle", "")
        lk_note = row.get("linkedin_note") or None
        yc_note = row.get("yc_job_note") or None
        em_subj = row.get("cold_email_subject") or None
        em_body = row.get("cold_email_body") or None

        # Upsert startup
        sid = StorageEngine.upsert_startup(StartupCreate(
            name=s_name,
            slug=slug,
            batch=batch,
            industry="B2B Software",
            is_hiring=True,
        ))

        # Upsert founder
        fid = StorageEngine.upsert_founder(FounderCreate(
            startup_id=sid,
            full_name=founder_name,
            title=founder_title,
            linkedin_url=linkedin,
        ))

        # Save fit evaluation
        StorageEngine.save_fit_evaluation(FitEvaluation(
            startup_id=sid,
            score=score,
            fit_tier=fit_tier,
            should_contact=True,
            contribution_angle=angle,
            match_rationale=[f"Verified match for {s_name} with {fit_tier} fit"],
        ))

        # Save message drafts if present
        if lk_note or yc_note or em_subj or em_body:
            StorageEngine.save_message_drafts(MessageDrafts(
                startup_id=sid,
                founder_id=fid,
                linkedin_note=lk_note,
                yc_job_note=yc_note,
                cold_email_subject=em_subj,
                cold_email_body=em_body,
            ))
        print(f"✓ Restored {s_name} - {founder_name} ({score} {fit_tier})")

print("All real live leads restored to PostgreSQL.")
