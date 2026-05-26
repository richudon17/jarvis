"""
tools/tool_registry.py
All AURUM tools live here.

Phase 1 reliability: tools return structured dicts:
{
  "ok": bool,
  "data": ...,
  "error": str | None,
  "metadata": dict
}
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ddgs import DDGS

from core.workspace import (
    DEFAULT_GOAL_ID,
    get_active_goal_id,
    goal_workspace_dir,
    resolve_workspace_path,
    workspace_root,
)


# ──────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────

def _ok(data, metadata=None):
    meta = metadata or {}
    meta.setdefault("tool_timestamp", datetime.now(timezone.utc).isoformat())
    return {"ok": True, "data": data, "error": None, "metadata": meta}


def _fail(error, metadata=None):
    meta = metadata or {}
    meta.setdefault("tool_timestamp", datetime.now(timezone.utc).isoformat())
    return {"ok": False, "data": None, "error": str(error), "metadata": meta}


# ──────────────────────────────────────────────
# WEB SEARCH
# ──────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web using DuckDuckGo (free, no API key needed)."""
    try:
        results = []
        ddgs = DDGS()
        for r in ddgs.text(query, max_results=max_results):
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
            )
        return _ok(
            results,
            {
                "query": query,
                "requested_max_results": max_results,
                "num_results": len(results),
            },
        )
    except Exception as e:
        return _fail(e, {"query": query})


def summarize_text(text: str, goal: str = "", max_items: int = 5) -> dict:
    """Create a concise Markdown summary from text or web search snippets."""
    try:
        if not text or not text.strip():
            return _fail("no text provided", {"goal": goal})

        entries = []
        blocks = re.split(r"\n---\n|\n\nStep \d+ result:\n", text)
        for block in blocks:
            title_match = re.search(r"Title:\s*(.+)", block)
            url_match = re.search(r"URL:\s*(.+)", block)
            snippet_match = re.search(
                r"Snippet:\s*(.+?)(?=\nTitle:|\nURL:|\Z)", block, re.S
            )
            if title_match or snippet_match:
                title = title_match.group(1).strip() if title_match else "Source"
                url = url_match.group(1).strip() if url_match else ""
                snippet = (
                    snippet_match.group(1).strip().replace("\n", " ")
                    if snippet_match
                    else ""
                )
                entries.append({"title": title, "url": url, "snippet": snippet})

        raw_markers = text.lower().count("title:") + text.lower().count(
            "url:"
        ) + text.lower().count("snippet:")
        looks_like_raw_snippets = raw_markers >= 4

        if not entries:
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            summary_text = " ".join(sentences[:max_items]).strip()
            if len(summary_text) < 80:
                summary_text = text.strip()[:1000]
            summary_md = f"# Summary\n\n{summary_text}".strip()
        else:
            topic = goal.strip() or "Research"
            lines = [f"# {topic}", "", "## Key Findings"]
            for entry in entries[:max_items]:
                snippet = entry["snippet"] or entry["title"]
                lines.append(f"- {snippet}")

            lines.extend(["", "## Sources"])
            for entry in entries[:max_items]:
                if entry["url"]:
                    lines.append(f"- [{entry['title']}]({entry['url']})")
                else:
                    lines.append(f"- {entry['title']}")

            summary_md = "\n".join(lines).strip()

        return _ok(
            {"summary": summary_md},
            {
                "goal": goal,
                "max_items": max_items,
                "looks_like_raw_snippets": looks_like_raw_snippets,
                "summary_length": len(summary_md),
            },
        )
    except Exception as e:
        return _fail(e, {"goal": goal})


# ──────────────────────────────────────────────
# FILE OPERATIONS
# ──────────────────────────────────────────────

