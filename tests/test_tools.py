"""Tests for the Decoupled Tool Engine and schema contracts."""

from tools.engine import ToolEngine, ToolExecutionResult


def test_tool_engine_schema_loading():
    """Verify tool schemas are cleanly loaded from JSON files."""
    engine = ToolEngine()
    discovery_tools = engine.load_agent_tools("discovery")

    assert len(discovery_tools) >= 2
    tool_names = [t["function"]["name"] for t in discovery_tools]
    assert "search_yc_directory" in tool_names
    assert "fetch_batch_startups" in tool_names


def test_tool_engine_successful_execution():
    """Verify tool execution returns standardized success contract."""
    engine = ToolEngine()

    def mock_search_tool(batch: str, limit: int = 10):
        return [{"name": "Startup Alpha", "batch": batch}]

    engine.register_handler("search_yc_directory", mock_search_tool)
    res = engine.execute("search_yc_directory", {"batch": "Fall 2026", "limit": 5})

    assert res["success"] is True
    assert res["error"] is None
    assert len(res["result"]) == 1
    assert res["result"][0]["name"] == "Startup Alpha"


def test_tool_engine_invalid_args_validation():
    """Verify bad arguments return validation_error without raising an exception."""
    engine = ToolEngine()

    def strict_handler(required_param: str):
        return required_param

    engine.register_handler("strict_tool", strict_handler)
    res = engine.execute("strict_tool", {"unexpected_param": "value"})

    assert res["success"] is False
    assert res["error_type"] == "validation_error"
    assert "Invalid arguments" in res["error"]


def test_tool_engine_unregistered_tool():
    """Verify calling an unregistered tool returns not_found_error."""
    engine = ToolEngine()
    res = engine.execute("non_existent_tool", {})

    assert res["success"] is False
    assert res["error_type"] == "not_found_error"
