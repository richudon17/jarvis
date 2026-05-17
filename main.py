"""
main.py
JARVIS — Persistent Goal-Driven Autonomous Agent
Entry point. Loads env, starts the CLI loop.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
valid_groq = GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here"

# Validate API key
if not valid_groq:
    print("❌ No valid GROQ_API_KEY set. Copy .env.example to .env and add your key.")
    print("   Get a free key at: https://console.groq.com")
    sys.exit(1)

from rich.console import Console
from core.orchestrator import Orchestrator
from interface.goal_input import prompt_goal, show_history

console = Console()


def main():
    console.print("""
[bold cyan]
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
[/bold cyan]
[dim]Persistent Goal-Driven Autonomous Agent — Phase 1[/dim]
""")

    agent = Orchestrator()

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