def _workspace_metadata(extra: dict | None = None) -> dict:
    goal_id = get_active_goal_id()
    goal_dir = goal_workspace_dir(goal_id)
    data = {
        "goal_id": goal_id,
        "workspace_root": str(workspace_root()),
        "workspace_dir": str(goal_dir),
        "workspace_files": [],
    }
    try:
        data["workspace_files"] = [str(goal_dir / rel) for rel in []]
    except Exception:
        data["workspace_files"] = []
    if extra:
        data.update(extra)
    return data


def _validate_python_source(code: str) -> str | None:
    if not code or not code.strip():
        return "Python syntax validation failed: generated code is empty."
    try:
        ast.parse(code)
    except SyntaxError as e:
        location = (
            f"line {e.lineno}, column {e.offset}" if e.lineno else "unknown location"
        )
        return f"Python syntax validation failed at {location}: {e.msg}"
    return None


def file_read(path: str) -> dict:
    try:
        path_expanded = resolve_workspace_path(path)
        if not path_expanded.exists():
            return _fail("file not found", _workspace_metadata({"path": path}))
        if not path_expanded.is_file():
            return _fail("not a file", _workspace_metadata({"path": path}))

        content = path_expanded.read_text(encoding="utf-8")
        if content is None:
            return _fail("read returned no content", _workspace_metadata({"path": path}))

        return _ok(
            {"content": content},
            _workspace_metadata({"path": path, "bytes_read": len(content.encode("utf-8"))}),
        )
    except Exception as e:
        return _fail(e, _workspace_metadata({"path": path}))


def file_write(path: str, content: str) -> dict:
    try:
        if content is None or not str(content).strip():
            return _fail("no content provided", _workspace_metadata({"path": path}))

        path_expanded = resolve_workspace_path(path)

        if str(path_expanded).endswith(".py"):
            validation_error = _validate_python_source(content)
            if validation_error:
                return _fail(validation_error, _workspace_metadata({"path": path}))

        path_expanded.parent.mkdir(parents=True, exist_ok=True)
        path_expanded.write_text(content, encoding="utf-8")

        if not path_expanded.is_file():
            return _fail("file was not created", _workspace_metadata({"path": path}))

        bytes_written = path_expanded.stat().st_size
        if bytes_written == 0:
            return _fail("file is empty after writing", _workspace_metadata({"path": path}))

        return _ok(
            {"path": str(path_expanded), "bytes_written": bytes_written},
            _workspace_metadata(
                {
                    "path": path,
                    "resolved_path": str(path_expanded),
                    "bytes_written": bytes_written,
                    "python_syntax_valid": str(path_expanded).endswith(".py"),
                }
            ),
        )
    except Exception as e:
        return _fail(e, _workspace_metadata({"path": path}))


def file_list(directory: str = ".") -> dict:
    try:
        directory_expanded = resolve_workspace_path(directory)
        if not directory_expanded.is_dir():
            return _fail("not a directory", _workspace_metadata({"directory": directory}))
        entries = os.listdir(directory_expanded)
        return _ok(
            {"entries": entries},
            _workspace_metadata({"directory": directory, "num_entries": len(entries)}),
        )
    except Exception as e:
        return _fail(e, _workspace_metadata({"directory": directory}))


# ──────────────────────────────────────────────
# CODE EXECUTION
# ──────────────────────────────────────────────

def _looks_interactive_or_gui(code: str) -> tuple[bool, str | None]:
    blocked_patterns = {
        "input()": ["input("],
        "pygame": ["import pygame", "from pygame", "pygame.display"],
        "tkinter": ["import tkinter", "from tkinter"],
        "turtle": ["import turtle", "from turtle", "Turtle"],
        "mainloop": [".mainloop("],
        "infinite loop": ["while true:", "while True:"],
    }
    lower = code.lower()
    for reason, patterns in blocked_patterns.items():
        for p in patterns:
            if p.lower() in lower:
                return True, reason
    return False, None


