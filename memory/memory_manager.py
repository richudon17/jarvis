"""
memory/memory_manager.py
Three-layer memory system:
- Short-term: current task context (in-memory dict)
- Long-term: persistent facts/preferences (SQLite)
- Episodic: past task histories and strategies (SQLite)
"""

import json
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "jarvis_state.db")


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
    """In-memory store for the current task session."""

    def __init__(self):
        self._store = {}

    def set(self, key: str, value):
        self._store[key] = value

    def get(self, key: str, default=None):
        return self._store.get(key, default)

    def clear(self):
        self._store = {}

    def snapshot(self) -> dict:
        return dict(self._store)


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
