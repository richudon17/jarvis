"""Quality-aware review layer for Phase 1.

This module produces a structured quality report for the artifact implied by
a goal, based on available evidence (completed/attempted steps).

The quality review is intentionally heuristic and bounded.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


CATEGORY_CODE = "code"
CATEGORY_RESEARCH = "research"
CATEGORY_CALCULATION = "calculation"
CATEGORY_FILE = "file"
CATEGORY_UNKNOWN = "unknown"


_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|your code here|placeholder|pass\s*$|pass\s*#|pass\n)\b",
    re.IGNORECASE | re.MULTILINE,
)

_FILENAME_PATTERN = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")


def _goal_filenames(goal: str) -> list[str]:
    return [m.group(0).rstrip(".,;:") for m in _FILENAME_PATTERN.finditer(goal)]


def _goal_mentions_any(goal_lower: str, keywords: list[str]) -> bool:
    return any(k in goal_lower for k in keywords)


def _infer_category(goal: str, filenames: list[str], completed_steps: list[dict]) -> str:
    gl = goal.lower()

    if _goal_mentions_any(gl, ["research", "search", "look up", "find information"]):
        return CATEGORY_RESEARCH
    if _goal_mentions_any(gl, ["calculate", "compute", "solve", "fibonacci"]):
        return CATEGORY_CALCULATION

    if filenames:
        if any(p.endswith(".py") for p in filenames):
            return CATEGORY_CODE
        return CATEGORY_FILE

    # Fall back: if a code-writing tool is present, assume code.
    if any(s.get("tool") == "file_write" and str(s.get("tool_input", {}).get("path", "")).endswith(".py") for s in completed_steps):
        return CATEGORY_CODE

    return CATEGORY_UNKNOWN


def _python_file_is_valid(path: str) -> tuple[bool, str]:
    file_path = Path(path).expanduser()
    try:
        source = file_path.read_text(encoding="utf-8")
        ast.parse(source)
        return True, ""
    except SyntaxError as e:
        location = f"line {e.lineno}, column {e.offset}" if e.lineno else "unknown location"
        return False, f"invalid syntax at {location}: {e.msg}"
    except Exception as e:
        return False, f"could not read/validate python: {e}"


def _file_non_empty(path: str) -> tuple[bool, str]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return False, f"expected file {path} does not exist"
    if not file_path.is_file():
        return False, f"expected {path} to be a file"
    if file_path.stat().st_size <= 0:
        return False, f"expected {path} to be non-empty"
    return True, ""


def _read_text_if_exists(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except Exception:
        return ""


def _contains_any(text: str, tokens: list[str]) -> bool:
    tl = text.lower()
    return any(t.lower() in tl for t in tokens)


def _quality_penalty(score: float, penalty: float) -> float:
    return max(0.0, min(1.0, score - penalty))


def _review_python_code(path: str, goal: str, completed_steps: list[dict] | None = None) -> dict[str, Any]:
    score = 1.0
    issues: list[str] = []
    recs: list[str] = []

    ok, reason = _file_non_empty(path)
    if not ok:
        return {
            "passed": False,
            "score": 0.0,
            "issues": [reason],
            "recommendations": ["Regenerate and write complete non-empty Python code."],
            "category": CATEGORY_CODE,
        }

    text = _read_text_if_exists(path)
    if not text.strip():
        score = 0.0
        issues.append("Python file is empty or whitespace only.")

    # Syntax
    valid, syn_reason = _python_file_is_valid(path)
    if not valid:
        return {
            "passed": False,
            "score": 0.0,
            "issues": [f"{syn_reason}"],
            "recommendations": ["Fix Python syntax and write a valid module."],
            "category": CATEGORY_CODE,
        }

    # Placeholder / stubs
    if _PLACEHOLDER_RE.search(text):
        score = _quality_penalty(score, 0.35)
        issues.append("Code appears to contain placeholders/stubs (TODO/pass-your code here).")
        recs.append("Replace placeholders with real implementation and executable logic.")

    # Meaningful logic heuristic: function/class OR main/loop.
    has_def = bool(re.search(r"^\s*def\s+\w+\s*\(", text, re.MULTILINE))
    has_class = bool(re.search(r"^\s*class\s+\w+\b", text, re.MULTILINE))
    has_main_guard = "if __name__ == \"__main__\":" in text or "if __name__ == '__main__':" in text
    has_execution = bool(re.search(r"\bwhile\s+True\b|\bfor\s+\w+\s+in\s+|\bargparse\b|\bprint\(", text))

    if not (has_def or has_class or has_execution):
        score = _quality_penalty(score, 0.35)
        issues.append("Code has little to no executable logic (no defs/classes and no obvious runtime flow).")
        recs.append("Add real functions/classes or a runnable main/entrypoint.")

    # Consider smoke-test runtime validation evidence (bonus)
    try:
        bonus = _python_smoke_test_bonus(path, completed_steps)
        if bonus > 0:
            score = float(min(1.0, score + bonus))
    except Exception:
        pass

    # Banned/flagged patterns
    if _contains_any(text, ["eval("]):
        # Allow only if goal explicitly asks for eval-like calculator behavior.
        if "calculator" not in goal.lower():
            score = _quality_penalty(score, 0.25)
            issues.append("Code uses eval() without an explicit calculator context.")
            recs.append("Remove eval(); use safe parsing or direct computation.")

    if re.search(r"\bexcept\s*:\s*$", text, re.MULTILINE):
        score = _quality_penalty(score, 0.15)
        issues.append("Code contains a bare except: handler.")
        recs.append("Catch specific exception types and handle appropriately.")

    # pygame/game heuristics
    gl = goal.lower()
    is_pygame_target = any(k in gl for k in ["pygame", "snake", "tetris", "game", "app"])
    if "pygame" in text or is_pygame_target:
        # Don't require execution, only structure.
        has_pygame_init = "pygame.init" in text
        has_event_loop = bool(re.search(r"pygame\.event\.get\(\)", text))
        has_display_update = _contains_any(text, ["pygame.display.flip()", "pygame.display.update()", "display.flip()", "display.update()"])
        has_clock_tick = "clock.tick" in text or "FPS" in text

        # Penalize if likely auto-exec without a main guard.
        has_main_guard = has_main_guard or bool(re.search(r"def\s+main\s*\(", text))
        if re.search(r"^\s*pygame\.init\(\)\s*$", text, re.MULTILINE) and not has_main_guard:
            score = _quality_penalty(score, 0.15)
            issues.append("pygame.init() appears at import time without clear main/guard.")
            recs.append("Move pygame.init() and game loop into a main() guarded by if __name__ == '__main__'.")

        missing = []
        if not has_pygame_init:
            missing.append("pygame.init")
        if not has_event_loop:
            missing.append("pygame event loop")
        if not has_display_update:
            missing.append("display update/flip")
        if not has_clock_tick:
            missing.append("clock tick / frame limiting")

        if missing:
            score = _quality_penalty(score, 0.25 + 0.05 * len(missing))
            issues.append(f"pygame/game structure missing: {', '.join(missing)}")
            recs.append("Add a proper pygame loop with event handling and display update + clock.tick.")

        # Obvious empty draw/blit calls
        if re.search(r"pygame\.draw\.[a-zA-Z_]+\([^\)]*\)\s*\,\s*\)\s*", text):
            score = _quality_penalty(score, 0.25)
            issues.append("Possible malformed pygame.draw call detected.")
            recs.append("Fix drawing calls (missing arguments) so they render correctly.")

        if re.search(r"\.blit\([^\)]*,\s*\)$", text):
            score = _quality_penalty(score, 0.2)
            issues.append("Possible malformed surface.blit call detected.")
            recs.append("Fix blit calls with complete coordinates/text surfaces.")

    # CLI heuristics (argparse or main guard)
    if "argparse" in text:
        if not has_main_guard:
            score = _quality_penalty(score, 0.15)
            issues.append("argparse is used but no __main__ guard detected.")
            recs.append("Wrap CLI entrypoint in if __name__ == '__main__': main().")
    else:
        # If it's intended as CLI (goal mentions todo/app/command), ensure a main guard.
        if any(k in goal.lower() for k in ["todo", "command", "cli", "list tasks", "terminal"]):
            if not has_main_guard:
                score = _quality_penalty(score, 0.25)
                issues.append("CLI app appears to lack if __name__ == '__main__' guard.")
                recs.append("Add a main guard and a clear command loop or argparse." )

    passed = score >= 0.7 and not any("placeholders" in i.lower() for i in issues)
    return {
        "passed": passed,
        "score": float(max(0.0, min(1.0, score))),
        "issues": issues[:10],
        "recommendations": recs[:10],
        "category": CATEGORY_CODE,
    }


def _python_smoke_test_bonus(path: str, completed_steps: list[dict] | None) -> float:
    """Return a small positive bonus if smoke-test runtime evidence exists and passed."""
    if not completed_steps:
        return 0.0
    for st in completed_steps:
        if st.get("tool") == "file_write" and str(st.get("tool_input", {}).get("path", "")) == path:
            tr = st.get("result")
            if not isinstance(tr, dict):
                return 0.0
            meta = tr.get("metadata") or {}
            smoke = meta.get("smoke_test") if isinstance(meta, dict) else None
            if isinstance(smoke, dict):
                # Reward compiled + executed or compiled + execution_skipped
                if smoke.get("compiled") and (smoke.get("executed") or smoke.get("execution_skipped")):
                    return 0.15
    return 0.0


def _review_research(goal: str, completed_steps: list[dict], attempted_steps: list[dict]) -> dict[str, Any]:
    score = 1.0
    issues: list[str] = []
    recs: list[str] = []

    # Find file_write to markdown/html-ish summary
    out_paths: list[str] = []
    for st in completed_steps:
        if st.get("tool") == "file_write":
            p = str(st.get("tool_input", {}).get("path", ""))
            if p and not p.endswith(".py"):
                out_paths.append(p)

    # If none, also allow summarize_text output presence.
    has_summarize = any(st.get("tool") == "summarize_text" for st in completed_steps)

    if not out_paths and not has_summarize:
        return {
            "passed": False,
            "score": 0.0,
            "issues": ["No research artifact or summarize step evidence found."],
            "recommendations": ["Use web_search then summarize_text, then save a clear summary."],
            "category": CATEGORY_RESEARCH,
        }

    # If file artifacts exist, validate the most likely one(s).
    def check_text(text: str) -> float:
        nonlocal score
        lowered = text.lower()
        # reject raw snippets dumps
        if lowered.count("title:") + lowered.count("url:") + lowered.count("snippet:") >= 4:
            score = _quality_penalty(score, 0.5)
            issues.append("Research output looks like a raw search snippet dump.")

        # require heading or bullets
        if "#" not in text and "- " not in text:
            score = _quality_penalty(score, 0.25)
            issues.append("Research summary lacks a heading or bullet structure.")

        # length
        if len(text.strip()) < 120:
            score = _quality_penalty(score, 0.2)
            issues.append("Research summary is too short to be useful.")

        # topical mention
        gl = goal.lower()
        if any(kw in gl for kw in ["python", "packaging", "docker", "kubernetes", "javascript", "ai", "ml"]):
            topic = "python" if "python" in gl else "packaging" if "packaging" in gl else "research"
            if topic not in lowered:
                score = _quality_penalty(score, 0.15)
                issues.append("Summary may not mention the requested topic strongly enough.")

        # sources: if web_search evidence exists, require 'Source' or URLs.
        has_web = any(st.get("tool") == "web_search" for st in attempted_steps)
        if has_web:
            has_urls = bool(re.search(r"https?://", text))
            has_sources = "source" in lowered or "sources" in lowered
            if not (has_urls or has_sources):
                score = _quality_penalty(score, 0.15)
                issues.append("No sources/URLs detected in research summary despite web_search evidence.")

        return score

    # Check stored file writes if any; otherwise check summarize_text data.
    checked_any = False
    for p in out_paths[:3]:
        txt = _read_text_if_exists(p)
        if txt:
            checked_any = True
            check_text(txt)

    if not checked_any:
        for st in completed_steps:
            if st.get("tool") == "summarize_text":
                data = st.get("result", {}).get("data") if isinstance(st.get("result"), dict) else None
                summary = ""
                if isinstance(data, dict):
                    summary = data.get("summary") or data.get("text") or ""
                if isinstance(summary, str) and summary:
                    check_text(summary)
                    checked_any = True
                    break

    if not checked_any:
        score = _quality_penalty(score, 0.6)
        issues.append("Could not inspect research summary content.")
        recs.append("Save the research summary to a markdown file and include sources.")

    passed = score >= 0.7
    return {
        "passed": passed,
        "score": float(max(0.0, min(1.0, score))),
        "issues": issues[:10],
        "recommendations": recs[:10] or (["Improve summary structure and include sources."] if not passed else []),
        "category": CATEGORY_RESEARCH,
    }


def _review_calculation(goal: str, completed_steps: list[dict]) -> dict[str, Any]:
    score = 1.0
    issues: list[str] = []
    recs: list[str] = []

    stdout = ""
    for st in completed_steps:
        if st.get("tool") == "run_python":
            tr = st.get("result")
            if isinstance(tr, dict):
                stdout = (tr.get("data") or {}).get("stdout") or ""
                if stdout.strip():
                    break

    if not stdout.strip():
        return {
            "passed": False,
            "score": 0.0,
            "issues": ["Calculation stdout is empty."],
            "recommendations": ["Re-run calculation code and ensure it prints the result."],
            "category": CATEGORY_CALCULATION,
        }

    # Fibonacci sanity check if requested.
    gl = goal.lower()
    if "fibonacci" in gl:
        nums = re.findall(r"-?\d+", stdout)
        ints = [int(x) for x in nums][:20]
        if len(ints) < 10:
            score = _quality_penalty(score, 0.5)
            issues.append("Fibonacci output contains too few numbers.")
        else:
            # compute first len(ints) fibonacci
            fib = [0, 1]
            while len(fib) < len(ints):
                fib.append(fib[-1] + fib[-2])
            expected = fib[:20]
            ok_prefix = ints[: min(20, len(ints))] == expected[: min(20, len(ints))]
            if not ok_prefix:
                score = _quality_penalty(score, 0.6)
                issues.append("Fibonacci sequence does not match expected values.")

    # general output length
    if len(stdout.strip()) < 5:
        score = _quality_penalty(score, 0.3)
        issues.append("Calculation output is too short.")

    passed = score >= 0.7
    return {
        "passed": passed,
        "score": float(max(0.0, min(1.0, score))),
        "issues": issues[:10],
        "recommendations": recs[:10] or (["Ensure the calculation prints the expected output."] if not passed else []),
        "category": CATEGORY_CALCULATION,
    }


def review_quality(goal: str, completed_steps: list[dict], attempted_steps: list[dict] | None = None) -> dict[str, Any]:
    """Entry point.

    Returns a structured quality report.
    """
    attempted_steps = attempted_steps or completed_steps
    filenames = _goal_filenames(goal)
    category = _infer_category(goal, filenames, completed_steps)

    if category == CATEGORY_CODE:
        # Prefer explicit filenames mentioned in goal.
        candidates = [p for p in filenames if p.endswith(".py")]
        if not candidates:
            # fallback to file_write evidence
            for st in completed_steps:
                if st.get("tool") == "file_write":
                    p = str(st.get("tool_input", {}).get("path", ""))
                    if p.endswith(".py"):
                        candidates.append(p)
        if not candidates:
            return {
                "passed": False,
                "score": 0.0,
                "issues": ["No Python artifact found for code quality review."],
                "recommendations": ["Save the generated Python code to a .py file."],
                "category": category,
            }

        # Review the first candidate.
        return _review_python_code(candidates[0], goal, completed_steps=completed_steps)

    if category == CATEGORY_RESEARCH:
        return _review_research(goal, completed_steps=completed_steps, attempted_steps=attempted_steps)

    if category == CATEGORY_CALCULATION:
        return _review_calculation(goal, completed_steps=completed_steps)

    if category == CATEGORY_FILE:
        # Basic file quality.
        candidates = [p for p in filenames if p and not p.endswith(".py")]
        if not candidates:
            return {
                "passed": False,
                "score": 0.0,
                "issues": ["No output file evidence found."],
                "recommendations": ["Write the requested file with content."],
                "category": category,
            }
        p = candidates[0]
        ok, reason = _file_non_empty(p)
        if not ok:
            return {
                "passed": False,
                "score": 0.0,
                "issues": [reason],
                "recommendations": ["Write a non-empty file."],
                "category": category,
            }
        return {
            "passed": True,
            "score": 0.8,
            "issues": [],
            "recommendations": [],
            "category": category,
        }

    return {
        "passed": False,
        "score": 0.0,
        "issues": ["Unknown goal category; quality review could not classify the artifact."],
        "recommendations": ["Ensure the goal requests a code file, research summary, or calculation output."],
        "category": category,
    }

