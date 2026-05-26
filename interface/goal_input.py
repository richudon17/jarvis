"""
interface/goal_input.py
Simple CLI interface for submitting goals to AURUM and viewing history.
"""

from rich.console import Console
from rich.table import Table
from state.persistence import list_goals, load_steps

console = Console()


def prompt_goal() -> str:
    console.print("\n[bold cyan]AURUM[/bold cyan] [dim]— Autonomous Agent[/dim]")
    console.print("[dim]Type your goal, or 'history' to see past tasks, or 'quit' to exit.[/dim]")
    console.print("[dim]For multiline goals, end your first line with \\ then keep typing. Type END to submit.[/dim]\n")
    
    first_line = input("Goal > ").strip()
    
    if first_line.lower() in ("quit", "exit", "q", "history", ""):
        return first_line
    
    # Only go multiline if user ends line with backslash
    if not first_line.endswith("\\"):
        return first_line
    
    lines = [first_line.rstrip("\\")]
    while True:
        line = input("... ").strip()
        if line.upper() == "END":
            break
        lines.append(line.rstrip("\\"))
    
    return "\n".join(lines).strip()


def show_history():
    """Display past goals and their statuses."""
    goals = list_goals()
    if not goals:
        console.print("[dim]No past goals found.[/dim]")
        return

    table = Table(title="Past Goals", show_lines=True)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Goal", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Created")

    for g in goals:
        status_color = {
            "completed": "green",
            "failed": "red",
            "running": "yellow",
            "pending": "dim"
        }.get(g["status"], "white")

        table.add_row(
            g["id"],
            g["goal_text"][:60] + ("..." if len(g["goal_text"]) > 60 else ""),
            f"[{status_color}]{g['status']}[/{status_color}]",
            g["created_at"][:16] if g["created_at"] else ""
        )

    console.print(table)
