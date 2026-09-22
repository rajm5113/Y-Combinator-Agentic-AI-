"""Discovery tool fulfillment handlers connecting schemas to DiscoveryAgent."""

from typing import Any, Dict, List, Optional

from agents.discovery_agent import DiscoveryAgent
from tools.engine import ToolExecutionResult


async def handle_search_yc_directory(
    batch: str,
    industry: Optional[str] = None,
    is_hiring: Optional[bool] = None,
    limit: int = 20,
    agent: Optional[DiscoveryAgent] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for search_yc_directory tool.

    Searches Y Combinator Algolia directory by batch and industry filters.
    Returns standardized ToolExecutionResult contract.
    """
    if not batch or not batch.strip():
        return ToolExecutionResult.fail(
            "Parameter 'batch' must be a non-empty string",
            error_type="validation_error",
        )

    active_agent = agent or DiscoveryAgent()
    context = {
        "batches": [batch.strip()],
        "industries": [industry.strip()] if industry and industry.strip() else [],
        "limit": limit if limit and limit > 0 else 20,
    }

    try:
        agent_res = await active_agent.execute(context)
        if not agent_res.success:
            return ToolExecutionResult.fail(
                "; ".join(agent_res.errors) or "Discovery agent execution failed",
                error_type="upstream_error",
            )

        startups = agent_res.data or []
        # Post-filter on is_hiring if explicitly requested
        if is_hiring is not None:
            startups = [s for s in startups if bool(s.get("is_hiring")) == bool(is_hiring)]

        return ToolExecutionResult.ok(startups)

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Failed searching YC directory: {str(e)}",
            error_type="system_error",
        )


async def handle_fetch_batch_startups(
    batch: str,
    agent: Optional[DiscoveryAgent] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Fulfillment handler for fetch_batch_startups tool.

    Fetches full company roster for a specific batch identifier.
    Returns standardized ToolExecutionResult contract.
    """
    if not batch or not batch.strip():
        return ToolExecutionResult.fail(
            "Parameter 'batch' must be a non-empty string",
            error_type="validation_error",
        )

    active_agent = agent or DiscoveryAgent()
    context = {
        "batches": [batch.strip()],
        "industries": [],
        "limit": 1000,
    }

    try:
        agent_res = await active_agent.execute(context)
        if not agent_res.success:
            return ToolExecutionResult.fail(
                "; ".join(agent_res.errors) or "Batch fetch failed",
                error_type="upstream_error",
            )

        return ToolExecutionResult.ok(agent_res.data or [])

    except Exception as e:
        return ToolExecutionResult.fail(
            f"Failed fetching batch startups for '{batch}': {str(e)}",
            error_type="system_error",
        )
