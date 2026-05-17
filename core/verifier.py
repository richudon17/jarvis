"""
core/verifier.py
Goal-level verification for Phase 1.

This module checks whether completed tool evidence actually satisfies the
original goal before the orchestrator is allowed to accept a done step.
"""

import ast
import re
from pathlib import Path


FILENAME_PATTERN = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")


def _goal_filenames(goal: str) -> list[str]:
    return [match.group(0).rstrip(".,;:") for match in FILENAME_PATTERN.finditer(goal)]


def _goal_mentions_any(goal_lower: str, keywords: list[str]) -> bool:
    return any(keyword in goal_lower for keyword in keywords)


def _path_exists_with_content(path: str) -> tuple[bool, str]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return False, f"Expected file {path} does not exist."
    if not file_path.is_file():
        return False, f"Expected {path} to be a file."
    if file_path.stat().st_size == 0:
        return False, f"Expected file {path} is empty."
    return True, ""


def _python_file_is_valid(path: str) -> tuple[bool, str]:
    file_path = Path(path).expanduser()
    try:
        source = file_path.read_text(encoding="utf-8")
        ast.parse(source)
    except SyntaxError as e:
        location = f"line {e.lineno}, column {e.offset}" if e.lineno else "unknown location"
        return False, f"Python file {path} has invalid syntax at {location}: {e.msg}"
    except Exception as e:
        return False, f"Could not validate Python file {path}: {e}"
    return True, ""


def _successful_steps(completed_steps: list[dict], tool: str | None = None) -> list[dict]:
    steps = [
        step for step in completed_steps
        if step.get("status") == "success" and step.get("evaluation", {}).get("passed")
    ]
    if tool:
        return [step for step in steps if step.get("tool") == tool]
    return steps


def _successful_file_write_to(completed_steps: list[dict], path: str) -> bool:
    for step in _successful_steps(completed_steps, "file_write"):
        if str(step.get("tool_input", {}).get("path", "")) == path:
            tr = step.get("result")
            if not isinstance(tr, dict):
                return False
            if not tr.get("ok"):
                return False
            meta = tr.get("metadata") or {}
            bw = meta.get("bytes_written")
            if isinstance(bw, int) and bw > 0:
                return True
    return False


def _successful_file_write_metadata(completed_steps: list[dict], path: str) -> dict | None:
    """Return the metadata dict from a successful file_write step for `path`, if present."""
    for step in _successful_steps(completed_steps, "file_write"):
        if str(step.get("tool_input", {}).get("path", "")) == path:
            tr = step.get("result")
            if not isinstance(tr, dict):
                return None
            return tr.get("metadata") or {}
    return None



def _successful_file_read_of(completed_steps: list[dict], path: str) -> bool:
    for step in _successful_steps(completed_steps, "file_read"):
        if str(step.get("tool_input", {}).get("path", "")) == path:
            tr = step.get("result")
            if not isinstance(tr, dict):
                return False
            if not tr.get("ok"):
                return False
            data = tr.get("data") or {}
            content = data.get("content")
            return isinstance(content, str) and bool(content.strip())
    return False



def _has_successful_tool(completed_steps: list[dict], tool: str) -> bool:
    return bool(_successful_steps(completed_steps, tool))


def _saved_file_looks_summarized(path: str) -> tuple[bool, str]:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Could not read saved summary {path}: {e}"

    lowered = text.lower()
    raw_markers = lowered.count("title:") + lowered.count("url:") + lowered.count("snippet:")
    if raw_markers >= 4:
        return False, f"{path} looks like raw search snippets, not a clear summary."

    # Allow shorter summaries in tests; but still require meaningful content.
    if len(text.strip()) < 40:
        return False, f"{path} is too short to be a useful summary."

    # Must contain at least a summary header or bullets.
    if "#" not in text and "- " not in text:
        return False, f"{path} does not look like a summary markdown document."

    return True, ""



