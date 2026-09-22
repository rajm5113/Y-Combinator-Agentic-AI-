"""Founder intel tool fulfillment handlers connecting schemas to FounderAgent."""

from typing import Any, Dict, Optional

from agents.founder_agent import FounderAgent, normalize_linkedin_url
from db.storage import storage_engine
from tools.engine import ToolExecutionResult


async def handle_extract_company_profile(
    company_slug: str,
    agent: Optional[FounderAgent] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for extract_company_profile tool.

    Extracts detailed company metadata, founder profiles, and active jobs.
    Returns standardized ToolExecutionResult contract.
    """
    if not company_slug or not company_slug.strip():
        return ToolExecutionResult.fail(
            "Parameter 'company_slug' must be a non-empty string",
            error_type="validation_error",
        )

    clean_slug = company_slug.strip()
    active_agent = agent or FounderAgent()

    try:
        agent_res = await active_agent.execute({"startup_slugs": [clean_slug]})
        if not agent_res.success:
            return ToolExecutionResult.fail(
                "; ".join(agent_res.errors) or f"Extraction failed for '{clean_slug}'",
                error_type="upstream_error",
            )

        if agent_res.data:
            return ToolExecutionResult.ok(agent_res.data[0])

        return ToolExecutionResult.fail(
            f"No profile data found for '{clean_slug}'",
            error_type="not_found_error",
        )

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Failed extracting company profile for '{clean_slug}': {str(e)}",
            error_type="system_error",
        )


def handle_verify_founder_socials(
    founder_name: str,
    company_name: str,
    linkedin_url: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for verify_founder_socials tool.

    Normalizes LinkedIn URLs and checks against Business Memory Blacklist.
    Returns standardized ToolExecutionResult contract.
    """
    if not founder_name or not founder_name.strip():
        return ToolExecutionResult.fail(
            "Parameter 'founder_name' must be a non-empty string",
            error_type="validation_error",
        )
    if not company_name or not company_name.strip():
        return ToolExecutionResult.fail(
            "Parameter 'company_name' must be a non-empty string",
            error_type="validation_error",
        )

    clean_name = founder_name.strip()
    clean_company = company_name.strip()

    try:
        normalized_url = normalize_linkedin_url(linkedin_url)
        is_blocked = storage_engine.is_blacklisted(
            founder_name=clean_name,
            linkedin_url=normalized_url,
            company_name=clean_company,
        )

        status = "verified" if (normalized_url and not is_blocked) else (
            "blacklisted" if is_blocked else "unverified"
        )

        return ToolExecutionResult.ok({
            "founder_name": clean_name,
            "company_name": clean_company,
            "raw_linkedin_url": linkedin_url,
            "normalized_linkedin_url": normalized_url,
            "is_valid_linkedin": normalized_url is not None,
            "is_blacklisted": is_blocked,
            "verification_status": status,
        })

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Failed verifying socials for '{clean_name}': {str(e)}",
            error_type="system_error",
        )
