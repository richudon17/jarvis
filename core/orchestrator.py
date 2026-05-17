"""
core/orchestrator.py
The brain of JARVIS. Central control loop that:
1. Receives a goal
2. Plans steps
3. Executes each step
4. Evaluates results
5. Replans on failure
6. Persists state throughout
7. Completes or declares failure after limits
"""

import uuid
import re
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

import os

from core.planner import create_plan, replan, _call_llm
from core.executor import run_step
from core.evaluator import evaluate_step, check_loop_detection
from core.smoke_test import smoke_test_python_file
from core.verifier import verify_goal
from core.deterministic_repair import deterministic_repair
from core.quality import review_quality
from core.semantic_verifier import semantic_verify_goal



from memory.memory_manager import MemoryManager
from state.persistence import (
    init_db, save_goal, update_goal_status, save_step, load_steps, reset_orphaned_goals
)

console = Console()

MAX_RETRIES = 3
MAX_STEPS = 20
MAX_REPLAN_ATTEMPTS = 2
MAX_REPAIR_ATTEMPTS = 2
PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
KNOWN_PLACEHOLDERS = ("{search_results}", "{results}", "{output}", "{summary}")

# Patterns that should NEVER be treated as placeholders (f-strings, format strings, etc.)
PLACEHOLDER_EXCLUSIONS = [
    re.compile(r"^\'[^\']*\'$"),  # Single-quoted strings
    re.compile(r'^\"[^\"]*\"$'),  # Double-quoted strings
    re.compile(r"^[^{}]*\{[^{}]+\}[^{}]*$"),  # Strings containing f-string patterns mixed with other text
]

# Maximum context length to prevent memory issues
MAX_CONTEXT_LENGTH = 50000



def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


QUALITY_THRESHOLD = _env_float("JARVIS_QUALITY_THRESHOLD", 0.60)
SEMANTIC_CONFIDENCE_MIN = _env_float("JARVIS_SEMANTIC_CONFIDENCE_MIN", 0.50)



def _previous_context(step_results: dict) -> str:
    """Build context from step results, truncating if necessary."""
    parts = []
    total_length = 0
    
    for i, r in step_results.items():
        part = f"Step {i} result:\n{r}"
        if total_length + len(part) > MAX_CONTEXT_LENGTH:
            # Truncate the last part if we're near the limit
            remaining = MAX_CONTEXT_LENGTH - total_length
            if remaining > 50:  # Only add if there's meaningful space
                parts.append(part[:remaining] + "...[truncated]")
            break
        parts.append(part)
        total_length += len(part)
    
    return "\n\n".join(parts)


def _latest_context(step_results: dict) -> str:
    if not step_results:
        return ""
    return next(reversed(step_results.values()))


def _is_placeholder_value(value: str) -> bool:
    """Check if a value is a placeholder that should be replaced.
    
    Excludes quoted strings and f-string patterns to prevent corruption.
    """
    if not isinstance(value, str):
        return False
    
    stripped = value.strip()
    lowered = stripped.lower()
    
    # Check if it matches any known placeholder
    if any(token in lowered for token in KNOWN_PLACEHOLDERS):
        return True
    
    # Check if it's a pure placeholder pattern (entire string is {something})
    if PLACEHOLDER_PATTERN.fullmatch(stripped) is not None:
        # Exclude if it looks like an f-string variable (short, simple names)
        inner = stripped[1:-1]  # Remove { and }
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', inner):
            # This looks like an f-string variable like {x} or {score}
            # Only treat as placeholder if it's a known one
            return False
        
        return True
    
    return False


def _replace_placeholders(value, previous_context: str, latest_context: str = "", prefer_latest: bool = False):
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
                prefer_latest
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_placeholders(item, previous_context, latest_context, prefer_latest)
            for item in value
        ]
    return value


