"""Core orchestration loop for AURUM.

Decision model:
- `core/quality.py` is the only authority for final completion.
- evaluator/verifier/semantic verifier are observation/advisory only.
- deterministic repair may suggest step fixes but does not decide completion.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from core.workspace import (
    clear_execution_context,
    list_workspace_files,
    set_execution_context,
    workspace_context_label,
    goal_workspace_dir,
)
from core.planner import create_plan, replan
from core.executor import run_step
from core.evaluator import evaluate_step, check_loop_detection
from core.smoke_test import smoke_test_python_file
from core.deterministic_repair import deterministic_repair
from core.verifier import verify_goal
from core.semantic_verifier import semantic_verify_goal
from core.quality import review_quality

from memory.memory_manager import MemoryManager
from state.persistence import (
    init_db,
    save_goal,
    update_goal_status,
    save_step,
    reset_orphaned_goals,
)

console = Console()

MAX_STEPS = 20
MAX_REPLAN_ATTEMPTS = 2
PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
KNOWN_PLACEHOLDERS = ("{search_results}", "{results}", "{output}", "{summary}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_safe(value: Any):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _trace_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trace_safe(v) for v in value]
    return repr(value)


def _append_trace(events: list[dict[str, Any]], event_type: str, **payload) -> None:
    event = {
        "event_index": len(events),
        "event_type": event_type,
        "timestamp": _utc_now(),
    }
    event.update(_trace_safe(payload))
    events.append(event)


def _is_placeholder_value(value: str) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    lowered = stripped.lower()

    if any(token in lowered for token in KNOWN_PLACEHOLDERS):
        return True

    if PLACEHOLDER_PATTERN.fullmatch(stripped) is None:
        return False

    inner = stripped[1:-1]
    # Exclude simple variable tokens such as "{x}" or "{score}".
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", inner):
        return False
    return True


def _replace_placeholders(
    value,
    previous_context: str,
    latest_context: str = "",
    prefer_latest: bool = False,
):
    if not previous_context:
        return value
    if isinstance(value, str) and _is_placeholder_value(value):
        if prefer_latest and latest_context:
            return latest_context
        return previous_context
    if isinstance(value, dict):
        return {
            key: item if key in ("path", "directory") else _replace_placeholders(
                item,
                previous_context,
                latest_context,
                prefer_latest,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_placeholders(item, previous_context, latest_context, prefer_latest)
            for item in value
        ]
    return value


def _latest_completed_output(completed_steps: list[dict]) -> str:
    for completed in reversed(completed_steps):
        result = completed.get("result") or {}
        if not isinstance(result, dict):
            continue
        data = result.get("data") or {}
        if isinstance(data, dict):
            for key in ("content", "stdout", "summary"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(data, str) and data.strip():
            return data.strip()
    return ""


def _failure_fingerprint(step: dict, observed: dict) -> str:
    tool = step.get("tool", "")
    tool_input = str(step.get("tool_input", {}))
    issues = "|".join((observed.get("observation", {}) or {}).get("issues", []))
    return f"{tool}|{tool_input}|{issues}"


def _quality_failure_reason(quality: dict, advisory: dict | None = None) -> str:
    issues = quality.get("issues", []) or []
    if issues:
        return f"Quality check failed: {', '.join(str(i) for i in issues)}"

    hints: list[str] = []
    if advisory:
        for key in ("verify_hints", "semantic_hints"):
            hints.extend(advisory.get(key, []) or [])
    if hints:
        return f"Quality check failed; advisory hints: {', '.join(hints[:3])}"

    return "Quality check failed."


class Orchestrator:
    def __init__(self):
        reset_orphaned_goals()
        init_db()
        self.memory = MemoryManager()

    def run(self, goal: str, goal_id: str | None = None) -> str:
        goal_id = goal_id or str(uuid.uuid4())[:8]
        set_execution_context(goal_id, goal)
        goal_dir = goal_workspace_dir(goal_id)

        state = {
            "completed": [],
            "attempted": [],
            "step_results": {},
            "replan_count": 0,
            "last_quality_failure": None,
            "failure_fingerprints": set(),
        }

        trace_events: list[dict[str, Any]] = []
        trace_started_at = _utc_now()
        trace_path = goal_dir / "execution_trace.json"
        trace_flushed = False
        final_status = "running"
        final_reason = ""

        def flush_trace(status: str, reason: str) -> None:
            nonlocal trace_flushed, final_status, final_reason
            final_status = status
            final_reason = reason
            payload = {
                "goal_id": goal_id,
                "goal": goal,
                "trace_started_at": trace_started_at,
                "trace_ended_at": _utc_now(),
                "final_status": final_status,
                "final_reason": final_reason,
                "timeline": trace_events,
            }
            trace_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.memory.short.set("execution_trace_path", str(trace_path))
            trace_flushed = True

        _append_trace(
            trace_events,
            "goal_started",
            goal_id=goal_id,
            goal=goal,
            workspace=str(goal_dir),
        )

        try:
            console.print(Panel(goal, title="Goal"))
            console.print(f"[dim]{workspace_context_label(goal_id)}[/dim]")
            console.print(f"[dim]Workspace: {goal_dir}[/dim]")
            console.print(f"[dim]Workspace files: {list_workspace_files(goal_id)}[/dim]")

            save_goal(goal_id, goal, status="running")
            self.memory.short.set("goal", goal)
            self.memory.short.set("goal_id", goal_id)

            from core.environment import environment_summary

            env_summary = environment_summary(goal_id=goal_id)
            self.memory.short.set("environment", env_summary)
            _append_trace(trace_events, "environment_scanned", summary=env_summary)

            plan = create_plan(goal, memory=self.memory)
            steps = plan.get("steps", [])
            _append_trace(
                trace_events,
                "plan_created",
                plan_summary=plan.get("plan_summary", ""),
                step_count=len(steps),
                step_indexes=[s.get("step_index") for s in steps],
            )

            while steps and len(state["attempted"]) < MAX_STEPS:
                step = steps.pop(0)
                latest_output = _latest_completed_output(state["completed"])
                resolved = _replace_placeholders(step.get("tool_input", {}) or {}, latest_output)
                step = {**step, "tool_input": resolved}
                tool = step.get("tool")

                _append_trace(
                    trace_events,
                    "step_started",
                    step_index=step.get("step_index"),
                    tool=tool,
                    input=resolved,
                )

                console.print(
                    f"[dim]Step {step.get('step_index')}: {step.get('description')} ({tool})[/dim]"
                )
                console.print(f"[dim]Workspace files before step: {list_workspace_files(goal_id)}[/dim]")

                if check_loop_detection(state["attempted"], step):
                    _append_trace(
                        trace_events,
                        "loop_detection_triggered",
                        step_index=step.get("step_index"),
                        tool=tool,
                        input=resolved,
                    )
                    reason = "Loop detected"
                    flush_trace("failed", reason)
                    return self._fail(goal_id, reason, goal)

                executed = run_step(step)

                if tool == "file_write":
                    path = str(resolved.get("path", ""))
                    if path.endswith(".py") and executed.get("status") == "success":
                        smoke = smoke_test_python_file(path)
                        executed.setdefault("result", {})
                        executed["result"].setdefault("metadata", {})
                        executed["result"]["metadata"]["smoke_test"] = smoke
                        _append_trace(
                            trace_events,
                            "python_smoke_test",
                            step_index=step.get("step_index"),
                            path=path,
                            smoke_result=smoke,
                        )

                observed = evaluate_step(executed)

                if (observed.get("observation", {}) or {}).get("issues"):
                    observed["failure_context"] = {
                        "tool": tool,
                        "input": resolved,
                        "issues": observed["observation"]["issues"],
                        "error": (observed.get("result") or {}).get("error"),
                    }

                _append_trace(
                    trace_events,
                    "step_finished",
                    step_index=step.get("step_index"),
                    tool=tool,
                    input=resolved,
                    output=observed.get("result"),
                    status=observed.get("status"),
                    observation=observed.get("observation"),
                )

                state["attempted"].append(observed)

                save_step(
                    goal_id,
                    step["step_index"],
                    step["description"],
                    tool,
                    resolved,
                    observed.get("result", {}),
                    observed.get("status", "unknown"),
                )

                if tool == "done":
                    advisory = self._collect_advisory(goal, state["completed"], state["attempted"])
                    quality = review_quality(goal, state["completed"], state["attempted"])
                    quality_reason = (
                        "quality passed"
                        if quality.get("passed")
                        else _quality_failure_reason(quality, advisory)
                    )

                    _append_trace(
                        trace_events,
                        "quality_decision",
                        passed=quality.get("passed", False),
                        category=quality.get("category"),
                        score=quality.get("score"),
                        issues=quality.get("issues", []),
                        reasoning_trace=quality.get("reasoning_trace", []),
                        failure_factors=quality.get("failure_factors", []),
                        decision_reason=quality_reason,
                        advisory=advisory,
                    )

                    if quality.get("passed"):
                        reason = resolved.get("summary", "done")
                        _append_trace(trace_events, "goal_completed", summary=reason)
                        flush_trace("completed", reason)
                        update_goal_status(goal_id, "completed")
                        self.memory.episodic.record(goal_id, goal, "completed", "done")
                        return f"completed: {reason}"

                    state["last_quality_failure"] = quality_reason
                    if state["replan_count"] < MAX_REPLAN_ATTEMPTS:
                        state["replan_count"] += 1
                        _append_trace(
                            trace_events,
                            "replan_triggered",
                            trigger="quality_failure",
                            attempt=state["replan_count"],
                            reason=quality_reason,
                        )
                        new_plan = replan(
                            goal,
                            state["completed"],
                            observed,
                            quality_reason,
                            memory=self.memory,
                        )
                        steps = new_plan.get("steps", [])
                        _append_trace(
                            trace_events,
                            "replan_result",
                            attempt=state["replan_count"],
                            step_count=len(steps),
                        )
                        continue

                    _append_trace(trace_events, "goal_failed", reason=quality_reason)
                    flush_trace("failed", quality_reason)
                    return self._fail(goal_id, quality_reason, goal)

                if observed.get("status") == "success":
                    state["completed"].append(observed)
                    state["step_results"][step["step_index"]] = observed.get("result", {})
                    continue

                failure_reason = self._issue_summary(observed)
                fingerprint = _failure_fingerprint(step, observed)
                seen = fingerprint in state["failure_fingerprints"]
                state["failure_fingerprints"].add(fingerprint)

                repair = deterministic_repair(
                    step=step,
                    executed_step=executed,
                    goal=goal,
                    completed_steps=state["completed"],
                    attempted_steps=state["attempted"],
                    previous_failure_fingerprint=fingerprint if seen else None,
                )

                if repair.get("handled"):
                    action = repair.get("action", "")
                    _append_trace(
                        trace_events,
                        "deterministic_repair_triggered",
                        step_index=step.get("step_index"),
                        action=action,
                        reason=repair.get("reason"),
                        new_steps_count=len(repair.get("new_steps") or []),
                        repeated_failure=seen,
                    )
                    if action == "stop":
                        failure_reason = str(repair.get("reason") or failure_reason)
                        advisory = self._collect_advisory(goal, state["completed"], state["attempted"])
                        quality = review_quality(goal, state["completed"], state["attempted"])
                        _append_trace(
                            trace_events,
                            "quality_decision",
                            passed=quality.get("passed", False),
                            category=quality.get("category"),
                            score=quality.get("score"),
                            issues=quality.get("issues", []),
                            reasoning_trace=quality.get("reasoning_trace", []),
                            failure_factors=quality.get("failure_factors", []),
                            decision_reason=failure_reason,
                            advisory=advisory,
                        )
                        _append_trace(trace_events, "goal_failed", reason=failure_reason)
                        flush_trace("failed", failure_reason)
                        return self._fail(goal_id, failure_reason, goal)

                    if action == "skip":
                        continue

                    new_steps = repair.get("new_steps") or []
                    if new_steps:
                        steps = new_steps + steps
                        continue

                    failure_reason = str(repair.get("reason") or failure_reason)

                if state["replan_count"] < MAX_REPLAN_ATTEMPTS:
                    state["replan_count"] += 1
                    _append_trace(
                        trace_events,
                        "replan_triggered",
                        trigger="step_failure",
                        attempt=state["replan_count"],
                        reason=failure_reason,
                    )
                    new_plan = replan(
                        goal,
                        state["completed"],
                        observed,
                        failure_reason,
                        memory=self.memory,
                    )
                    steps = new_plan.get("steps", [])
                    _append_trace(
                        trace_events,
                        "replan_result",
                        attempt=state["replan_count"],
                        step_count=len(steps),
                    )
                    continue

                fail_reason = failure_reason or state.get("last_quality_failure") or "Plan exhausted"
                _append_trace(trace_events, "goal_failed", reason=fail_reason)
                flush_trace("failed", fail_reason)
                return self._fail(goal_id, fail_reason, goal)

            fail_reason = state.get("last_quality_failure") or "Plan exhausted"
            _append_trace(trace_events, "goal_failed", reason=fail_reason)
            flush_trace("failed", fail_reason)
            return self._fail(goal_id, fail_reason, goal)
        finally:
            if not trace_flushed:
                _append_trace(
                    trace_events,
                    "trace_finalized_on_exit",
                    status=final_status,
                    reason=final_reason or "orchestrator exited",
                )
                flush_trace(
                    final_status if final_status != "running" else "aborted",
                    final_reason or "orchestrator exited without explicit completion/failure",
                )
            clear_execution_context()

    def _issue_summary(self, observed_step: dict) -> str:
        observation = observed_step.get("observation", {}) or {}
        issues = observation.get("issues", []) or []
        if issues:
            return ", ".join(str(i) for i in issues)
        result = observed_step.get("result", {}) or {}
        if result.get("error"):
            return str(result["error"])
        return "Step execution failed"

    def _collect_advisory(self, goal: str, completed: list[dict], attempted: list[dict]) -> dict:
        verify = verify_goal(goal, completed, attempted_steps=attempted)
        semantic = semantic_verify_goal(goal, completed, attempted_steps=attempted)

        return {
            "verify_status": verify.get("advisory_status"),
            "verify_hints": verify.get("hints", []),
            "verify_confidence": verify.get("confidence"),
            "semantic_status": semantic.get("advisory_status"),
            "semantic_hints": semantic.get("hints", []),
            "semantic_confidence": semantic.get("confidence"),
        }

    def _fail(self, goal_id: str, reason: str, goal: str) -> str:
        update_goal_status(goal_id, "failed")
        self.memory.episodic.record(goal_id, goal, "failed", reason)
        console.print(f"[red]FAILED: {reason}[/red]")
        return f"failed: {reason}"
