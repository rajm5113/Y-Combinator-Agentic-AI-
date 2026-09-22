"""Fit qualification tool fulfillment handlers connecting schemas to FitAgent."""

import re
from typing import Any, Dict, List, Optional

from agents.fit_agent import FitAgent
from db.models import StartupCreate
from db.storage import storage_engine
from tools.engine import ToolExecutionResult


def slugify(name: str) -> str:
    """Generates a URL-safe slug from a company name."""
    s = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[-\s]+", "-", s) or "startup"


async def handle_evaluate_startup_fit(
    company_name: str,
    one_liner: str,
    description: Optional[str] = None,
    industries: Optional[List[str]] = None,
    founder_backgrounds: Optional[List[str]] = None,
    agent: Optional[FitAgent] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for evaluate_startup_fit tool.

    Compares candidate profile against a startup to generate an objective 0-100 fit score.
    Returns standardized ToolExecutionResult contract.
    """
    if not company_name or not company_name.strip():
        return ToolExecutionResult.fail(
            "Parameter 'company_name' must be a non-empty string",
            error_type="validation_error",
        )
    if not one_liner or not one_liner.strip():
        return ToolExecutionResult.fail(
            "Parameter 'one_liner' must be a non-empty string",
            error_type="validation_error",
        )

    clean_name = company_name.strip()
    clean_one_liner = one_liner.strip()
    slug = slugify(clean_name)

    # Ensure startup exists in storage to get an ID
    existing = storage_engine.get_startup_by_slug(slug)
    if existing:
        startup_id = existing["id"]
    else:
        startup_id = storage_engine.upsert_startup(StartupCreate(
            name=clean_name,
            slug=slug,
            batch="Current",
            one_liner=clean_one_liner,
            long_description=description,
            tags=industries or [],
            industry=(industries[0] if industries else None),
        ))

    active_agent = agent or FitAgent()

    try:
        fit_eval, error_msg, _, is_blacklisted = await active_agent.evaluate_startup(startup_id)
        if is_blacklisted:
            return ToolExecutionResult.fail(
                f"Startup '{clean_name}' is blacklisted from outreach",
                error_type="blacklisted_error",
            )
        if error_msg:
            return ToolExecutionResult.fail(
                error_msg,
                error_type="upstream_error",
            )
        if fit_eval:
            return ToolExecutionResult.ok(fit_eval.model_dump())

        return ToolExecutionResult.fail(
            f"Failed to evaluate fit for '{clean_name}'",
            error_type="system_error",
        )

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Error executing fit evaluation for '{clean_name}': {str(e)}",
            error_type="system_error",
        )
