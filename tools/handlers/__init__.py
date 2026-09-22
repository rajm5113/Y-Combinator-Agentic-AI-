"""Tool fulfillment handlers package with automatic schema registration for Phase 2 and Phase 3."""

from typing import Optional

from tools.engine import ToolEngine, tool_engine
from tools.handlers.discovery_handlers import (
    handle_fetch_batch_startups,
    handle_search_yc_directory,
)
from tools.handlers.fit_handlers import (
    handle_evaluate_startup_fit,
)
from tools.handlers.founder_handlers import (
    handle_extract_company_profile,
    handle_verify_founder_socials,
)
from tools.handlers.outreach_handlers import (
    handle_check_never_contact_blacklist,
    handle_generate_grounded_messages,
)


def register_all_handlers(engine: Optional[ToolEngine] = None):
    """Registers all Phase 2 and Phase 3 tool handlers and loads their JSON schemas."""
    target_engine = engine or tool_engine

    # Load agent tool schemas
    target_engine.load_agent_tools("discovery")
    target_engine.load_agent_tools("founder")
    target_engine.load_agent_tools("fit")
    target_engine.load_agent_tools("outreach")

    # Register Phase 2 handlers
    target_engine.register_handler("search_yc_directory", handle_search_yc_directory)
    target_engine.register_handler("fetch_batch_startups", handle_fetch_batch_startups)
    target_engine.register_handler("extract_company_profile", handle_extract_company_profile)
    target_engine.register_handler("verify_founder_socials", handle_verify_founder_socials)

    # Register Phase 3 handlers
    target_engine.register_handler("evaluate_startup_fit", handle_evaluate_startup_fit)
    target_engine.register_handler("check_never_contact_blacklist", handle_check_never_contact_blacklist)
    target_engine.register_handler("generate_grounded_messages", handle_generate_grounded_messages)


# Auto-register handlers with the global singleton tool_engine
register_all_handlers(tool_engine)

__all__ = [
    "handle_search_yc_directory",
    "handle_fetch_batch_startups",
    "handle_extract_company_profile",
    "handle_verify_founder_socials",
    "handle_evaluate_startup_fit",
    "handle_check_never_contact_blacklist",
    "handle_generate_grounded_messages",
    "register_all_handlers",
]
