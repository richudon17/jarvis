"""
memory/memory_manager.py
Three-layer memory system:
- Short-term: current task context (in-memory dict, with SQLite write-through for resume)
- Long-term: persistent facts/preferences (SQLite)
- Episodic: past task histories and strategies (SQLite)
"""

import json
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "aurum_state.db")

# Keys that are worth persisting across restarts (scoped per goal_id)
_PERSISTENT_KEYS = {"goal", "goal_id", "execution_trace_path"}

from contextlib import contextmanager


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn_ctx():
    """Deterministic connection context manager for strict ResourceWarning cleanup."""
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


class ShortTermMemory:
    """
    In-memory store for the current task session.

    Persistent keys (goal, goal_id, execution_trace_path) are written
    through to SQLite under memory_type='short_term' so they survive
    process restarts and can be restored when resuming a goal.
    """

    def __init__(self, goal_id: str | None = None):
        self._store: dict = {}
        self._goal_id = goal_id

        # If a goal_id is provided, restore any previously persisted keys
        if goal_id:
            self._restore(goal_id)

    # ── Public API ──────────────────────────────────────────────────────────

    def set(self, key: str, value):
        self._store[key] = value
        if key in _PERSISTENT_KEYS and self._goal_id:
            self._persist(key, value)

    def get(self, key: str, default=None):
        return self._store.get(key, default)

    def clear(self):
        self._store = {}

    def snapshot(self) -> dict:
        return dict(self._store)

    def bind_goal(self, goal_id: str) -> None:
        """
        Bind this instance to a goal_id after construction.
        Called by the orchestrator once goal_id is known so that
        subsequent set() calls write through to SQLite.
        """
        self._goal_id = goal_id
        self._restore(goal_id)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _persist(self, key: str, value) -> None:
        """Write a single key through to SQLite, scoped to the active goal_id."""
        now = datetime.now(timezone.utc).isoformat()
        scoped_key = f"{self._goal_id}:{key}"
        serialized = json.dumps(value) if not isinstance(value, str) else value
        try:
            with _conn_ctx() as conn:
                # Upsert: one row per scoped key, always latest value
                conn.execute("""
                    INSERT INTO memory (memory_type, key, value, created_at)
                    VALUES ('short_term', ?, ?, ?)
                    ON CONFLICT(memory_type, key) DO UPDATE
                        SET value = excluded.value,
                            created_at = excluded.created_at
                """, (scoped_key, serialized, now))
                conn.commit()
        except Exception:
            # Never let persistence failures crash the agent
            pass

    def _restore(self, goal_id: str) -> None:
        """Load persisted short-term keys for this goal_id back into the in-memory store."""
        try:
            with _conn_ctx() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM memory WHERE memory_type='short_term' AND key LIKE ?",
                    (f"{goal_id}:%",)
                ).fetchall()
                for row in rows:
                    # Strip the goal_id: prefix to get the original key
                    raw_key = row["key"][len(goal_id) + 1:]
                    if raw_key in _PERSISTENT_KEYS:
                        try:
                            self._store[raw_key] = json.loads(row["value"])
                        except (json.JSONDecodeError, TypeError):
                            self._store[raw_key] = row["value"]
        except Exception:
            pass


class LongTermMemory:
    """Persistent key-value facts about the user and environment."""

    def set(self, key: str, value):
        now = datetime.now(timezone.utc).isoformat()
        with _conn_ctx() as conn:
            conn.execute("""
                INSERT INTO memory (memory_type, key, value, created_at)
                VALUES ('long_term', ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, (key, json.dumps(value), now))
            conn.commit()

    def get(self, key: str, default=None):
        with _conn_ctx() as conn:
            row = conn.execute(
                "SELECT value FROM memory WHERE memory_type='long_term' AND key=? ORDER BY id DESC LIMIT 1",
                (key,)
            ).fetchone()
            return json.loads(row["value"]) if row else default

    def all(self) -> dict:
        with _conn_ctx() as conn:
            rows = conn.execute(
                "SELECT key, value FROM memory WHERE memory_type='long_term'"
            ).fetchall()
            return {r["key"]: json.loads(r["value"]) for r in rows}


class EpisodicMemory:
    """Stores summaries of completed tasks and what strategies worked."""

    def record(self, goal_id: str, goal_text: str, outcome: str, strategy_notes: str):
        now = datetime.now(timezone.utc).isoformat()
        entry = json.dumps({
            "goal_id": goal_id,
            "goal_text": goal_text,
            "outcome": outcome,
            "strategy_notes": strategy_notes
        })
        with _conn_ctx() as conn:
            conn.execute("""
                INSERT INTO memory (memory_type, key, value, created_at)
                VALUES ('episodic', ?, ?, ?)
            """, (goal_id, entry, now))
            conn.commit()

    def recall_recent(self, limit: int = 5) -> list:
        with _conn_ctx() as conn:
            rows = conn.execute(
                "SELECT value FROM memory WHERE memory_type='episodic' ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [json.loads(r["value"]) for r in rows]


class MemoryManager:
    """Unified interface for all three memory layers."""

    def __init__(self):
        self.short = ShortTermMemory()
        self.long = LongTermMemory()
        self.episodic = EpisodicMemory()