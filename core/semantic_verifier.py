"""core/semantic_verifier.py

Phase 1 reliability: semantic verification layer.

This module is intentionally lightweight: it provides a semantic review
interface so that Phase 1 can enforce *quality* gates beyond structural
checks.

Because this repository runs in environments where the LLM may not be
available during tests, semantic verification uses a bounded heuristic
fallback when the LLM call fails.

Return schema:
{
  "passed": bool,
  "confidence": float,
  "issues": [str],
  "recommendations": [str],
  "category": str
}
"""

from __future__ import annotations

from typing import Any
import json
import re

from core.verifier import verify_goal  # for category inference reuse


def _infer_category_from_goal(goal: str) -> str:
    gl = goal.lower()
    if any(k in gl for k in ["research", "search", "look up", "find information", "investigate"]):
        return "research"
    if any(k in gl for k in ["calculate", "compute", "solve", "fibonacci", "sum ", "mean", "median", "average", "do a calculation"]):
        return "calculation"
    if any(k.endswith(".py") for k in re.findall(r"[\w./-]+\.py\b", goal)):
        return "code"
    # Detect code/file creation goals
    if any(k in gl for k in ["write code", "write a python", "create a python"]):
        return "code"
    if any(k in gl for k in ["create a file", "write to", "save to", "write a file"]):
        return "file"
    if any(k in gl for k in ["create", "generate"] + [p for p in re.findall(r"[\w./-]+\.txt\b", goal)]):
        return "file"
    return "unknown"


def _is_trivial_placeholder_code(content: str) -> bool:
    """Check if code is just placeholder/stub (pass, ..., TODO comments)."""
    lines = content.strip().split("\n")
    # Remove comments and whitespace
    code_lines = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    
    # All trivial patterns
    trivial_patterns = ["pass", "...", "todo", "placeholder", "not implemented"]
    
    # If code is empty or only 1 line of trivial stuff
    if not code_lines or len(code_lines) <= 1:
        if not code_lines:
            return True
        return any(p in code_lines[0].lower() for p in trivial_patterns)
    
    return False


def _code_has_depth(content: str) -> bool:
    """Check if code has real substance (functions, classes, logic)."""
    substance_indicators = [
        "def ", "class ", "if ", "for ", "while ", "try:", "import ",
        "return ", "yield ", "with ", "lambda", "assert "
    ]
    return any(indicator in content for indicator in substance_indicators)


