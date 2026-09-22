#!/usr/bin/env python3
"""Unified One-Click Startup Launcher for YC Founder Outreach System.

Performs pre-flight environment checks (PostgreSQL, Redis, OpenRouter API keys),
ensures schema migrations are current, launches the FastAPI dashboard server,
and opens the operator's default web browser.
"""

import asyncio
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Fix Windows console utf-8 output
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

import uvicorn
from config.health import health_checker
from config.settings import settings
from db.storage import storage_engine


BANNER = r"""
================================================================================
   __   __ _____   ___  _   _ _____ ____  _____    _    ____ _   _ 
   \ \ / // ____| / _ \| | | |_   _|  _ \| ____|  / \  / ___| | | |
    \ V /| |     | | | | | | | | | | |_) |  _|   / _ \| |   | |_| |
     | | | |___  | |_| | |_| | | | |  _ <| |___ / ___ \ |___|  _  |
     |_|  \_____| \___/ \___/  |_| |_| \_\_____/_/   \_\____|_| |_|
                       FOUNDER OUTREACH AGENT
================================================================================
"""


async def run_preflight() -> bool:
    """Performs pre-flight health check across all required infrastructure."""
    print("Running system pre-flight diagnostics...")
    report = await health_checker.check_all()

    all_good = True
    for comp, data in report.get("components", {}).items():
        status = data.get("status", "unknown").upper()
        lat = f"({data['latency_ms']:.1f}ms)" if data.get("latency_ms") is not None else ""
        icon = "✓" if status == "HEALTHY" else ("⚠" if status == "DEGRADED" else "✗")
        print(f"  [{icon}] {comp:<14}: {status:<10} {lat}")
        if data.get("error"):
            print(f"      Issue: {data['error']}")
            if comp in ("database", "redis"):
                all_good = False

    if not all_good:
        print("\n[WARNING] Some core services are not responding properly.")
        print("          If using Docker, run: docker compose up -d\n")

    return all_good


def open_browser_delayed(url: str, delay: float = 1.5):
    """Opens browser after server has started listening."""
    def _open():
        time.sleep(delay)
        print(f"[Launcher] Opening browser: {url}")
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def main():
    print(BANNER)

    # 1. Initialize DB schema
    print("[1/3] Verifying and applying database schema migrations...")
    try:
        storage_engine.init_db()
        print("      Schema verified.")
    except Exception as e:
        print(f"      [ERROR] Database initialization failed: {e}")

    # 2. Run diagnostics
    print("[2/3] Checking service connectivity...")
    asyncio.run(run_preflight())

    # 3. Launch dashboard
    host = settings.dashboard_host
    port = settings.dashboard_port
    url = f"http://{host}:{port}"

    print(f"[3/3] Launching Dashboard on {url} (Ctrl+C to quit)\n")

    if settings.dashboard_auto_open_browser:
        open_browser_delayed(url)

    uvicorn.run(
        "dashboard.server:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
