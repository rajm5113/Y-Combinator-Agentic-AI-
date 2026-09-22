#!/usr/bin/env python3
"""Command-Line Interface for the YC Founder Outreach Pipeline.

Subcommands:
    run         Execute the end-to-end outreach pipeline
    stats       Show aggregate database and conversion metrics
    blacklist   Manage the 'Never Contact Again' blacklist
    export      Export qualified leads and message drafts to CSV or JSON
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

# Reconfigure stdout/stderr to utf-8 if supported to prevent Windows charmap encoding crashes
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config.health import health_checker
from config.settings import settings
from db.backup import backup_engine
from db.storage import storage_engine
from pipeline import MasterOrchestrator, PipelineConfig, PipelineReport, PipelineStage

logger = logging.getLogger("outreach_cli")


def setup_logging(verbose: bool = False) -> None:
    """Configures console and file logging."""
    level = logging.DEBUG if verbose else logging.INFO
    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=log_fmt, stream=sys.stderr)


# ─── Subcommand: run ────────────────────────────────────────────────────────

def format_pipeline_report(report: PipelineReport) -> str:
    """Formats the completed PipelineReport into a human-readable summary,
    clearly separating current pipeline run stats from cumulative database totals."""
    lines = []
    bar = "=" * 64
    lines.append(bar)
    lines.append(" PIPELINE COMPLETE")
    status_label = "[OK] SUCCESS" if report.success else "[FAIL] FAILED"
    lines.append(f" Status        : {status_label}")
    lines.append(f" Session ID    : {report.session_id}")
    lines.append(f" Total Duration: {report.total_duration_seconds:.1f}s")
    lines.append("-" * 64)
    lines.append(" CURRENT PIPELINE RUN:")
    lines.append(f"   Discovered : {report.total_startups_discovered}")
    lines.append(f"   Founders   : {report.total_founders_extracted}")
    lines.append(f"   Evaluated  : {report.total_fit_evaluated}")
    lines.append(f"   Qualified  : {report.total_qualified}")
    lines.append(f"   Drafts     : {report.total_drafts_generated}")

    try:
        db_stats = storage_engine.get_pipeline_stats()
        db_qualified = db_stats.get("fit_high", 0) + db_stats.get("fit_medium", 0)
        lines.append("-" * 64)
        lines.append(" DATABASE TOTAL (HISTORICAL ACROSS ALL BATCHES):")
        lines.append(f"   Startups   : {db_stats.get('startups', 0)}")
        lines.append(f"   Founders   : {db_stats.get('founders', 0)}")
        lines.append(f"   Evaluated  : {db_stats.get('fit_evaluations', 0)}")
        lines.append(f"   Qualified  : {db_qualified}")
        lines.append(f"   Drafts     : {db_stats.get('message_drafts', 0)}")
    except Exception:
        pass

    total_errors = sum(len(s.errors) for s in report.stages.values())
    if total_errors > 0:
        lines.append("-" * 64)
        lines.append(f" Warnings/Errors: {total_errors} non-fatal issues encountered")
        for stage, res in report.stages.items():
            if res.errors:
                lines.append(f"   [{stage.value}] {len(res.errors)} error(s), e.g.: {res.errors[0]}")

    lines.append("-" * 64)
    lines.append(" Next steps:")
    lines.append("   * View database stats: python cli.py stats")
    lines.append("   * Export qualified leads: python cli.py export --min-score 50")
    lines.append(bar)
    return "\n".join(lines)


async def handle_run(args: argparse.Namespace) -> int:
    """Handles the 'run' subcommand."""
    setup_logging(args.verbose)

    # Parse skip stages
    skip_stages: List[PipelineStage] = []
    if args.skip:
        for s in args.skip:
            try:
                skip_stages.append(PipelineStage(s.lower()))
            except ValueError:
                print(f"[ERROR] Invalid stage to skip: '{s}'. Valid: {[e.value for e in PipelineStage]}")
                return 1

    batches = args.batch if args.batch else list(settings.default_batches)
    industries = args.industry if args.industry else []

    config = PipelineConfig(
        batches=batches,
        industries=industries,
        limit=args.limit,
        min_fit_score=args.min_score,
        max_concurrency=args.concurrency,
        skip_stages=skip_stages,
        force_refresh=args.force_refresh,
        dry_run=args.dry_run,
    )

    bar = "=" * 64
    print(bar)
    print(" PIPELINE: YC FOUNDER OUTREACH")
    print(f" Batches    : {', '.join(config.batches)}")
    print(f" Industries : {', '.join(config.industries) if config.industries else 'ALL'}")
    print(f" Min Score  : {config.min_fit_score} | Concurrency: {config.max_concurrency}")
    if config.limit:
        print(f" Limit      : {config.limit} startups")
    if config.dry_run:
        print(" Mode       : DRY RUN (Discovery only, no LLM calls)")
    if skip_stages:
        print(f" Skipping   : {[s.value for s in skip_stages]}")
    print(bar)
    print()

    def on_progress(event: str, payload: dict):
        if event == "stage_start":
            stage_name = payload.get("stage", "").upper()
            print(f">> Starting [{stage_name}] stage...")
        elif event == "stage_complete":
            stage_name = payload.get("stage", "").upper()
            success = payload.get("success", False)
            mark = "[OK]" if success else "[WARN]"
            print(f"{mark} Finished [{stage_name}] stage.")

    orchestrator = MasterOrchestrator(config=config, progress_callback=on_progress)
    report = await orchestrator.run()

    print()
    print(format_pipeline_report(report))
    return 0 if report.success else 1


# ─── Subcommand: stats ──────────────────────────────────────────────────────

def handle_stats(args: argparse.Namespace) -> int:
    """Displays formatted database overview and funnel conversion metrics."""
    stats = storage_engine.get_pipeline_stats()

    outreach = stats.get("outreach_status", {})
    draft_cnt = outreach.get("draft", 0)
    approved_cnt = outreach.get("approved", 0)
    sent_cnt = outreach.get("sent", 0)
    replied_cnt = outreach.get("replied", 0)
    rejected_cnt = outreach.get("rejected", 0)
    blacklisted_cnt = stats.get("blacklisted", 0)

    template = f"""+==============================================================+