def verify_goal(goal: str, completed_steps: list[dict], attempted_steps: list[dict] | None = None) -> dict:
    """
    Verify that the original goal is satisfied by completed step evidence.
    Returns {'passed': bool, 'reason': str, 'confidence': float}.
    """
    attempted_steps = attempted_steps or []
    goal_lower = goal.lower()
    filenames = _goal_filenames(goal)

    if not _successful_steps(completed_steps):
        return {
            "passed": False,
            "reason": "No successful work was completed before the done step.",
            "confidence": 0.95,
        }

    is_research_goal = _goal_mentions_any(goal_lower, ["research", "search", "look up", "find information"])
    is_summary_goal = _goal_mentions_any(goal_lower, ["summary", "summarize", "summarise"])
    is_read_goal = _goal_mentions_any(goal_lower, ["read", "open"]) and filenames
    is_calculation_goal = _goal_mentions_any(goal_lower, ["calculate", "compute", "solve"])
    is_code_file_goal = any(path.endswith(".py") for path in filenames)
    is_code_generation_goal = _goal_mentions_any(
        goal_lower,
        ["create", "write", "build", "generate", "make", "implement"]
    ) and _goal_mentions_any(
        goal_lower,
        ["game", "app", "program", "script", "code", "pygame", "tkinter", "turtle"]
    )
    asks_to_save = _goal_mentions_any(goal_lower, ["save", "write", "create", "build", "generate", "make", "implement"])

    if is_read_goal and not is_research_goal:
        for path in filenames:
            if not _successful_file_read_of(completed_steps, path):
                return {
                    "passed": False,
                    "reason": f"Goal asked to read {path}, but there is no successful file_read result for that file.",
                    "confidence": 0.95,
                }

    if filenames and (asks_to_save or is_code_file_goal or is_research_goal):
        for path in filenames:
            ok, reason = _path_exists_with_content(path)
            if not ok:
                return {"passed": False, "reason": reason, "confidence": 0.95}

            # For explicit save requests, require successful structured file_write evidence.
            if asks_to_save and not _successful_file_write_to(completed_steps, path):
                return {
                    "passed": False,
                    "reason": f"Expected a successful file_write to {path}, but none was completed.",
                    "confidence": 0.9,
                }

            if path.endswith(".py"):
                ok, reason = _python_file_is_valid(path)
                if not ok:
                    return {"passed": False, "reason": reason, "confidence": 0.98}

                # If smoke-test metadata exists for the created python artifact, require compile success
                meta = _successful_file_write_metadata(completed_steps, path)
                if isinstance(meta, dict) and meta:
                    smoke = meta.get("smoke_test")
                    if isinstance(smoke, dict):
                        if not smoke.get("compiled", False):
                            return {"passed": False, "reason": f"Artifact {path} failed smoke-test compilation.", "confidence": 0.98}
                        # If compiled but neither executed nor execution was intentionally skipped,
                        # reduce confidence so downstream can trigger replanning/repair if needed.
                        if not (smoke.get("execution_skipped") or smoke.get("executed")):
                            return {"passed": True, "reason": f"Artifact {path} compiled but has no runtime evidence.", "confidence": 0.60}

                # If smoke-test metadata exists for the created python artifact, increase confidence.
                meta = _successful_file_write_metadata(completed_steps, path)
                if isinstance(meta, dict) and meta:
                    smoke = meta.get("smoke_test")
                    if isinstance(smoke, dict):
                        if smoke.get("compiled") and (smoke.get("executed") or smoke.get("execution_skipped")):
                            return {"passed": True, "reason": f"Artifact {path} runtime-validated.", "confidence": 0.90}
                        if smoke.get("compiled") and not (smoke.get("executed") or smoke.get("execution_skipped")):
                            # Compiled but no runtime evidence: lower confidence so orchestrator may replan.
                            return {"passed": True, "reason": f"Artifact {path} compiled but no runtime evidence.", "confidence": 0.60}

            # Research summaries must not be raw dumps.
            if is_research_goal and is_summary_goal and not path.endswith(".py"):
                ok, reason = _saved_file_looks_summarized(path)
                if not ok:
                    return {"passed": False, "reason": reason, "confidence": 0.85}


    if is_code_generation_goal and not filenames:
        written_python_files = [
            str(step.get("tool_input", {}).get("path", ""))
            for step in _successful_steps(completed_steps, "file_write")
            if str(step.get("tool_input", {}).get("path", "")).endswith(".py")
        ]
        if not written_python_files:
            return {
                "passed": False,
                "reason": "Code generation goal did not successfully write a Python file.",
                "confidence": 0.9,
            }
        for path in written_python_files:
            ok, reason = _path_exists_with_content(path)
            if not ok:
                return {"passed": False, "reason": reason, "confidence": 0.95}
            ok, reason = _python_file_is_valid(path)
            if not ok:
                return {"passed": False, "reason": reason, "confidence": 0.98}

    if is_research_goal and not _has_successful_tool(completed_steps, "web_search"):
        return {
            "passed": False,
            "reason": "Goal required research/search, but no successful web_search step completed.",
            "confidence": 0.9,
        }

    if is_calculation_goal and not filenames:
        # Calculation must produce executed stdout (structured run_python result).
        run_steps = _successful_steps(completed_steps, "run_python")
        if not run_steps:
            return {
                "passed": False,
                "reason": "Goal asked for a calculation, but no successful run_python step completed.",
                "confidence": 0.85,
            }

        has_meaningful_stdout = False
        for st in run_steps:
            tr = st.get("result")
            if not isinstance(tr, dict):
                continue
            data = tr.get("data") or {}
            stdout = data.get("stdout", "")
            if isinstance(stdout, str) and len(stdout.strip()) > 0:
                has_meaningful_stdout = True
                break

        if not has_meaningful_stdout:
            return {
                "passed": False,
                "reason": "Goal asked for a calculation, but run_python produced no meaningful stdout.",
                "confidence": 0.85,
            }

        # Extra check: if run_python referenced a path, ensure that path exists (prevent forged runtime claims).
        for st in run_steps:
            path_ref = str(st.get("tool_input", {}).get("path", ""))
            if path_ref:
                ok, reason = _path_exists_with_content(path_ref)
                if not ok:
                    return {
                        "passed": False,
                        "reason": f"run_python references {path_ref} but file is missing or empty.",
                        "confidence": 0.95,
                    }


    # Hard gate: done cannot pass if there is zero successful evidence.
    if len(_successful_steps(completed_steps)) == 0:
        return {
            "passed": False,
            "reason": "Only failed/irrelevant attempts were recorded before done.",
            "confidence": 0.95,
        }


    return {
        "passed": True,
        "reason": "Completed evidence satisfies Phase 1 goal checks.",
        "confidence": 0.75,
    }