def _resolve_step_placeholders(
    step: dict,
    previous_context: str,
    latest_context: str = "",
    previous_tool: str = ""
) -> dict:
    tool_input = step.get("tool_input", {})
    prefer_latest = step.get("tool") == "file_write" and previous_tool == "summarize_text"
    if (
        step.get("tool") == "file_write"
        and str(tool_input.get("path", "")).endswith(".py")
        and "content" in tool_input
    ):
        return {
            key: value if key == "content" else _replace_placeholders(value, previous_context, latest_context, prefer_latest)
            for key, value in tool_input.items()
        }
    return _replace_placeholders(tool_input, previous_context, latest_context, prefer_latest)


class Orchestrator:
    def __init__(self):
        reset_orphaned_goals()
        init_db()
        self.memory = MemoryManager()

    def run(self, goal: str, goal_id: str = None) -> str:
        """Main entry point. Accepts a goal string, runs until complete or exhausted."""
        goal_id = goal_id or str(uuid.uuid4())[:8]

        console.print(Panel(f"[bold cyan]🤖 JARVIS[/bold cyan]\n[white]{goal}[/white]",
                            title="New Goal", border_style="cyan"))

        # Persist the goal
        save_goal(goal_id, goal, status="running")
        self.memory.short.set("goal", goal)
        self.memory.short.set("goal_id", goal_id)

        # Generate initial plan
        console.print("\n[yellow]⚙  Planning...[/yellow]")
        plan = create_plan(goal, memory=self.memory)
        steps = plan.get("steps", [])
        console.print(f"[green]✓ Plan ready:[/green] {plan.get('plan_summary', '')}")
        for s in steps:
            console.print(f"  [dim]{s['step_index']}. {s['description']} → [{s['tool']}][/dim]")

        completed = []
        attempted = []
        step_results = {}
        retry_count = 0
        replan_count = 0
        repair_count = 0
        total_steps = 0
        terminal_success = False
        stop_reason = ""

        # ── Main execution loop ──
        while steps and total_steps < MAX_STEPS:
            step = steps.pop(0)
            total_steps += 1
            step["tool_input"] = _resolve_step_placeholders(
                step,
                _previous_context(step_results),
                _latest_context(step_results),
                completed[-1].get("tool", "") if completed else ""
            )

            # Loop detection
            if check_loop_detection(attempted, step):
                stop_reason = f"Loop detected on step {step['step_index']}."
                console.print(f"[red]⚠  {stop_reason} Stopping.[/red]")
                break

            console.print(f"\n[bold]→ Step {step['step_index']}:[/bold] {step['description']}")
            console.print(f"  [dim]Tool: {step['tool']} | Input: {step['tool_input']}[/dim]")

            # Execute
            executed = run_step(step)

            # Smoke test Python artifacts when they are written.
            if step.get("tool") == "file_write" and str(step.get("tool_input", {}).get("path", "")).endswith(".py") and executed.get("status") == "success":
                smoke = smoke_test_python_file(step.get("tool_input", {}).get("path", ""))
                executed["result"]["metadata"]["smoke_test"] = smoke
                if not smoke.get("compiled", False):
                    executed["result"]["ok"] = False
                    executed["result"]["error"] = smoke.get("compile_error") or "Python compile failed during smoke test."
                elif smoke.get("safe_to_execute") and not smoke.get("execution_skipped") and not smoke.get("executed"):
                    executed["result"]["ok"] = False
                    executed["result"]["error"] = (
                        "Python runtime validation failed: "
                        + (smoke.get("stderr") or "execution produced non-zero exit code or timeout")
                    )
                executed["status"] = "success" if executed["result"]["ok"] else "failed"

            # Evaluate
            evaluated = evaluate_step(executed)
            passed = evaluated["evaluation"]["passed"]
            reason = evaluated["evaluation"]["reason"]

            # Deterministic repair before any LLM replanning.
            if not passed:
                # Fingerprint used to detect repeated failures.
                failure_fingerprint = f"{step.get('tool')}|{step.get('tool_input')}|{(executed.get('result') or {}).get('error')}"
                # Count previous identical fingerprints in attempted steps.
                repeat_count = 0
                for prev in attempted:
                    if prev.get('tool') == step.get('tool') and prev.get('tool_input') == step.get('tool_input'):
                        prev_err = (prev.get('result') or {}).get('error') if isinstance(prev.get('result'), dict) else None
                        cur_err = (executed.get('result') or {}).get('error') if isinstance(executed.get('result'), dict) else None
                        if prev_err == cur_err:
                            repeat_count += 1
                previous_fingerprint = failure_fingerprint if repeat_count >= 1 else None

                repair = deterministic_repair(
                    step=step,
                    executed_step=executed,
                    goal=goal,
                    completed_steps=completed,
                    attempted_steps=attempted,
                    previous_failure_fingerprint=previous_fingerprint,
                )

                if repair.get("handled") and repair.get("action") in ("retry", "convert"):
                    repair_count += 1
                    if repair_count > MAX_REPAIR_ATTEMPTS:
                        stop_reason = "Maximum repair attempts reached."
                        update_goal_status(goal_id, "failed")
                        self.memory.episodic.record(goal_id, goal, "failed", stop_reason)
                        return f"JARVIS failed due to repeated repair attempts: {stop_reason}"

                    new_steps = repair.get("new_steps") or []
                    if new_steps:
                        # Execute repaired steps immediately (bounded to at most one repair step here).
                        for ns in new_steps:
                            ns = dict(ns)
                            ns.setdefault("tool_input", ns.get("tool_input", {}) or {})
                            ns_exec = run_step(ns)
                            ns_eval = evaluate_step(ns_exec)
                            attempted.append(ns_eval)
                            # Persist repaired step
                            save_step(
                                goal_id=goal_id,
                                step_index=ns.get("step_index", step.get("step_index")),
                                description=ns.get("description", step.get("description")),
                                tool=ns.get("tool", step.get("tool")),
                                tool_input=ns.get("tool_input", {}),
                                result=ns_exec.get("result"),
                                status=ns_exec.get("status", "unknown"),
                            )

                            if ns_exec.get("status") == "success":
                                passed = True
                                executed = ns_exec
                                evaluated = ns_eval
                                reason = ns_eval["evaluation"]["reason"]
                            else:
                                passed = False
                                reason = ns_eval["evaluation"]["reason"]

                    if passed:
                        pass
                    else:
                        pass

                elif repair.get("handled") and repair.get("action") == "stop":
                    stop_reason = repair.get("reason") or "Deterministic repair stopped the plan."
                    update_goal_status(goal_id, "failed")
                    self.memory.episodic.record(goal_id, goal, "failed", stop_reason)
                    return f"JARVIS failed to complete the goal: {stop_reason}"

            attempted.append(evaluated)

            # Print result summary
            result_preview = str(executed.get("result", ""))[:300]
            if passed:
                console.print(f"  [green]✓ {reason}[/green]")
                console.print(f"  [dim]{result_preview}[/dim]")
                # Preserve structured result for verifier and placeholder context.
                step_results[step["step_index"]] = executed.get("result", {})

            else:
                console.print(f"  [red]✗ {reason}[/red]")

            # Persist step
            save_step(
                goal_id=goal_id,
                step_index=step["step_index"],
                description=step["description"],
                tool=step["tool"],
                tool_input=step.get("tool_input", {}),
                result=executed.get("result", {}),

                status=executed.get("status", "unknown")
            )

            # Handle terminal step
            if executed.get("status") == "done":
                verification = verify_goal(goal, completed, attempted)
                if verification["passed"]:
                    quality = review_quality(goal, completed_steps=completed, attempted_steps=attempted)
                    if quality.get("passed") or quality.get("score", 0.0) >= QUALITY_THRESHOLD:
                        semantic = semantic_verify_goal(goal, completed_steps=completed, attempted_steps=attempted)
                        if semantic.get("passed") and float(semantic.get("confidence", 0.0)) >= SEMANTIC_CONFIDENCE_MIN:
                            terminal_success = True

                        console.print(
                            f"  [green]✓ Goal verified:[/green] {verification['reason']} "
                            f"[dim](confidence {verification['confidence']:.2f})[/dim]"
                        )
                        console.print(
                            f"  [green]✓ Quality passed:[/green] "
                            f"score {quality.get('score', 0.0):.2f} "
                            f"[dim]({quality.get('category', 'unknown')})[/dim]"
                        )
                        console.print("\n[bold green]✅ Goal complete![/bold green]")
                        update_goal_status(goal_id, "completed")
                        self.memory.episodic.record(goal_id, goal, "success", plan.get("plan_summary", ""))
                        return executed.get("result", "Done.")

                    issues = "; ".join(quality.get("issues") or ["quality score below threshold"])
                    reason = (
                        f"Goal quality failed: score {quality.get('score', 0.0):.2f} "
                        f"below {QUALITY_THRESHOLD:.2f}. {issues}"
                    )
                else:
                    reason = f"Goal verification failed: {verification['reason']}"
                console.print(f"  [red]✗ {reason}[/red]")
                retry_count += 1
                if retry_count > MAX_RETRIES or replan_count >= MAX_REPLAN_ATTEMPTS:
                    stop_reason = f"Maximum retries/replans reached during verification after {retry_count} failures."
                    console.print(f"\n[red]❌ {stop_reason}[/red]")
                    update_goal_status(goal_id, "failed")
                    self.memory.episodic.record(goal_id, goal, "failed", reason)
                    return f"JARVIS failed to complete the goal after verification retries. Last error: {reason}"

                replan_count += 1
                console.print(f"\n[yellow]↺ Replanning after verification failure (attempt {replan_count}/{MAX_REPLAN_ATTEMPTS})...[/yellow]")
                new_plan = replan(goal, completed, evaluated, reason, memory=self.memory)
                steps = new_plan.get("steps", [])
                if not steps:
                    stop_reason = f"Replan produced no steps after verification failure: {reason}"
                    console.print("[red]Replan produced no steps. Stopping.[/red]")
                    break
                continue

            # Handle failure with replan
            if not passed:
                retry_count += 1
                if retry_count > MAX_RETRIES or replan_count >= MAX_REPLAN_ATTEMPTS:
                    stop_reason = f"Maximum retries/replans reached after {retry_count} failures."
                    console.print(f"\n[red]❌ {stop_reason}[/red]")
                    update_goal_status(goal_id, "failed")
                    self.memory.episodic.record(goal_id, goal, "failed", stop_reason)
                    return f"JARVIS failed to complete the goal after repeated retries. Last error: {reason}"

                replan_count += 1
                console.print(f"\n[yellow]↺ Replanning (attempt {replan_count}/{MAX_REPLAN_ATTEMPTS})...[/yellow]")
                new_plan = replan(goal, completed, step, reason, memory=self.memory)
                steps = new_plan.get("steps", [])
                if not steps:
                    stop_reason = f"Replan produced no steps after failure: {reason}"
                    console.print("[red]Replan produced no steps. Stopping.[/red]")
                    break
            else:
                completed.append(evaluated)
                retry_count = 0  # Reset on success

        # Exhausted steps
        if total_steps >= MAX_STEPS:
            stop_reason = f"Hit maximum step limit ({MAX_STEPS})."
            console.print(f"\n[red]⚠  {stop_reason}[/red]")

        if not terminal_success:
            if not stop_reason:
                stop_reason = "Plan ended without a done step."
            update_goal_status(goal_id, "failed")
            self.memory.episodic.record(goal_id, goal, "failed", stop_reason)
            console.print(f"\n[red]❌ Goal not completed: {stop_reason}[/red]")
            return f"JARVIS failed to complete the goal: {stop_reason}"

        update_goal_status(goal_id, "completed")
        
        # Generate LLM summary instead of hardcoded message
        completed_summary = "\n".join([
            f"Step {i+1}: {s.get('description')} - {str(s.get('result', ''))[:100]}"
            for i, s in enumerate(completed)

        ])
        
        summary_prompt = f"""The following steps were completed for this goal: {goal}

Steps and results:
{completed_summary}

Write one clear paragraph summarizing what was accomplished."""
        
        try:
            summary = _call_llm(summary_prompt, "You are a helpful summarizer of task outcomes.")
        except Exception as e:
            summary = f"Completed {len(completed)} steps for goal: {goal}"
        
        self.memory.episodic.record(goal_id, goal, "completed", summary)
        console.print(f"\n[green]✅ Done. Executed {len(completed)} steps.[/green]")
        return summary