|  YC FOUNDER OUTREACH -- DATABASE SUMMARY                     |
+==============================================================+
|  Startups Discovered  : {stats.get('startups', 0):<45}|
|  Founders Extracted   : {stats.get('founders', 0):<45}|
|  Fit Evaluations      : {stats.get('fit_evaluations', 0):<45}|
|    -> HIGH (>=75)     : {stats.get('fit_high', 0):<45}|
|    -> MEDIUM (50-74)  : {stats.get('fit_medium', 0):<45}|
|    -> LOW (<50)       : {stats.get('fit_low', 0):<45}|
|  Message Drafts       : {stats.get('message_drafts', 0):<45}|
|  Outreach Status:                                            |
|    -> draft           : {draft_cnt:<45}|
|    -> approved        : {approved_cnt:<45}|
|    -> sent            : {sent_cnt:<45}|
|    -> replied         : {replied_cnt:<45}|
|    -> rejected        : {rejected_cnt:<45}|
|  Blacklisted Entities : {blacklisted_cnt:<45}|
+==============================================================+"""
    print(template)
    return 0


# ─── Subcommand: blacklist ──────────────────────────────────────────────────

def handle_blacklist(args: argparse.Namespace) -> int:
    """Handles blacklist management subcommands (add, list, remove)."""
    action = args.blacklist_action

    if action == "add":
        if not args.type or not args.value or not args.reason:
            print("[ERROR] --type, --value, and --reason are all required to add to blacklist.")
            return 1
        valid_types = ["company_name", "founder_name", "linkedin_url", "domain"]
        if args.type not in valid_types:
            print(f"[ERROR] --type must be one of: {valid_types}")
            return 1

        entry_id = storage_engine.add_to_blacklist(
            identifier_type=args.type,
            identifier_value=args.value,
            reason=args.reason,
        )
        print(f"[OK] Successfully blacklisted [{args.type}] '{args.value}' (ID: {entry_id})")
        print(f"     Reason: {args.reason}")
        return 0

    elif action == "remove":
        if not args.value:
            print("[ERROR] --value is required to remove from blacklist.")
            return 1
        success = storage_engine.remove_from_blacklist(args.value)
        if success:
            print(f"[OK] Successfully removed '{args.value}' from blacklist.")
            return 0
        else:
            print(f"[WARN] Value '{args.value}' was not found in blacklist.")
            return 1

    elif action == "list":
        entries = storage_engine.get_blacklist_entries()
        if not entries:
            print("[INFO] Blacklist is currently empty.")
            return 0

        bar = "-" * 80
        print(bar)
        print(f"{'ID':<6} {'TYPE':<16} {'IDENTIFIER':<32} {'REASON':<24}")
        print(bar)
        for e in entries:
            eid = str(e.get("id", ""))
            itype = str(e.get("identifier_type", ""))
            ival = str(e.get("identifier_value", ""))[:30]
            reason = str(e.get("reason", ""))[:22]
            print(f"{eid:<6} {itype:<16} {ival:<32} {reason:<24}")
        print(bar)
        print(f"Total Blacklisted Entities: {len(entries)}")
        return 0

    else:
        print("[ERROR] Invalid blacklist action. Use: add, list, or remove")
        return 1


# ─── Subcommand: export ─────────────────────────────────────────────────────

EXPORT_COLUMNS = [
    "startup_name",
    "slug",
    "batch",
    "founder_name",
    "founder_title",
    "linkedin_url",
    "fit_score",
    "fit_tier",
    "contribution_angle",
    "linkedin_note",
    "yc_job_note",
    "cold_email_subject",
    "cold_email_body",
    "outreach_status",
]


def handle_export(args: argparse.Namespace) -> int:
    """Exports qualified leads and drafts to CSV or JSON."""
    min_score = args.min_score
    export_format = args.format.lower()
    output_path = Path(args.output) if args.output else Path(f"qualified_leads_score_{min_score}.{export_format}")

    leads = storage_engine.get_qualified_leads_for_export(min_score=min_score)

    if not leads:
        print(f"[WARN] No qualified leads found with fit score >= {min_score}.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "csv":
        with open(output_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for lead in leads:
                writer.writerow(lead)
    elif export_format == "json":
        with open(output_path, mode="w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, ensure_ascii=False)
    else:
        print(f"[ERROR] Unsupported export format: {export_format}. Use 'csv' or 'json'.")
        return 1

    print(f"[OK] Successfully exported {len(leads)} qualified leads from database total (score >= {min_score}) to:")
    print(f"     Path: {output_path.resolve()}")
    return 0


# ─── CLI Entrypoint & Parser ────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Constructs the root command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="YC Founder Outreach Pipeline & Business Memory CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Execute the outreach pipeline")
    run_parser.add_argument(
        "--batch", nargs="+",
        help="YC batch names to process (e.g., --batch 'Fall 2026' 'Summer 2026')",
    )
    run_parser.add_argument(
        "--industry", nargs="+",
        help="Industry tags to filter by (e.g., --industry 'AI/ML' 'Developer Tools')",
    )
    run_parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum startups to process per batch (default: all)",
    )
    run_parser.add_argument(
        "--min-score", type=int, default=settings.pipeline_default_min_score,
        help="Qualification gate threshold score (0-100, default: 50)",
    )
    run_parser.add_argument(
        "--concurrency", type=int, default=settings.pipeline_max_concurrency,
        help="Bounded parallelism limit for concurrent agent executions (default: 5)",
    )
    run_parser.add_argument(
        "--skip", nargs="+",
        help="Stages to skip (options: discovery, founder, fit, message)",
    )
    run_parser.add_argument(
        "--force-refresh", action="store_true",
        help="Bypass all caches and re-fetch/re-evaluate",
    )
    run_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run discovery stage only; no LLM calls made",
    )
    run_parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG level logging output",
    )

    # Subcommand: stats
    subparsers.add_parser("stats", help="Display summary metrics from the database")

    # Subcommand: blacklist
    bl_parser = subparsers.add_parser("blacklist", help="Manage never-contact blacklist")
    bl_subparsers = bl_parser.add_subparsers(dest="blacklist_action", help="Blacklist actions")

    bl_add = bl_subparsers.add_parser("add", help="Add an entity to the blacklist")
    bl_add.add_argument(
        "--type", required=True,
        choices=["company_name", "founder_name", "linkedin_url", "domain"],
        help="Type of entity identifier",
    )
    bl_add.add_argument("--value", required=True, help="Identifier value (e.g. company name or URL)")
    bl_add.add_argument("--reason", required=True, help="Reason for blacklisting")

    bl_subparsers.add_parser("list", help="List all blacklisted entities")

    bl_remove = bl_subparsers.add_parser("remove", help="Remove an entity from the blacklist")
    bl_remove.add_argument("--value", required=True, help="Identifier value to remove")

    # Subcommand: export
    export_parser = subparsers.add_parser("export", help="Export qualified leads to CSV or JSON")
    export_parser.add_argument(
        "--min-score", type=int, default=50,
        help="Minimum fit score threshold for exported leads (default: 50)",
    )
    export_parser.add_argument(
        "--format", choices=["csv", "json"], default="csv",
        help="Export file format (default: csv)",
    )
    export_parser.add_argument(
        "--output", default=None,
        help="Output file path (default: qualified_leads_score_{min_score}.{format})",
    )

    # Subcommand: dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Start the web-based outreach review dashboard")
    dash_parser.add_argument("--host", default=None, help="Host to bind (default: from settings or 127.0.0.1)")
    dash_parser.add_argument("--port", type=int, default=None, help="Port to bind (default: from settings or 8501)")
    dash_parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    dash_parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    # Subcommand: health (Phase 6)
    subparsers.add_parser("health", help="Run end-to-end system diagnostics (DB, Redis, OpenRouter)")

    # Subcommand: backup (Phase 6)
    subparsers.add_parser("backup", help="Create a compressed snapshot of the database")

    # Subcommand: restore (Phase 6)
    restore_p = subparsers.add_parser("restore", help="Restore database from a backup snapshot")
    restore_p.add_argument("filename", help="Backup filename to restore")

    # Subcommand: history (Phase 6)
    hist_p = subparsers.add_parser("history", help="Show recent pipeline execution history")
    hist_p.add_argument("--limit", type=int, default=10, help="Number of runs to show (default: 10)")

    return parser


def handle_dashboard(args: argparse.Namespace) -> int:
    """Launch the FastAPI dashboard server via uvicorn."""
    import webbrowser
    import uvicorn

    host = args.host or settings.dashboard_host
    port = args.port or settings.dashboard_port
    url = f"http://{host}:{port}"

    print(f"🚀 Starting YC Founder Outreach Dashboard at {url}")

    if not args.no_browser and settings.dashboard_auto_open_browser:
        def open_browser():
            import time
            time.sleep(1.0)
            webbrowser.open(url)
        import threading
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run("dashboard.server:app", host=host, port=port, log_level="info" if not args.verbose else "debug")
    return 0


# ─── Subcommands: Phase 6 Operations ───────────────────────────────────────

async def handle_health(args: argparse.Namespace) -> int:
    """Run diagnostics on database, redis, and API."""
    report = await health_checker.check_all()
    print("=" * 64)
    print(" SYSTEM HEALTH & DIAGNOSTICS")
    status_icon = "✓" if report["status"] == "healthy" else ("⚠" if report["status"] == "degraded" else "✗")
    print(f" Status: {status_icon} {report['status'].upper()}")
    print("-" * 64)
    for comp, data in report.get("components", {}).items():
        lat = f"({data['latency_ms']:.1f}ms)" if data.get("latency_ms") is not None else ""
        print(f" * {comp:<12}: {data['status'].upper():<10} {lat}")
        if data.get("error"):
            print(f"   Error: {data['error']}")
        elif data.get("details"):
            for k, v in data["details"].items():
                print(f"   - {k}: {v}")
    print("=" * 64)
    return 0 if report["status"] != "unhealthy" else 1


def handle_backup(args: argparse.Namespace) -> int:
    """Create database backup snapshot."""
    res = backup_engine.create_backup()
    if res.get("success"):
        print(f"✓ Backup created: {res['filename']} ({res['size_bytes'] / 1024:.1f} KB)")
        for tbl, cnt in res.get("table_counts", {}).items():
            print(f"  - {tbl}: {cnt} records")
        return 0
    else:
        print(f"✗ Backup failed: {res.get('error')}")
        return 1


def handle_restore(args: argparse.Namespace) -> int:
    """Restore database from backup snapshot."""
    res = backup_engine.restore_backup(args.filename)
    if res.get("success"):
        print(f"✓ Database restored from: {args.filename}")
        for tbl, cnt in res.get("restored_counts", {}).items():
            print(f"  - {tbl}: {cnt} records")
        return 0
    else:
        print(f"✗ Restore failed: {res.get('error')}")
        return 1


def handle_history(args: argparse.Namespace) -> int:
    """Display recent pipeline execution history."""
    history = storage_engine.get_pipeline_run_history(limit=args.limit)
    if not history:
        print("No pipeline execution history recorded.")
        return 0
    print("=" * 72)
    print(" RECENT PIPELINE RUN HISTORY")
    print("-" * 72)
    for r in history:
        stats = r.get("stats_json") or {}
        stat_str = f"Disc:{stats.get('discovered', 0)} Qual:{stats.get('qualified', 0)} Drafts:{stats.get('drafts', 0)}"
        print(f" [{r['status'].upper():<9}] {r['session_id']} | {r['batch']} | {r.get('duration_seconds', 0.0):.1f}s | {stat_str}")
        if r.get("error_summary"):
            print(f"   Error: {r['error_summary']}")
    print("=" * 72)
    return 0


def main() -> None:
    """CLI execution entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    if args.subcommand == "run":
        exit_code = asyncio.run(handle_run(args))
        sys.exit(exit_code)
    elif args.subcommand == "stats":
        sys.exit(handle_stats(args))
    elif args.subcommand == "blacklist":
        if not getattr(args, "blacklist_action", None):
            parser.parse_args(["blacklist", "--help"])
            sys.exit(0)
        sys.exit(handle_blacklist(args))
    elif args.subcommand == "export":
        sys.exit(handle_export(args))
    elif args.subcommand == "dashboard":
        sys.exit(handle_dashboard(args))
    elif args.subcommand == "health":
        sys.exit(asyncio.run(handle_health(args)))
    elif args.subcommand == "backup":
        sys.exit(handle_backup(args))
    elif args.subcommand == "restore":
        sys.exit(handle_restore(args))
    elif args.subcommand == "history":
        sys.exit(handle_history(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
