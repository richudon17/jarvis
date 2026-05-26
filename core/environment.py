"""
core/environment.py
Scans the active task workspace and returns a summary for the planner.
"""

from __future__ import annotations

from pathlib import Path

from core.workspace import goal_workspace_dir, get_active_goal_id

IGNORE = {".venv", "__pycache__", ".git", ".pytest_cache", "node_modules"}
MAX_FILES = 30


def scan_environment(directory: str | None = None, goal_id: str | None = None) -> dict:
    """Scan the active task workspace and return a structured summary."""
    if directory is not None and directory not in ("", "."):
        root = Path(directory).expanduser().resolve()
    else:
        root = goal_workspace_dir(goal_id or get_active_goal_id())

    files = []

    for entry in root.rglob("*"):
        if any(p in entry.parts for p in IGNORE):
            continue
        if entry.is_file():
            rel = str(entry.relative_to(root))
            size = entry.stat().st_size
            files.append({"path": rel, "size": size})
        if len(files) >= MAX_FILES:
            break

    return {
        "working_directory": str(root),
        "file_count": len(files),
        "files": files,
    }


def environment_summary(directory: str | None = None, goal_id: str | None = None) -> str:
    """Return a plain text summary for injection into the planner prompt."""
    env = scan_environment(directory, goal_id=goal_id)
    if not env["files"]:
        return "Working directory is empty."

    lines = [
        f"Working directory: {env['working_directory']}",
        f"Existing files ({env['file_count']}):",
    ]
    for f in env["files"]:
        lines.append(f"  - {f['path']} ({f['size']} bytes)")
    return "\n".join(lines)
