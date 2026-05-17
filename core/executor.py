"""
core/executor.py
Pure execution engine. Receives a step from the planner and runs it.
No reasoning here — just call the tool and return the result.
"""

from tools.tool_registry import execute_tool


def _coerce_tool_result(result):
    """Coerce tool results into the structured schema.

    Phase 1 hardening requirement: executor must NOT infer success/failure from
    strings. If the tool does not return a dict, treat it as failure.
    """

    if isinstance(result, dict):
        return {
            "ok": bool(result.get("ok", False)),
            "data": result.get("data"),
            "error": result.get("error"),
            "metadata": result.get("metadata") or {},
        }

    # Non-dict tool results are rejected deterministically.
    return {
        "ok": False,
        "data": None,
        "error": f"Tool returned non-structured result type={type(result).__name__}",
        "metadata": {"legacy": True},
    }



def run_step(step: dict) -> dict:
    """Execute a single plan step and preserve structured tool results."""

    tool = step.get("tool", "")
    tool_input = step.get("tool_input", {}) or {}

    if tool == "done":
        # Preserve the done summary as the result payload for downstream logging.
        return {
            **step,
            "result": {
                "ok": True,
                "data": {"summary": tool_input.get("summary", "Task complete.")},
                "error": None,
                "metadata": {"done": True},
            },
            "status": "done",
        }

    raw_result = execute_tool(tool, tool_input)
    result = _coerce_tool_result(raw_result)

    status = "success" if result.get("ok") else "failed"
    return {
        **step,
        "tool": tool,
        "tool_input": tool_input,
        "result": result,
        "status": status,
    }


