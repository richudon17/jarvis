"""
state/persistence.py
Handles saving and loading task state to SQLite so JARVIS can resume after interruption.
"""

import json
import sqlite3
import os
from datetime import datetime, timezone
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "jarvis_state.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    # Ensure row_factory always set before any fetches.
    conn.row_factory = sqlite3.Row
    return conn


from contextlib import contextmanager


@contextmanager
def _conn_ctx():
    """Connection context manager that guarantees deterministic close."""
    conn = None
    try:
        conn = _get_conn()
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass





def init_db():
    """Create tables if they don't exist."""
    from contextlib import contextmanager

    with _conn_ctx() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                goal_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT NOT NULL,
                step_index INTEGER,
                description TEXT,
                tool TEXT,
                tool_input TEXT,
                result TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                FOREIGN KEY (goal_id) REFERENCES goals(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT,
                key TEXT,
                value TEXT,
                created_at TEXT
            )
        """)
        conn.commit()



def save_goal(goal_id: str, goal_text: str, status: str = "pending"):
    now = datetime.now(timezone.utc).isoformat()
    with _conn_ctx() as conn:
        conn.execute("""
            INSERT INTO goals (id, goal_text, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at
        """, (goal_id, goal_text, status, now, now))
        conn.commit()



def update_goal_status(goal_id: str, status: str):
    now = datetime.now(timezone.utc).isoformat()
    with _conn_ctx() as conn:
        conn.execute("UPDATE goals SET status=?, updated_at=? WHERE id=?", (status, now, goal_id))
        conn.commit()



def serialize_for_storage(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return json.dumps(str(value))


def deserialize_from_storage(value: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def save_step(goal_id: str, step_index: int, description: str, tool: str,
              tool_input: dict, result: Any, status: str):
    now = datetime.now(timezone.utc).isoformat()
    with _conn_ctx() as conn:
        conn.execute("""
            INSERT INTO steps (goal_id, step_index, description, tool, tool_input, result, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            goal_id,
            step_index,
            description,
            tool,
            serialize_for_storage(tool_input),
            serialize_for_storage(result),
            status,
            now,
        ))
        conn.commit()



def load_goal(goal_id: str) -> Optional[dict]:
    with _conn_ctx() as conn:
        row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return dict(row) if row else None



def load_steps(goal_id: str) -> list:
    with _conn_ctx() as conn:
        rows = conn.execute("SELECT * FROM steps WHERE goal_id=? ORDER BY step_index", (goal_id,)).fetchall()
        steps = []
        for row in rows:
            row_dict = dict(row)
            row_dict["tool_input"] = deserialize_from_storage(row_dict.get("tool_input"))
            row_dict["result"] = deserialize_from_storage(row_dict.get("result"))
            steps.append(row_dict)
        return steps



def list_goals() -> list:
    with _conn_ctx() as conn:
        rows = conn.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def reset_orphaned_goals():
    """Reset any goals left in 'running' status to 'failed' (cleanup on startup)."""
    with _conn_ctx() as conn:
        conn.execute(
            "UPDATE goals SET status='failed' WHERE status='running'"
        )
        conn.commit()
