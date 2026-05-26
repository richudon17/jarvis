"""
core/workspace.py

Workspace isolation utilities for AURUM.

All task file access is scoped to:
./aurum_workspace/<goal_id>/

This module centralizes:
- active goal context
- workspace path resolution
- path traversal / absolute path blocking
- workspace inspection helpers
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Iterable

WORKSPACE_ROOT_NAME = "aurum_workspace"
DEFAULT_GOAL_ID = "global"

_current_goal_id: ContextVar[str] = ContextVar("aurum_goal_id", default=DEFAULT_GOAL_ID)
_current_goal_description: ContextVar[str] = ContextVar("aurum_goal_description", default="")


def workspace_root() -> Path:
    """Return the fixed workspace root, creating it if needed."""
    root = Path.cwd() / WORKSPACE_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def set_execution_context(goal_id: str, goal_description: str = "") -> None:
    """Set the active task context for downstream file and logging operations."""
    _current_goal_id.set((goal_id or DEFAULT_GOAL_ID).strip() or DEFAULT_GOAL_ID)
    _current_goal_description.set(goal_description or "")


def clear_execution_context() -> None:
    """Reset the active task context."""
    _current_goal_id.set(DEFAULT_GOAL_ID)
    _current_goal_description.set("")


def get_active_goal_id() -> str:
    return _current_goal_id.get()


def get_active_goal_description() -> str:
    return _current_goal_description.get()


def goal_workspace_dir(goal_id: str | None = None) -> Path:
    """Return the per-goal workspace directory and ensure it exists."""
    active_goal_id = (goal_id or get_active_goal_id() or DEFAULT_GOAL_ID).strip() or DEFAULT_GOAL_ID
    root = workspace_root()
    goal_dir = root / active_goal_id
    goal_dir.mkdir(parents=True, exist_ok=True)
    return goal_dir.resolve()


def _is_absolute_or_traversal(path: Path) -> bool:
    if path.is_absolute():
        return True
    return any(part == ".." for part in path.parts)


def validate_user_path(user_path: str) -> Path:
    """Validate a user-supplied relative path.

    Rejects:
    - absolute paths
    - path traversal ("..")
    """
    if user_path is None:
        raise ValueError("path is required")

    raw = str(user_path).strip()
    if not raw:
        raise ValueError("path is required")

    candidate = Path(raw)
    if _is_absolute_or_traversal(candidate):
        raise ValueError("path must stay inside the workspace")

    return candidate


def resolve_workspace_path(user_path: str, goal_id: str | None = None) -> Path:
    """Resolve a user path into the active goal workspace.

    Under an active goal context, all paths are rewritten to:
    ./aurum_workspace/<goal_id>/<user_path>

    Outside an active goal context, direct filesystem calls keep legacy
    compatibility for test helpers and utility usage.
    """
    raw = str(user_path).strip()
    if not raw:
        raise ValueError("path is required")

    active_goal_id = (goal_id or get_active_goal_id() or DEFAULT_GOAL_ID).strip() or DEFAULT_GOAL_ID
    if active_goal_id == DEFAULT_GOAL_ID:
        return Path(raw).expanduser().resolve()

    candidate = validate_user_path(raw)
    goal_dir = goal_workspace_dir(active_goal_id)
    resolved = (goal_dir / candidate).resolve()

    root = workspace_root()
    if root not in resolved.parents and resolved != root and goal_dir not in resolved.parents and resolved != goal_dir:
        raise ValueError("path must stay inside the workspace")

    return resolved


def list_workspace_files(goal_id: str | None = None, max_entries: int = 50) -> list[str]:
    """Return relative file paths inside the active workspace folder."""
    active_goal_id = (goal_id or get_active_goal_id() or DEFAULT_GOAL_ID).strip() or DEFAULT_GOAL_ID
    if active_goal_id == DEFAULT_GOAL_ID:
        return []
    goal_dir = goal_workspace_dir(active_goal_id)
    files: list[str] = []

    for entry in goal_dir.rglob("*"):
        if entry.is_file():
            try:
                files.append(str(entry.relative_to(goal_dir)))
            except ValueError:
                continue
        if len(files) >= max_entries:
            break

    return files


def workspace_context_label(goal_id: str | None = None) -> str:
    """Human-readable execution label for logs."""
    active_goal_id = (goal_id or get_active_goal_id() or DEFAULT_GOAL_ID).strip() or DEFAULT_GOAL_ID
    description = get_active_goal_description().strip()
    label = f"ACTIVE TASK: {active_goal_id}"
    if description:
        label += f"\nTASK DESCRIPTION: {description}"
    return label