def _fallback_semantic_check(goal: str, completed_steps: list[dict]) -> dict[str, Any]:
    category = _infer_category_from_goal(goal)
    issues: list[str] = []
    recs: list[str] = []
    confidence = 0.55

    if category == "research":
        # Must have either summarize_text evidence OR file_write with markdown content
        has_good_summary = False
        
        for st in completed_steps:
            if st.get("tool") == "summarize_text":
                tr = st.get("result")
                if isinstance(tr, dict) and tr.get("ok") and isinstance((tr.get("data") or {}), dict):
                    summary = (tr.get("data") or {}).get("summary")
                    if isinstance(summary, str) and summary.strip():
                        text = summary
                        if "##" not in text and "#" not in text:
                            issues.append("Research summary lacks markdown structure.")
                        if "## sources" not in text.lower() and "# sources" not in text.lower():
                            issues.append("Research summary missing sources section.")
                        if not issues:
                            has_good_summary = True
                            confidence = 0.75
                        else:
                            confidence = 0.45
            
            # Also check for file_write with markdown content
            if st.get("tool") == "file_write" and st.get("status") == "success":
                p = str((st.get("tool_input") or {}).get("path", ""))
                if p and not p.endswith(".py"):
                    # This might be a research output file
                    tr = st.get("result")
                    if isinstance(tr, dict) and tr.get("ok"):
                        meta = tr.get("metadata") or {}
                        bytes_written = meta.get("bytes_written", 0)
                        if bytes_written > 100:  # Reasonable content
                            # Check that content actually has sources section
                            content = str((st.get("tool_input") or {}).get("content", ""))
                            if "## sources" in content.lower() or "# sources" in content.lower():
                                has_good_summary = True
                                confidence = 0.70
                            else:
                                issues.append("Research file missing sources section.")
                                confidence = 0.45

        if not has_good_summary and not any(st.get("tool") == "summarize_text" for st in completed_steps):
            issues.append("No evidence of summarization for research.")
            confidence = 0.35

        passed = confidence >= 0.65 and not issues
        return {
            "passed": passed,
            "confidence": float(confidence),
            "issues": issues,
            "recommendations": recs,
            "category": category,
        }

    if category == "calculation":
        # Must have run_python stdout with non-empty content.
        stdout = ""
        for st in completed_steps:
            if st.get("tool") == "run_python" and st.get("status") == "success":
                tr = st.get("result")
                if isinstance(tr, dict):
                    stdout = ((tr.get("data") or {}).get("stdout") or "")
                    break
        if not isinstance(stdout, str) or not stdout.strip():
            issues.append("Calculation has no stdout output.")
            confidence = 0.3
        else:
            # Basic plausibility.
            confidence = 0.8

        passed = confidence >= 0.7
        return {
            "passed": passed,
            "confidence": float(confidence),
            "issues": issues,
            "recommendations": recs,
            "category": category,
        }

    # code: ensure file_write evidence and non-trivial content
    if category == "code":
        file_writes = [st for st in completed_steps if st.get("tool") == "file_write" and str((st.get("tool_input") or {}).get("path", "")).endswith(".py") and st.get("status") == "success"]
        if not file_writes:
            issues.append("No successful Python file_write evidence for code goal.")
            confidence = 0.25
        else:
            # Check code quality
            content = str((file_writes[0].get("tool_input") or {}).get("content", ""))
            if _is_trivial_placeholder_code(content):
                issues.append("Code is only placeholder/stub (pass, ..., TODO).")
                confidence = 0.35
            elif not _code_has_depth(content):
                issues.append("Code lacks substantive logic (no functions, classes, or control flow).")
                confidence = 0.50
            else:
                confidence = 0.75

        passed = confidence >= 0.7 and not issues
        return {
            "passed": passed,
            "confidence": float(confidence),
            "issues": issues,
            "recommendations": recs,
            "category": category,
        }

    # file: ensure file_write succeeded with content
    if category == "file":
        file_writes = [st for st in completed_steps if st.get("tool") == "file_write" and st.get("status") == "success"]
        if not file_writes:
            issues.append("No successful file_write evidence for file goal.")
            confidence = 0.25
        else:
            tr = file_writes[0].get("result")
            if isinstance(tr, dict):
                meta = tr.get("metadata") or {}
                bytes_written = meta.get("bytes_written", 0)
                if bytes_written <= 0:
                    issues.append("File write produced empty or no bytes.")
                    confidence = 0.25
                else:
                    # Check if content is actually placeholder/stub
                    content = str((file_writes[0].get("tool_input") or {}).get("content", ""))
                    if _is_trivial_placeholder_code(content):
                        issues.append("File content is only placeholder/stub code.")
                        confidence = 0.35
                    else:
                        confidence = 0.75
        
        passed = confidence >= 0.7 and not issues
        return {
            "passed": passed,
            "confidence": float(confidence),
            "issues": issues,
            "recommendations": recs,
            "category": category,
        }

    return {
        "passed": False,
        "confidence": 0.4,
        "issues": ["Could not infer category or semantic evidence missing."],
        "recommendations": recs,
        "category": category,
    }


def semantic_verify_goal(goal: str, completed_steps: list[dict], attempted_steps: list[dict] | None = None) -> dict[str, Any]:
    """Semantic verifier.

    If an LLM is configured, this can call it. For now, we keep the
    implementation deterministic for tests.
    """

    # Deterministic fallback always used in this repo to keep tests stable.
    # (Hook point for LLM semantic checks can be added later.)
    return _fallback_semantic_check(goal, completed_steps)

