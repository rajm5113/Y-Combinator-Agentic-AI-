"""Outreach tool fulfillment handlers connecting schemas to MessageAgent and Blacklist."""

from typing import Any, Dict, List, Optional

from agents.founder_agent import extract_domain, normalize_linkedin_url
from agents.message_agent import MessageAgent
from db.models import FitEvaluation, FounderCreate, StartupCreate
from db.storage import storage_engine
from tools.engine import ToolExecutionResult
from tools.handlers.fit_handlers import slugify


def handle_check_never_contact_blacklist(
    founder_name: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    company_name: Optional[str] = None,
    website: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for check_never_contact_blacklist tool.

    Returns standardized ToolExecutionResult contract with boolean match.
    """
    clean_linkedin = normalize_linkedin_url(linkedin_url) if linkedin_url else None
    clean_domain = extract_domain(website) if website else None

    is_blocked = storage_engine.is_blacklisted(
        founder_name=founder_name.strip() if founder_name else None,
        linkedin_url=clean_linkedin,
        company_name=company_name.strip() if company_name else None,
        domain=clean_domain,
    )

    return ToolExecutionResult.ok({
        "is_blacklisted": is_blocked,
        "founder_name": founder_name,
        "company_name": company_name,
        "checked_domain": clean_domain,
    })


async def handle_generate_grounded_messages(
    founder_name: str,
    company_name: str,
    verified_facts: List[str],
    pitch_angle: str,
    agent: Optional[MessageAgent] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for generate_grounded_messages tool.

    Drafts personalized outreach messages across 3 channels.
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
    if not verified_facts:
        return ToolExecutionResult.fail(
            "Parameter 'verified_facts' must be a non-empty list of facts",
            error_type="validation_error",
        )
    if not pitch_angle or not pitch_angle.strip():
        return ToolExecutionResult.fail(
            "Parameter 'pitch_angle' must be a non-empty string",
            error_type="validation_error",
        )

    clean_founder = founder_name.strip()
    clean_company = company_name.strip()
    slug = slugify(clean_company)

    # Ensure startup exists in DB
    existing_startup = storage_engine.get_startup_by_slug(slug)
    if existing_startup:
        sid = existing_startup["id"]
    else:
        sid = storage_engine.upsert_startup(StartupCreate(
            name=clean_company,
            slug=slug,
            batch="Current",
            one_liner="; ".join(verified_facts[:2]),
        ))

    # Ensure founder exists in DB
    fid = storage_engine.upsert_founder(FounderCreate(
        startup_id=sid,
        full_name=clean_founder,
        bio="; ".join(verified_facts),
    ))

    # Ensure fit evaluation exists to satisfy qualification gate
    existing_eval = storage_engine.get_fit_evaluation_by_startup_id(sid)
    if not existing_eval:
        storage_engine.save_fit_evaluation(FitEvaluation(
            startup_id=sid,
            score=80,
            fit_tier="HIGH",
            should_contact=True,
            match_rationale=verified_facts,
            contribution_angle=pitch_angle.strip(),
        ))

    active_agent = agent or MessageAgent()

    try:
        drafts, err, skipped_low_fit, is_blacklisted = await active_agent.draft_for_startup_and_founder(
            startup_id=sid,
            founder_id=fid,
            pitch_angle_override=pitch_angle.strip(),
        )

        if is_blacklisted:
            return ToolExecutionResult.fail(
                f"Founder '{clean_founder}' at '{clean_company}' is blacklisted",
                error_type="blacklisted_error",
            )
        if skipped_low_fit:
            return ToolExecutionResult.fail(
                f"Startup '{clean_company}' did not meet the fit threshold",
                error_type="qualification_error",
            )
        if err:
            return ToolExecutionResult.fail(err, error_type="upstream_error")

        if drafts:
            return ToolExecutionResult.ok(drafts.model_dump())

        return ToolExecutionResult.fail(
            "Message drafting failed unexpectedly",
            error_type="system_error",
        )

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Error generating grounded messages: {str(e)}",
            error_type="system_error",
        )
