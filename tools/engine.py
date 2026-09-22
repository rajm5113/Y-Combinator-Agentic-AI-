"""Tool dispatcher and execution engine following the Decoupled Tool Contract.

Validates inputs, executes sync and async fulfillment handlers, and returns standardized:
{
    "success": bool,
    "result": Any,
    "error": Optional[str],
    "error_type": Optional[str]
}
"""

import asyncio
import concurrent.futures
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("tool_engine")

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


class ToolExecutionResult:
    @staticmethod
    def ok(result: Any) -> Dict[str, Any]:
        return {
            "success": True,
            "result": result,
            "error": None,
            "error_type": None,
        }

    @staticmethod
    def fail(error: str, error_type: str = "system_error") -> Dict[str, Any]:
        return {
            "success": False,
            "result": None,
            "error": str(error),
            "error_type": error_type,
        }


class ToolEngine:
    """Registry and dispatcher for AI Agent tools with sync and async fulfillment."""

    def __init__(self):
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._schemas: Dict[str, Dict[str, Any]] = {}

    def register_handler(self, tool_name: str, handler: Callable[..., Any]):
        """Registers a Python function as the fulfillment handler for a tool."""
        self._handlers[tool_name] = handler

    def load_agent_tools(self, agent_name: str) -> List[Dict[str, Any]]:
        """Loads tool definitions from tools/schemas/{agent_name}_tools.json."""
        schema_file = SCHEMA_DIR / f"{agent_name}_tools.json"
        if not schema_file.exists():
            logger.warning(f"Schema file not found for agent: {agent_name} at {schema_file}")
            return []
        try:
            with open(schema_file, "r", encoding="utf-8") as f:
                tools = json.load(f)
                for tool in tools:
                    func_name = tool.get("function", {}).get("name")
                    if func_name:
                        self._schemas[func_name] = tool
                return tools
        except Exception as e:
            logger.error(f"Failed to load schemas from {schema_file}: {e}")
            return []

    def get_tool_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Returns the schema for a specific tool."""
        return self._schemas.get(tool_name)

    async def execute_async(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Asynchronously executes a tool handler with defensive error handling."""
        if arguments is None:
            arguments = {}

        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolExecutionResult.fail(
                f"No handler registered for tool '{tool_name}'",
                error_type="not_found_error",
            )

        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                result = handler(**arguments)

            if isinstance(result, dict) and "success" in result and "error_type" in result:
                return result
            return ToolExecutionResult.ok(result)

        except TypeError as e:
            logger.warning(f"Validation error calling {tool_name} with {arguments}: {e}")
            return ToolExecutionResult.fail(
                f"Invalid arguments for tool '{tool_name}': {e}",
                error_type="validation_error",
            )
        except Exception as e:
            logger.error(f"System error executing tool {tool_name}: {e}", exc_info=True)
            return ToolExecutionResult.fail(
                f"Error executing tool '{tool_name}': {str(e)}",
                error_type="system_error",
            )

    def execute(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Synchronously executes a tool handler with defensive error handling.

        If the handler is an async coroutine, it automatically bridges execution.
        """
        if arguments is None:
            arguments = {}

        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolExecutionResult.fail(
                f"No handler registered for tool '{tool_name}'",
                error_type="not_found_error",
            )

        try:
            if inspect.iscoroutinefunction(handler):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        result = pool.submit(asyncio.run, handler(**arguments)).result()
                else:
                    result = asyncio.run(handler(**arguments))
            else:
                result = handler(**arguments)

            if isinstance(result, dict) and "success" in result and "error_type" in result:
                return result
            return ToolExecutionResult.ok(result)

        except TypeError as e:
            logger.warning(f"Validation error calling {tool_name} with {arguments}: {e}")
            return ToolExecutionResult.fail(
                f"Invalid arguments for tool '{tool_name}': {e}",
                error_type="validation_error",
            )
        except Exception as e:
            logger.error(f"System error executing tool {tool_name}: {e}", exc_info=True)
            return ToolExecutionResult.fail(
                f"Error executing tool '{tool_name}': {str(e)}",
                error_type="system_error",
            )


tool_engine = ToolEngine()
