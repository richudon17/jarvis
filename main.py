"""
main.py
AURUM — Persistent Goal-Driven Autonomous Agent
Entry point. Loads env, starts the CLI loop.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
valid_groq = GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here"

if not valid_groq:
    print("❌ No valid GROQ_API_KEY set. Copy .env.example to .env and add your key.")
    print("   Get a free key at: https://console.groq.com")
    sys.exit(1)

from rich.console import Console
from rich.table import Table
from core.orchestrator import Orchestrator
from interface.goal_input import prompt_goal, show_history
from state.persistence import (
    init_db,
    list_interrupted_goals,
    load_steps,
    abandon_goal,
    reset_orphaned_goals,
)

console = Console()


def prompt_resume() -> tuple[str | None, str | None, list | None]:
    """
    Check for interrupted goals and ask the user what to do.

    Returns (goal_text, goal_id, steps_to_resume) if resuming,
    or (None, None, None) if skipping / nothing to resume.
    """
    init_db()
    interrupted = list_interrupted_goals()
    if not interrupted:
        return None, None, None

    console.print("\n[bold yellow]⚠  Interrupted goals found[/bold yellow]")

    table = Table(show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Goal", style="white")
    table.add_column("Last active")

    for i, g in enumerate(interrupted, 1):
        table.add_row(
            str(i),
            g["id"],
            g["goal_text"][:60] + ("..." if len(g["goal_text"]) > 60 else ""),
            g["updated_at"][:16] if g["updated_at"] else "",
        )

    console.print(table)
    console.print("[dim]Enter a number to resume, or press Enter to skip all.[/dim]\n")

    try:
        choice = input("Resume > ").strip()
    except (KeyboardInterrupt, EOFError):
        choice = ""

    if not choice:
        # User skipped — mark all as abandoned so they don't show again
        for g in interrupted:
            abandon_goal(g["id"])
        console.print("[dim]Skipped. Starting fresh.[/dim]\n")
        return None, None, None

    try:
        index = int(choice) - 1
        if not (0 <= index < len(interrupted)):
            raise ValueError
    except ValueError:
        console.print("[dim]Invalid choice. Starting fresh.[/dim]\n")
        for g in interrupted:
            abandon_goal(g["id"])
        return None, None, None

    chosen = interrupted[index]

    # Abandon the rest
    for i, g in enumerate(interrupted):
        if i != index:
            abandon_goal(g["id"])

    steps = load_steps(chosen["id"])
    completed_steps = [s for s in steps if s.get("status") == "success"]

    console.print(
        f"\n[bold green]Resuming:[/bold green] {chosen['goal_text'][:60]}"
        f"\n[dim]{len(completed_steps)} completed step(s) will be restored.[/dim]\n"
    )

    return chosen["goal_text"], chosen["id"], steps


def main():
    aurum_logo = """
[#e5c07b]      █████╗ [/#e5c07b][#d4af37]██╗   ██╗[/#d4af37][#bfa046]██████╗ [/#bfa046][#9a8c3c]██╗   ██╗[/#9a8c3c][#6b8f47]███╗   ███╗[/#6b8f47]
[#e5c07b]     ██╔══██╗[/#e5c07b][#d4af37]██║   ██║[/#d4af37][#bfa046]██╔══██╗[/#bfa046][#9a8c3c]██║   ██║[/#9a8c3c][#6b8f47]████╗ ████║[/#6b8f47]
[#e5c07b]     ███████║[/#e5c07b][#d4af37]██║   ██║[/#d4af37][#bfa046]██████╔╝[/#bfa046][#9a8c3c]██║   ██║[/#9a8c3c][#6b8f47]██╔████╔██║[/#6b8f47]
[#c9a227]     ██╔══██║[/#c9a227][#a88c2c]██║   ██║[/#a88c2c][#8c7a2f]██╔══██╗[/#8c7a2f][#5f7f3f]██║   ██║[/#5f7f3f][#3f6b35]██║╚██╔╝██║[/#3f6b35]
[#b8931f]     ██║  ██║[/#b8931f][#8f7a22]╚██████╔╝[/#8f7a22][#6f7429]██║  ██║[/#6f7429][#4f6f37]╚██████╔╝[/#4f6f37][#2f5f2f]██║ ╚═╝ ██║[/#2f5f2f]
[#8b6f1a]     ╚═╝  ╚═╝[/#8b6f1a][#6f6a24] ╚═════╝ [/#6f6a24][#4f6930]╚═╝  ╚═╝ [/#4f6930][#2e5a2e] ╚═════╝ [/#2e5a2e][#1f4d1f]╚═╝     ╚═╝[/#1f4d1f]

                 [bold #d4af37]✦ A U R U M ✦[/bold #d4af37]
"""
    console.print(aurum_logo)

    # Check for interrupted goals before initialising the orchestrator
    # (Orchestrator.__init__ would wipe them via reset_orphaned_goals)
    resume_goal, resume_goal_id, resume_steps = prompt_resume()

    # skip_orphan_reset=True because we've already handled interrupted goals above
    agent = Orchestrator(skip_orphan_reset=True)

    # If the user chose to resume, kick it off immediately
    if resume_goal:
        try:
            result = agent.run(resume_goal, goal_id=resume_goal_id, resume_from_steps=resume_steps)
            console.print(f"\n[bold]Final Result:[/bold]\n{result}\n")
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted.[/dim]")
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")

    while True:
        try:
            goal = prompt_goal()

            if not goal:
                continue
            elif goal.lower() in ("quit", "exit", "q"):
                console.print("[dim]Goodbye.[/dim]")
                break
            elif goal.lower() == "history":
                show_history()
            else:
                result = agent.run(goal)
                console.print(f"\n[bold]Final Result:[/bold]\n{result}\n")

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted. Type 'quit' to exit.[/dim]")
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")


if __name__ == "__main__":
    main()
