"""\
core/evaluator.py

Phase 1 reliability: step evaluation must be structured and deterministic.

A step is evaluated using tool result schema:
{
  "ok": bool,
  "data": ...,
  "error": str | None,
  "metadata": dict
}
"""

from __future__ import annotations


def evaluate_step(step: dict) -> dict:
    """Assess a completed step using structured result fields."""
    status = step.get("status", "")
    
    # Done steps always pass regardless of result
    if status == "done":
        return {**step, "evaluation": {"passed": True, "reason": "Task marked complete."}}
    
    tool_result = step.get("result")

    # Handle missing/None result as tool failure
    if tool_result is None:
        return {
            **step,
            "evaluation": {
                "passed": False,
                "reason": "tool produced no result",
            },
        }

    # Normalize legacy: if result is a string, treat non-empty as ok.
    if not isinstance(tool_result, dict):
        ok = bool(str(tool_result).strip())
        err = None if ok else str(tool_result)
        tool_result = {"ok": ok, "data": None, "error": err, "metadata": {"legacy": True}}

    if not tool_result.get("ok", False):
        return {
            **step,
            "evaluation": {
                "passed": False,
                "reason": tool_result.get("error") or "tool reported failure",
            },
        }

    # passed tool result => now apply a minimal deterministic sanity check
    data = tool_result.get("data")
    metadata = tool_result.get("metadata") or {}

    # file_write: require non-empty bytes_written (if present)
    if step.get("tool") == "file_write":
        bw = metadata.get("bytes_written")
        if isinstance(bw, int) and bw <= 0:
            return {**step, "evaluation": {"passed": False, "reason": "file_write produced empty file"}}

    # file_read: require content non-empty (if present)
    if step.get("tool") == "file_read":
        content = (data or {}).get("content")
        if isinstance(content, str) and not content.strip():
            return {**step, "evaluation": {"passed": False, "reason": "file_read returned empty content"}}

    # run_python: require exit_code==0 and/or stdout non-empty when output is expected
    if step.get("tool") == "run_python":
        exit_code = metadata.get("exit_code")
        stdout = (data or {}).get("stdout")
        if exit_code is not None and exit_code != 0:
            return {**step, "evaluation": {"passed": False, "reason": f"run_python nonzero exit_code={exit_code}"}}
        if isinstance(stdout, str) and stdout == "":
            # still allow empty stdout if exit_code==0; caller/verifier will decide for calculation goals
            pass

    if step.get("tool") in ("web_search", "summarize_text"):
        # Require returned data to exist
        if data is None:
            return {**step, "evaluation": {"passed": False, "reason": "tool returned no data"}}

    return {**step, "evaluation": {"passed": True, "reason": "Tool ok"}}


def check_loop_detection(step_history: list, current_step: dict, threshold: int = 3) -> bool:
    """Detect if we're stuck in a loop — same tool + same input repeated too many times."""
    tool = current_step.get("tool")
    tool_input = str(current_step.get("tool_input", {}))

    count = sum(
        1
        for s in step_history
        if s.get("tool") == tool and str(s.get("tool_input", {})) == tool_input
    )
    return count >= threshold