def run_python(code: str) -> dict:
    """Execute Python code in a subprocess and return structured output."""
    tmpfile = None
    try:
        if code is None:
            return _fail("no code", _workspace_metadata())

        blocked, reason = _looks_interactive_or_gui(code)
        if blocked:
            return _fail(
                "execution blocked", _workspace_metadata({"blocked_reason": reason, "timeout": False})
            )

        validation_error = _validate_python_source(code)
        if validation_error:
            return _fail(validation_error, _workspace_metadata())

        goal_dir = goal_workspace_dir(get_active_goal_id())
        with tempfile.NamedTemporaryFile(
            suffix=".py",
            mode="w",
            delete=False,
            dir=str(goal_dir),
        ) as f:
            f.write(code)
            tmpfile = f.name

        try:
            result = subprocess.run(
                ["python3", tmpfile],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(goal_dir),
            )
        finally:
            if tmpfile:
                try:
                    os.unlink(tmpfile)
                except Exception:
                    pass

        return _ok(
            {
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "exit_code": result.returncode,
            },
            _workspace_metadata(
                {
                    "goal_id": get_active_goal_id(),
                    "tmpfile": tmpfile,
                    "exit_code": result.returncode,
                    "timeout": False,
                    "stdout_len": len((result.stdout or "").encode("utf-8")),
                    "stderr_len": len((result.stderr or "").encode("utf-8")),
                }
            ),
        )
    except subprocess.TimeoutExpired:
        return _fail("timeout", _workspace_metadata({"timeout": True}))
    except Exception as e:
        return _fail(e, _workspace_metadata())


# ──────────────────────────────────────────────
# TOOL REGISTRY
# ──────────────────────────────────────────────

TOOLS = {
    "web_search": {
        "fn": web_search,
        "description": "Search the web for information. Input: {'query': str}",
        "params": ["query"],
    },
    "summarize_text": {
        "fn": summarize_text,
        "description": "Summarize text or web search snippets into concise Markdown. Input: {'text': str, 'goal': str}",
        "params": ["text", "goal"],
    },
    "file_read": {
        "fn": file_read,
        "description": "Read contents of a file. Input: {'path': str}",
        "params": ["path"],
    },
    "file_write": {
        "fn": file_write,
        "description": "Write text to a file. Input: {'path': str, 'content': str}",
        "params": ["path", "content"],
    },
    "file_list": {
        "fn": file_list,
        "description": "List files in a directory. Input: {'directory': str}",
        "params": ["directory"],
    },
    "run_python": {
        "fn": run_python,
        "description": "Execute Python code and return stdout/stderr/exit_code. Input: {'code': str}",
        "params": ["code"],
    },
}


def get_tool_descriptions() -> str:
    """Return a formatted string of all available tools for the planner prompt."""
    lines = []
    for name, meta in TOOLS.items():
        lines.append(f"- {name}: {meta['description']}")
    
    # Add browser tools if available
    try:
        from core.browser import get_browser_tool_descriptions
        lines.append(get_browser_tool_descriptions())
    except ImportError:
        pass
    
    return "\n".join(lines)


def execute_tool(tool_name: str, params: dict) -> dict:
    """Call a tool by name with given params."""
    # Check standard tools first
    if tool_name in TOOLS:
        try:
            fn = TOOLS[tool_name]["fn"]
            return fn(**params)
        except TypeError as e:
            return _fail(e, {"tool": tool_name, "params": params})
        except Exception as e:
            return _fail(e, {"tool": tool_name, "params": params})
    
    # Check browser tools
    try:
        from core.browser import execute_browser_tool
        browser_tools = ["browser_open", "browser_click", "browser_type", "browser_extract", 
                         "browser_screenshot", "browser_wait", "browser_back", "browser_forward"]
        if tool_name in browser_tools:
            return execute_browser_tool(tool_name, params)
    except ImportError:
        pass
    
    return _fail(
        "unknown tool",
        {"tool": tool_name, "available": list(TOOLS.keys())},
    )
