"""Deterministic recovery rules for JARVIS Phase 1.

This module sits between:
- core/executor (tool execution + structured results)
- core/orchestrator (planning/replanning)

Goal: handle common, structured, non-LLM-fixable failures deterministically.
Only if repairs cannot fix the issue do we allow the orchestrator to call
LLM replan().

All repairs must be bounded: no infinite loops.

Phase 1 improvements:
- Offline repair capabilities (no LLM required for many fixes)
- Auto-fix Python syntax errors
- Generate minimal viable content for empty files
- Simplify timeout-prone code
- Alternative path fallbacks
- Alternative search queries
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path
from typing import Any, Optional


def _get_result_ok(step_result: Any) -> bool:
    if isinstance(step_result, dict):
        return bool(step_result.get("ok", False))
    return False


def deterministic_repair(
    *,
    step: dict,
    executed_step: dict,
    goal: str,
    completed_steps: list[dict],
    attempted_steps: list[dict],
    previous_failure_fingerprint: Optional[str] = None,
) -> dict:
    """
    Returns one of:
      {"handled": True, "action": "retry" | "stop" | "convert" | "skip", "new_steps": [...]}
      {"handled": False}

    The orchestrator will execute the returned new_steps (if any) or stop.
    
    New actions:
      - "skip": Skip the step and continue (when failure is non-critical)
    """

    # If success, nothing to do.
    if executed_step.get("status") == "success":
        return {"handled": False}

    tool = step.get("tool")
    tool_input = step.get("tool_input", {}) or {}
    result = executed_step.get("result", {})

    # If no structured result, can't reliably repair.
    if not isinstance(result, dict):
        return {"handled": False}

    ok = bool(result.get("ok", False))
    if ok:
        return {"handled": False}

    error = result.get("error")
    metadata = result.get("metadata") or {}

    # Prevent repeated same failure loops.
    if previous_failure_fingerprint is not None:
        return {
            "handled": True,
            "action": "stop",
            "reason": "Same failure repeated; deterministic repair stopped.",
            "new_steps": [],
        }

    # ── File write repairs ──
    if tool == "file_write":
        path = str(tool_input.get("path", ""))
        content = tool_input.get("content", "")
        
        # Empty content - try to generate minimal content offline
        if (content is None) or (not str(content).strip()):
            minimal_content = _generate_minimal_content(goal, path)
            if minimal_content:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": "Empty content - generated minimal viable content offline",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": f"Write content to {path}",
                        "tool": "file_write",
                        "tool_input": {"path": path, "content": minimal_content}
                    }],
                }
            # Fallback: placeholder
            return {
                "handled": True,
                "action": "retry",
                "reason": "Empty content - retry with placeholder",
                "new_steps": [{
                    **step,
                    "tool_input": {**tool_input, "content": "# empty file placeholder (deterministic repair)\n"},
                }],
            }

        # Python syntax errors - try auto-fix offline
        if path.endswith(".py") and error and "syntax" in str(error).lower():
            fixed_content = _try_fix_python_syntax(content, error)
            if fixed_content:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": "Python syntax error - auto-fixed offline",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": f"Write fixed Python code to {path}",
                        "tool": "file_write",
                        "tool_input": {"path": path, "content": fixed_content}
                    }],
                }
            # Fall back to LLM correction
            return {
                "handled": True,
                "action": "retry",
                "reason": "Python syntax error - needs LLM correction",
                "new_steps": [],
                "needs_llm_correction": True,
            }

        # Disk/permission errors - try alternative path
        if any(phrase in str(error).lower() for phrase in ["permission", "read-only", "disk full", "no space"]):
            alt_path = _get_alternative_path(path)
            if alt_path != path:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": f"Cannot write to {path}, trying {alt_path}",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": f"Write to alternative path {alt_path}",
                        "tool": "file_write",
                        "tool_input": {"path": alt_path, "content": content}
                    }],
                }
            return {
                "handled": True,
                "action": "stop",
                "reason": f"Cannot write to {path}: {error}",
                "new_steps": [],
            }

        return {"handled": False}

    # ── File read repairs ──
    if tool == "file_read":
        path = str(tool_input.get("path", ""))
        
        # Missing file - try to create it or skip
        if error and ("not found" in str(error).lower() or "does not exist" in str(error).lower()):
            # If goal is to create the file, convert to a create step
            if any(kw in goal.lower() for kw in ["create", "write", "save", "make"]):
                return {
                    "handled": True,
                    "action": "convert",
                    "reason": f"File not found: {path}, converting to create step",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": f"Create file {path}",
                        "tool": "file_write",
                        "tool_input": {"path": path, "content": ""}
                    }],
                }
            return {
                "handled": True,
                "action": "stop",
                "reason": f"File not found: {path}",
                "new_steps": [],
            }
        return {"handled": False}

    # ── Run Python repairs ──
    if tool == "run_python":
        code = tool_input.get("code", "")
        
        # Blocked execution (pygame, tkinter, etc.) - convert to file_write
        if metadata.get("blocked") or (error and "execution blocked" in str(error).lower()):
            path = str(tool_input.get("path", "") or "")
            fallback = path if path.endswith(".py") else "output.py"
            return {
                "handled": True,
                "action": "convert",
                "reason": "Code execution blocked, converted to file_write",
                "new_steps": [{
                    "step_index": step.get("step_index"),
                    "description": f"Write blocked code to {fallback}",
                    "tool": "file_write",
                    "tool_input": {"path": fallback, "content": code}
                }],
            }
        
        # Timeout - try to simplify offline
        if metadata.get("timeout") or (error and "timeout" in str(error).lower()):
            simplified = _try_simplify_python(code)
            if simplified:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": "Execution timed out - simplified code offline",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": "Run simplified Python code",
                        "tool": "run_python",
                        "tool_input": {"code": simplified}
                    }],
                }
            return {
                "handled": True,
                "action": "convert",
                "reason": "Execution timed out - saving code instead",
                "new_steps": [{
                    "step_index": step.get("step_index"),
                    "description": "Save timed-out code to file",
                    "tool": "file_write",
                    "tool_input": {"path": "output.py", "content": code}
                }],
            }
        
        # Syntax error - try auto-fix offline
        if error and "syntax" in str(error).lower():
            fixed_code = _try_fix_python_syntax(code, error)
            if fixed_code:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": "Python syntax error - auto-fixed offline",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": "Run fixed Python code",
                        "tool": "run_python",
                        "tool_input": {"code": fixed_code}
                    }],
                }
            # Convert to file_write
            return {
                "handled": True,
                "action": "convert",
                "reason": "Python syntax error - saving code for inspection",
                "new_steps": [{
                    "step_index": step.get("step_index"),
                    "description": "Save failing code to file",
                    "tool": "file_write",
                    "tool_input": {"path": "output.py", "content": code}
                }],
            }
        
        # Runtime error with output - might still be useful
        data = result.get("data", {})
        if isinstance(data, dict) and data.get("stdout"):
            stdout = data["stdout"]
            if stdout.strip():
                return {
                    "handled": True,
                    "action": "skip",
                    "reason": f"Runtime error but produced output: {stdout[:100]}",
                    "new_steps": [],
                }

        return {"handled": False}

    # ── Web search repairs ──
    if tool == "web_search":
        query = tool_input.get("query", "")
        
        # Rate limiting - retry
        if error and any(phrase in str(error).lower() for phrase in ["rate limit", "too many requests", "429", "quota"]):
            return {
                "handled": True,
                "action": "retry",
                "reason": "Rate limited - will retry",
                "new_steps": [],
            }
        
        # Network errors - retry
        if error and any(phrase in str(error).lower() for phrase in ["network", "connection", "timeout"]):
            return {
                "handled": True,
                "action": "retry",
                "reason": f"Network error - will retry: {error}",
                "new_steps": [],
            }
        
        # Empty results - try different query offline
        data = result.get("data")
        if data == [] or data is None:
            alt_query = _generate_alternative_query(query, goal)
            if alt_query and alt_query != query:
                return {
                    "handled": True,
                    "action": "retry",
                    "reason": f"Empty results for '{query}', trying '{alt_query}'",
                    "new_steps": [{
                        "step_index": step.get("step_index"),
                        "description": "Search with alternative query",
                        "tool": "web_search",
                        "tool_input": {"query": alt_query}
                    }],
                }

        return {"handled": False}

    # ── Summarize text repairs ──
    if tool == "summarize_text":
        # Empty or invalid text - try to use completed steps data
        if error and ("no text" in str(error).lower() or "empty" in str(error).lower()):
            if completed_steps:
                last_step = completed_steps[-1]
                step_result = last_step.get("result", {})
                if isinstance(step_result, dict) and step_result.get("data"):
                    return {
                        "handled": True,
                        "action": "retry",
                        "reason": "Using data from previous step for summarization",
                        "new_steps": [{
                            "step_index": step.get("step_index"),
                            "description": "Summarize available data",
                            "tool": "summarize_text",
                            "tool_input": {
                                "text": str(step_result["data"]),
                                "goal": tool_input.get("goal", "")
                            }
                        }],
                    }
            return {
                "handled": True,
                "action": "stop",
                "reason": "No text to summarize",
                "new_steps": [],
            }

        return {"handled": False}

    # ── Done step repairs ──
    if tool == "done":
        # Done step failed - this means verification failed
        return {
            "handled": True,
            "action": "retry",
            "reason": "Done step failed - needs verification fix",
            "new_steps": [],
            "needs_llm_correction": True,
        }

    # ── No matching repair pattern ──
    return {"handled": False}


# ══════════════════════════════════════════════════════════════
# Offline repair helpers (no LLM required)
# ══════════════════════════════════════════════════════════════

def _generate_minimal_content(goal: str, path: str) -> str | None:
    """Generate minimal viable content based on file type and goal."""
    if path.endswith(".py"):
        if "todo" in goal.lower():
            return '''#!/usr/bin/env python3
"""Simple todo list application."""
import json
from pathlib import Path

DATA_FILE = Path.home() / ".jarvis_todo.json"

def main():
    tasks = []
    if DATA_FILE.exists():
        try:
            tasks = json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            pass
    print(f"Todo list with {len(tasks)} tasks")

if __name__ == "__main__":
    main()
'''
        if "calc" in goal.lower() or "comput" in goal.lower():
            return '''#!/usr/bin/env python3
"""Simple calculator."""
def main():
    result = 2 + 2
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
'''
        if "fibonacci" in goal.lower():
            return '''#!/usr/bin/env python3
"""Calculate fibonacci numbers."""
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

def main():
    for i in range(10):
        print(f"F({i}) = {fibonacci(i)}")

if __name__ == "__main__":
    main()
'''
        # Generic minimal Python
        return '''#!/usr/bin/env python3
"""Generated Python script."""
def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
'''
    elif path.endswith(".txt"):
        return "Generated content.\n"
    elif path.endswith(".md"):
        return "# Generated Document\n\nContent goes here.\n"
    elif path.endswith(".json"):
        return '{"status": "generated", "data": {}}\n'
    return None


def _try_fix_python_syntax(code: str, error: str) -> str | None:
    """Try to fix common Python syntax errors without LLM."""
    # Try to extract line number from error
    line_match = re.search(r'line (\d+)', error.lower())
    if line_match:
        error_line = int(line_match.group(1))
        lines = code.split('\n')
        if 0 < error_line <= len(lines):
            line = lines[error_line - 1]
            
            # Fix: Missing colon after if/while/for/def/class
            colon_match = re.match(r'^(\s*(if|while|for|def|class|else|elif|try|except|finally|with)\s+.*[^:{\s])$', line)
            if colon_match:
                lines[error_line - 1] = line.rstrip() + ':'
                fixed = '\n'.join(lines)
                try:
                    ast.parse(fixed)
                    return fixed
                except SyntaxError:
                    pass
            
            # Fix: Unclosed parenthesis
            if line.count('(') > line.count(')'):
                diff = line.count('(') - line.count(')')
                lines[error_line - 1] = line + ')' * diff
                fixed = '\n'.join(lines)
                try:
                    ast.parse(fixed)
                    return fixed
                except SyntaxError:
                    pass
            
            # Fix: Unclosed bracket
            if line.count('[') > line.count(']'):
                diff = line.count('[') - line.count(']')
                lines[error_line - 1] = line + ']' * diff
                fixed = '\n'.join(lines)
                try:
                    ast.parse(fixed)
                    return fixed
                except SyntaxError:
                    pass
            
            # Fix: Unclosed brace
            if line.count('{') > line.count('}'):
                diff = line.count('{') - line.count('}')
                lines[error_line - 1] = line + '}' * diff
                fixed = '\n'.join(lines)
                try:
                    ast.parse(fixed)
                    return fixed
                except SyntaxError:
                    pass
            
            # Fix: Unclosed string (single/double quote)
            stripped = line.rstrip()
            if stripped.endswith(('"', "'")) and stripped.count(stripped[-1]) % 2 != 0:
                # Try adding closing quote
                quote = stripped[-1]
                lines[error_line - 1] = stripped + quote
                fixed = '\n'.join(lines)
                try:
                    ast.parse(fixed)
                    return fixed
                except SyntaxError:
                    pass

    # Fix: Missing main guard if code ends with def main()
    if 'if __name__' not in code and code.strip().endswith('def main():'):
        # Check if there's an indented block after def main()
        lines = code.rstrip().split('\n')
        if len(lines) > 1:
            fixed = code.rstrip() + '\n\nif __name__ == "__main__":\n    main()\n'
            try:
                ast.parse(fixed)
                return fixed
            except SyntaxError:
                pass

    # Fix: Missing import for commonly used modules
    if 'print(' in code and 'import' not in code:
        # Code might just need to be wrapped
        pass

    return None


def _try_simplify_python(code: str) -> str | None:
    """Try to simplify Python code that might be timing out."""
    modified = False
    
    # Replace infinite loops with bounded loops
    if re.search(r'while\s+(True|1|False\s*==\s*False)\s*:', code):
        code = re.sub(
            r'while\s+(True|1|False\s*==\s*False)\s*:',
            'for _ in range(1000):  # Bounded loop for safety',
            code
        )
        modified = True
    
    # Remove or skip time.sleep calls
    if 'time.sleep' in code:
        code = re.sub(r'time\.sleep\([^)]*\)', 'pass  # Skipped sleep for speed', code)
        modified = True
    
    # Replace input() with empty string
    if 'input(' in code:
        code = re.sub(r'input\([^)]*\)', '""  # Simulated empty input', code)
        modified = True
    
    # Limit recursion by replacing recursive calls with loops (simple cases)
    # This is a heuristic - complex recursion needs LLM
    
    return code if modified else None


def _get_alternative_path(path: str) -> str:
    """Get an alternative file path when the original fails."""
    p = Path(path)
    
    # Try home directory if not already there
    if not str(p).startswith(str(Path.home())):
        return str(Path.home() / p.name)
    
    # Try current directory if not already there
    if p.parent != Path('.') and p.parent != Path.cwd():
        return p.name
    
    # Try temp directory
    return str(Path(tempfile.gettempdir()) / p.name)


def _generate_alternative_query(query: str, goal: str) -> str | None:
    """Generate an alternative search query based on the goal."""
    # Add more specific terms from the goal
    stop_words = {'the', 'a', 'an', 'is', 'to', 'for', 'in', 'of', 'and', 'or', 'it', 'on', 'at', 'by', 'be', 'as', 'with'}
    goal_words = set(goal.lower().split())
    query_words = set(query.lower().split())
    
    # Add relevant goal words not in query
    extra_words = (goal_words - query_words - stop_words)
    
    if extra_words:
        return query + ' ' + ' '.join(sorted(extra_words)[:3])
    
    # Try rephrasing
    if 'how' in query.lower():
        return query.replace('how', 'what is')
    if 'best' in query.lower():
        return query.replace('best', 'top')
    if 'what is' in query.lower():
        return query.replace('what is', 'how to')
    
    # Add "tutorial" or "guide" for learning goals
    if any(kw in goal.lower() for kw in ['learn', 'understand', 'tutorial']):
        return query + ' tutorial'
    
    return None