"""Base agent protocol and standardized result contract.

Every specialist agent in the system inherits from BaseAgent and returns
AgentResult, enabling the Master Orchestrator to dispatch uniformly.
"""

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent_base")


@dataclass
class AgentResult:
    """Standardized return contract for every agent execution.

    Attributes:
        success: Whether the agent completed its primary objective.
        agent_name: Identifier of the agent that produced this result.
        data: Primary payload (list of startups, founders, etc.).
        errors: Non-fatal warnings or issues encountered during execution.
        stats: Execution metrics (e.g., {"discovered": 15, "deduplicated": 3}).
        duration_seconds: Wall-clock execution time in seconds.
    """

    success: bool
    agent_name: str
    data: Any = None
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def summary(self) -> str:
        """Human-readable one-line summary for logging."""
        status = "✅ SUCCESS" if self.success else "❌ FAILED"
        error_count = len(self.errors)
        return (
            f"[{self.agent_name}] {status} | "
            f"Duration: {self.duration_seconds:.2f}s | "
            f"Stats: {self.stats} | "
            f"Warnings: {error_count}"
        )


class BaseAgent(ABC):
    """Abstract base class for all specialist agents.

    Provides:
    - Uniform `run()` wrapper with timing, logging, and error containment.
    - Abstract `execute()` method that subclasses implement.
    - Standard metadata fields for orchestrator introspection.
    """

    agent_name: str = "base"
    description: str = "Abstract base agent"
    required_tools: List[str] = []

    async def run(self, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Public entry point. Wraps execute() with timing and error handling.

        Args:
            context: Pipeline context dict containing parameters, upstream data,
                     and session metadata.

        Returns:
            AgentResult with execution data, stats, and any errors.
        """
        if context is None:
            context = {}

        start_time = time.monotonic()
        logger.info(f"[{self.agent_name}] Starting execution with context keys: {list(context.keys())}")

        try:
            result = await self.execute(context)
            result.duration_seconds = time.monotonic() - start_time
            logger.info(result.summary())
            return result

        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(f"[{self.agent_name}] Unhandled exception after {duration:.2f}s: {e}", exc_info=True)
            return AgentResult(
                success=False,
                agent_name=self.agent_name,
                data=None,
                errors=[f"Unhandled exception: {str(e)}"],
                stats={},
                duration_seconds=duration,
            )

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> AgentResult:
        """Core agent logic. Subclasses must implement this.

        Args:
            context: Pipeline context with parameters and upstream data.

        Returns:
            AgentResult with the agent's output payload.
        """
        ...
