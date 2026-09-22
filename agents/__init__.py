"""Agent fleet package — autonomous specialist agents for the YC outreach pipeline."""

from agents.base import BaseAgent, AgentResult
from agents.discovery_agent import DiscoveryAgent
from agents.fit_agent import FitAgent
from agents.founder_agent import FounderAgent, extract_inertia_data, normalize_linkedin_url
from agents.http_client import ResilientHTTPClient, HTTPResult
from agents.message_agent import MessageAgent, enforce_linkedin_limit

__all__ = [
    "BaseAgent",
    "AgentResult",
    "DiscoveryAgent",
    "FounderAgent",
    "FitAgent",
    "MessageAgent",
    "MasterOrchestrator",
    "PipelineConfig",
    "PipelineReport",
    "PipelineStage",
    "StageResult",
    "extract_inertia_data",
    "normalize_linkedin_url",
    "enforce_linkedin_limit",
    "ResilientHTTPClient",
    "HTTPResult",
]


def __getattr__(name: str):
    if name in ("MasterOrchestrator", "PipelineConfig", "PipelineReport", "PipelineStage", "StageResult"):
        import pipeline
        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
