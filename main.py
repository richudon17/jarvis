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
