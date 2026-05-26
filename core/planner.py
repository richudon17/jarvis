"""
core/planner.py
LLM-based planner. Takes a goal and context, returns a structured list of steps.
Each step specifies: description, tool to use, and tool parameters.
"""

import json
import os
import re
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Phase 1 tests may import core.orchestrator/core.planner without an LLM.
# Only fail fast when the planner is actually used.
if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
    GROQ_API_KEY = ""


from tools.tool_registry import get_tool_descriptions

PLANNER_SYSTEM = """RULE #1 — ABSOLUTE: If the goal involves creating a game, pygame, tkinter, turtle, or any GUI application, your ENTIRE plan must be exactly 2 steps:
Step 0: file_write the complete code to the requested filename
Step 1: done

Do NOT use run_python. Do NOT generate any other steps. This is non-negotiable."

You are AURUM, an autonomous AI agent planner.

Your job is to break down a user goal into a clear, ordered list of executable steps.

Available tools:
{tools}

Rules:
- Return ONLY valid JSON
- No markdown
- No explanations
- No code fences
- Each step must contain:
  - "step_index"
  - "description"
  - "tool"
  - "tool_input"
- tool_input must ALWAYS be a JSON object
- Maximum 10 steps
- Be specific and concrete
- Use only available tools
- When generating Python code:
  - ALWAYS generate complete valid Python
  - NEVER leave placeholders
  - NEVER generate incomplete assignments
  - Code must run immediately without editing
  - IMPORTANT: Never use run_python if the code:
    * Contains input() calls (waits for user input)
    * Uses pygame, tkinter, turtle, or any GUI library
    * Is a game or interactive application
    In ALL these cases, use file_write to save the code to a .py file instead.
- Prefer simple approaches over complex web scraping
- For goals that involve reading, modifying, and writing a file, if no specific input file is mentioned, first create a sample input file using file_write, then read it with file_read, modify the content in a run_python step or inline, then write the result back with file_write.
- For calculation/compute/solve goals with no requested output file, use run_python to execute the calculation and produce the answer before done.
- For research/search goals:
  - Use web_search first.
  - If the user asks for a summary, clear summary, or research report, use summarize_text on the web_search results before file_write.
  - Only write raw web_search results directly when the user explicitly asks for raw results.
- For browser/web interaction goals:
  - Use browser_open to navigate to URLs
  - Use browser_click to interact with elements (use selectors from observation)
  - Use browser_type to fill input fields
  - Use browser_extract to get content from pages
  - ALWAYS base your actions on the current observation state
  - Use selectors that match elements seen in the observation

CRITICAL TOOL RULES:
- web_search already returns text snippets. NEVER follow up a web_search step with run_python that uses requests or BeautifulSoup to scrape URLs. The search results ARE the data — use them directly in the next step.
- Never use requests, BeautifulSoup, urllib, or any HTTP library in run_python. web_search is the only way to get web data.
- After a web_search step, the next step should be summarize_text or file_write to save the results, NOT another attempt to fetch URLs.

- IMPORTANT: Every plan MUST end with this exact step as the final step:
  {{
    "step_index": N,
    "description": "Mark task complete",
    "tool": "done",
    "tool_input": {{"summary": "clear one sentence description of what was accomplished"}}
  }}

Response format:
{{
  "plan_summary": "brief description",
  "steps": [
    {{
      "step_index": 0,
      "description": "step description",
      "tool": "tool_name",
      "tool_input": {{
        "param": "value"
      }}
    }}
  ]
}}
"""

REPLAN_SYSTEM = """You are AURUM, an autonomous AI replanner.

A previous step failed.

You must generate a revised remaining plan.

Original goal:
{goal}

Completed steps:
{completed}

Failed step:
{failed_step}

Failure reason:
{failure_reason}

Available tools:
{tools}

Rules:
- Return ONLY valid JSON
- No markdown
- No explanations
- Keep same response format
- Generate ONLY remaining steps
- If goal verification failed, change strategy instead of repeating the same failed tool/input.
- If research output was raw snippets but a summary was requested, use summarize_text before file_write.
- If a calculation goal lacked executed output, use run_python to compute and print the answer.
"""


def _call_groq(
    user_prompt: str,
    system_prompt: str,
    response_format: dict | None = None,
    max_tokens: int = 1024
) -> str:
    """Call Groq API with system and user prompts."""
    client = Groq(api_key=GROQ_API_KEY)
    params = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0,
        "max_tokens": max_tokens
    }
    if response_format is not None:
        params["response_format"] = response_format

    response = client.chat.completions.create(**params)
    return response.choices[0].message.content.strip()


def _call_llm(user_prompt: str, system_prompt: str) -> str:
    """Call the LLM (Groq)."""
    return _call_groq(user_prompt, system_prompt, response_format={"type": "json_object"})


def _parse_plan(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def _normalize_plan(plan: dict, done_summary: str = "Task completed") -> dict:
    """Make LLM plans executable without changing their intended tools."""
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    normalized = []
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        tool = str(raw_step.get("tool") or "").strip()
        if not tool:
            continue
        tool_input = raw_step.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        step_index = raw_step.get("step_index")
        if not isinstance(step_index, int):
            step_index = len(normalized)
        normalized.append({
            "step_index": step_index,
            "description": raw_step.get("description") or f"Run {tool}",
            "tool": tool,
            "tool_input": tool_input,
        })

    if not normalized or normalized[-1].get("tool") != "done":
        normalized.append({
            "step_index": len(normalized),
            "description": "Mark task complete",
            "tool": "done",
            "tool_input": {"summary": done_summary},
        })

    return {
        "plan_summary": plan.get("plan_summary", "") if isinstance(plan, dict) else "",
        "steps": normalized,
    }


def _filename_from_goal(goal: str) -> str:
    """Extract the first mentioned Python filename from a goal."""
    match = re.search(r"[\w./-]+\.py\b", goal)
    if match:
        return match.group(0)
    return "output.py"


def _strip_markdown_fences(raw_code: str) -> str:
    raw_code = raw_code.strip()
    if raw_code.startswith("```"):
        raw_code = raw_code.split("\n", 1)[-1]
    if raw_code.endswith("```"):
        raw_code = raw_code.rsplit("```", 1)[0]
    return raw_code.strip()


TODO_TEMPLATE = '''
import argparse
import json
from pathlib import Path

DATA_FILE = Path.home() / ".aurum_todo.json"


def load_tasks():
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_tasks(tasks):
    DATA_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def list_tasks(tasks):
    if not tasks:
        print("No tasks yet.")
        return
    for index, task in enumerate(tasks, start=1):
        mark = "x" if task.get("done") else " "
        print(f"{index}. [{mark}] {task['text']}")


def main():
    parser = argparse.ArgumentParser(description="Command line todo list")
    parser.add_argument("-a", "--add", help="Add a new task")
    parser.add_argument("-l", "--list", action="store_true", help="List tasks")
    parser.add_argument("-d", "--done", type=int, help="Mark task number as done")
    parser.add_argument("-r", "--remove", type=int, help="Remove task number")
    parser.add_argument("--clear", action="store_true", help="Remove all tasks")
    args = parser.parse_args()

    tasks = load_tasks()

    if args.add:
        tasks.append({"text": args.add, "done": False})
        save_tasks(tasks)
        print(f"Added: {args.add}")
    elif args.done is not None:
        if 1 <= args.done <= len(tasks):
            tasks[args.done - 1]["done"] = True
            save_tasks(tasks)
            print(f"Completed: {tasks[args.done - 1]['text']}")
        else:
            print("Invalid task number.")
    elif args.remove is not None:
        if 1 <= args.remove <= len(tasks):
            removed = tasks.pop(args.remove - 1)
            save_tasks(tasks)
            print(f"Removed: {removed['text']}")
        else:
            print("Invalid task number.")
    elif args.clear:
        save_tasks([])
        print("Cleared all tasks.")
    else:
        list_tasks(tasks)


if __name__ == "__main__":
    main()
'''


TETRIS_TEMPLATE = '''
import random
import sys
import pygame

pygame.init()

CELL_SIZE = 30
COLUMNS = 10
ROWS = 20
SIDE_PANEL = 180
WIDTH = CELL_SIZE * COLUMNS + SIDE_PANEL
HEIGHT = CELL_SIZE * ROWS
FPS = 60
DROP_EVENT = pygame.USEREVENT + 1

BLACK = (18, 18, 24)
GRID = (44, 44, 56)
WHITE = (240, 240, 245)
RED = (230, 72, 72)
COLORS = [
    (0, 240, 240),
    (0, 120, 240),
    (240, 160, 0),
    (240, 240, 0),
    (0, 220, 80),
    (160, 80, 220),
    (230, 60, 60),
]

SHAPES = [
    [[1, 1, 1, 1]],
    [[1, 0, 0], [1, 1, 1]],
    [[0, 0, 1], [1, 1, 1]],
    [[1, 1], [1, 1]],
    [[0, 1, 1], [1, 1, 0]],
    [[0, 1, 0], [1, 1, 1]],
    [[1, 1, 0], [0, 1, 1]],
]


class Piece:
    def __init__(self):
        self.shape_index = random.randrange(len(SHAPES))
        self.shape = [row[:] for row in SHAPES[self.shape_index]]
        self.color = COLORS[self.shape_index]
        self.x = COLUMNS // 2 - len(self.shape[0]) // 2
        self.y = 0

    def rotated(self):
        return [list(row) for row in zip(*self.shape[::-1])]


def new_board():
    return [[None for _ in range(COLUMNS)] for _ in range(ROWS)]


def valid_position(board, piece, dx=0, dy=0, shape=None):
    shape = shape or piece.shape
    for row_index, row in enumerate(shape):
        for col_index, cell in enumerate(row):
            if not cell:
                continue
            x = piece.x + col_index + dx
            y = piece.y + row_index + dy
            if x < 0 or x >= COLUMNS or y >= ROWS:
                return False
            if y >= 0 and board[y][x] is not None:
                return False
    return True


def lock_piece(board, piece):
    for row_index, row in enumerate(piece.shape):
        for col_index, cell in enumerate(row):
            if cell:
                y = piece.y + row_index
                x = piece.x + col_index
                if 0 <= y < ROWS:
                    board[y][x] = piece.color


def clear_lines(board):
    kept = [row for row in board if any(cell is None for cell in row)]
    cleared = ROWS - len(kept)
    while len(kept) < ROWS:
        kept.insert(0, [None for _ in range(COLUMNS)])
    return kept, cleared


def draw_board(screen, board, piece, score, game_over):
    screen.fill(BLACK)
    board_rect = pygame.Rect(0, 0, COLUMNS * CELL_SIZE, HEIGHT)
    pygame.draw.rect(screen, (26, 26, 34), board_rect)

    for y in range(ROWS):
        for x in range(COLUMNS):
            rect = pygame.Rect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            color = board[y][x]
            pygame.draw.rect(screen, color or GRID, rect, 1 if color is None else 0)
            if color:
                pygame.draw.rect(screen, BLACK, rect, 1)

    for row_index, row in enumerate(piece.shape):
        for col_index, cell in enumerate(row):
            if cell:
                rect = pygame.Rect(
                    (piece.x + col_index) * CELL_SIZE,
                    (piece.y + row_index) * CELL_SIZE,
                    CELL_SIZE,
                    CELL_SIZE,
                )
                pygame.draw.rect(screen, piece.color, rect)
                pygame.draw.rect(screen, BLACK, rect, 1)

    font = pygame.font.SysFont("arial", 24)
    big_font = pygame.font.SysFont("arial", 34, bold=True)
    panel_x = COLUMNS * CELL_SIZE + 24
    screen.blit(big_font.render("Tetris", True, WHITE), (panel_x, 32))
    screen.blit(font.render(f"Score: {score}", True, WHITE), (panel_x, 88))
    screen.blit(font.render("Arrows: move", True, WHITE), (panel_x, 150))
    screen.blit(font.render("Up: rotate", True, WHITE), (panel_x, 182))
    screen.blit(font.render("Esc: quit", True, WHITE), (panel_x, 214))

    if game_over:
        overlay = pygame.Surface((COLUMNS * CELL_SIZE, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 165))
        screen.blit(overlay, (0, 0))
        text = big_font.render("Game Over", True, RED)
        screen.blit(text, text.get_rect(center=(COLUMNS * CELL_SIZE // 2, HEIGHT // 2)))

    pygame.display.flip()


def main():
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Tetris")
    clock = pygame.time.Clock()
    pygame.time.set_timer(DROP_EVENT, 500)

    board = new_board()
    piece = Piece()
    score = 0
    game_over = False

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()
                if not game_over and event.key == pygame.K_LEFT and valid_position(board, piece, dx=-1):
                    piece.x -= 1
                elif not game_over and event.key == pygame.K_RIGHT and valid_position(board, piece, dx=1):
                    piece.x += 1
                elif not game_over and event.key == pygame.K_DOWN and valid_position(board, piece, dy=1):
                    piece.y += 1
                elif not game_over and event.key == pygame.K_UP:
                    rotated = piece.rotated()
                    if valid_position(board, piece, shape=rotated):
                        piece.shape = rotated
            if event.type == DROP_EVENT and not game_over:
                if valid_position(board, piece, dy=1):
                    piece.y += 1
                else:
                    lock_piece(board, piece)
                    board, cleared = clear_lines(board)
                    score += cleared * cleared * 100
                    piece = Piece()
                    if not valid_position(board, piece):
                        game_over = True

        draw_board(screen, board, piece, score, game_over)
        clock.tick(FPS)


if __name__ == "__main__":
    main()
'''


def _local_code_template(goal: str) -> str:
    goal_lower = goal.lower()
    if "todo" in goal_lower and ".py" in goal_lower:
        return TODO_TEMPLATE.strip() + "\n"
    if "tetris" in goal_lower:
        return TETRIS_TEMPLATE.strip() + "\n"
    return ""


def _raw_code_fallback_plan(goal: str) -> dict:
    """Ask for raw code and wrap it in a manual file_write plan."""
    filename = _filename_from_goal(goal)
    raw_code = _local_code_template(goal)
    if not raw_code:
        prompt = (
            f"Write complete Python code for: {goal}. Return ONLY the raw Python code, "
            "nothing else. No explanation, no markdown."
        )
        raw_code = _call_groq(
            prompt,
            "You write complete raw Python code only. Do not return JSON, markdown, or explanations.",
            max_tokens=8192
        )
        raw_code = _strip_markdown_fences(raw_code)
    if not raw_code:
        raw_code = _local_code_template(goal)
    if not raw_code:
        retry_prompt = (
            f"Generate a complete, runnable Python file named {filename} for this request: {goal}. "
            "Return only Python source code. The response must not be empty."
        )
        raw_code = _call_groq(
            retry_prompt,
            "Return only complete Python source code. No JSON. No markdown. No explanation.",
            max_tokens=8192
        )
        raw_code = _strip_markdown_fences(raw_code)

    return {
        "plan_summary": "Write code to file directly",
        "steps": [
            {
                "step_index": 0,
                "description": "Write code to file",
                "tool": "file_write",
                "tool_input": {
                    "path": filename,
                    "content": raw_code
                }
            },
            {
                "step_index": 1,
                "description": "Mark task complete",
                "tool": "done",
                "tool_input": {
                    "summary": "Code written successfully to file"
                }
            }
        ]
    }


def _is_gui_or_game_goal(goal: str) -> bool:
    gui_keywords = [
        'pygame',
        'tkinter',
        'turtle',
        'snake game',
        'chess game',
        'tetris',
        'game',
        'gui',
        'desktop app'
    ]
    goal_lower = goal.lower()
    return any(kw in goal_lower for kw in gui_keywords)


def _is_plain_file_creation_goal(goal: str) -> bool:
    """Detect goals that are simple file creation with raw content (txt, md, json, etc.)."""
    goal_lower = goal.lower()
    non_py_extensions = (
        ".txt", ".md", ".json", ".csv", ".yaml", ".yml",
        ".xml", ".html", ".css", ".log", ".cfg", ".ini", ".conf",
    )
    if any(ext in goal_lower for ext in non_py_extensions):
        intent_markers = ("create", "write", "make", "save", "generate", "replace", "overwrite")
        return any(marker in goal_lower for marker in intent_markers)
    return False


def _extract_plain_filename(goal: str) -> str:
    match = re.search(
        r"([\w./-]+\.(?:txt|md|json|csv|yaml|yml|xml|html|css|log|cfg|ini|conf))\b",
        goal,
        re.IGNORECASE,
    )
    return match.group(1).rstrip(".,;:") if match else ""


def _is_file_read_goal(goal: str) -> bool:
    goal_lower = goal.lower()
    if not _extract_plain_filename(goal):
        return False
    read_markers = ("read", "show", "display", "open")
    write_markers = ("create", "write", "make", "save", "generate", "replace", "overwrite")
    return any(marker in goal_lower for marker in read_markers) and not any(
        marker in goal_lower for marker in write_markers
    )


def _goal_requests_readback(goal: str) -> bool:
    goal_lower = goal.lower()
    return any(
        phrase in goal_lower
        for phrase in (
            "then read it back",
            "read it back",
            "then read",
            "read back",
        )
    )


def _strip_readback_request(content: str) -> str:
    return re.sub(
        r"(?:\n|\s)+then\s+read(?:\s+it)?\s+back\s*$",
        "",
        content,
        flags=re.IGNORECASE,
    ).strip()


def _normalize_plain_file_content(content: str) -> str:
    content = _strip_readback_request(content).strip(" .")
    content = re.sub(r"^\s*(?:the\s+)?(?:text|content)\s+", "", content, flags=re.IGNORECASE)
    content = re.sub(r"^\s*\d+\s+lines?\s*:\s*", "", content, flags=re.IGNORECASE)
    return content.strip()


def _file_read_plan(goal: str) -> dict:
    filename = _extract_plain_filename(goal)
    if not filename:
        return {"plan_summary": "Read file", "steps": []}
    return {
        "plan_summary": f"Read {filename}",
        "steps": [
            {
                "step_index": 0,
                "description": f"Read {filename}",
                "tool": "file_read",
                "tool_input": {"path": filename},
            },
            {
                "step_index": 1,
                "description": "Return file contents",
                "tool": "done",
                "tool_input": {"summary": "{output}"},
            },
        ],
    }

def _is_code_generation_goal(goal: str) -> bool:
    """Detect goals that explicitly require Python code generation."""
    goal_lower = goal.lower()
    # Explicit Python code generation indicators
    explicit_python_keywords = [
        "python", "python script", "python code", "python program",
        "pygame", "tkinter", "turtle", "gui app", "gui application"
    ]
    # Check for explicit Python mention
    for kw in explicit_python_keywords:
        if kw in goal_lower:
            return True
    # Check for .py file with code/program/script/app keyword
    if ".py" in goal_lower:
        code_action_keywords = ["write", "create", "build", "generate", "make", "implement", "script", "program", "app"]
        if any(kw in goal_lower for kw in code_action_keywords):
            return True
    # Games and interactive apps always need code generation
    game_keywords = ["game", "interactive"]
    dangerous_keywords = ["pygame", "tkinter", "turtle"]
    if any(kw in goal_lower for kw in game_keywords + dangerous_keywords):
        return True
    return False


def _plan_has_empty_python_write(plan: dict) -> bool:
    for step in plan.get("steps", []):
        tool_input = step.get("tool_input", {})
        path = str(tool_input.get("path", ""))
        content = tool_input.get("content")
        if step.get("tool") == "file_write" and path.endswith(".py") and not str(content or "").strip():
            return True
    return False


def _plain_file_creation_plan(goal: str) -> dict:
    """Create a simple plan for plain file creation goals."""
    filename = _extract_plain_filename(goal)
    if not filename:
        return {
            "plan_summary": "Plain file creation",
            "steps": []
        }

    goal_lower = goal.lower()
    lower_filename = filename.lower()
    filename_idx = goal_lower.find(lower_filename)

    content = ""

    # Pattern: "... <filename> with <content>"
    with_match = re.search(r"\bwith\b(.+)$", goal, re.IGNORECASE | re.DOTALL)
    if with_match and filename_idx != -1 and with_match.start() > filename_idx:
        content = with_match.group(1)

    # Pattern: "write <content> to <filename>"
    if not content and filename_idx != -1:
        to_match = re.search(r"\bto\b\s+" + re.escape(filename), goal, re.IGNORECASE)
        write_match = re.search(r"\bwrite\b(.+?)\bto\b", goal, re.IGNORECASE | re.DOTALL)
        if to_match and write_match and write_match.start() < to_match.start():
            content = write_match.group(1)

    # Pattern: quoted payload anywhere in prompt
    if not content:
        quoted = re.search(r"['\"]([^'\"]+)['\"]", goal)
        if quoted:
            content = quoted.group(1).strip()

    content = _normalize_plain_file_content(content)

    if content:
        steps = [
            {
                "step_index": 0,
                "description": f"Write content to {filename}",
                "tool": "file_write",
                "tool_input": {
                    "path": filename,
                    "content": content
                }
            }
        ]
        if _goal_requests_readback(goal):
            steps.append({
                "step_index": 1,
                "description": f"Read {filename}",
                "tool": "file_read",
                "tool_input": {"path": filename},
            })
        steps.append({
            "step_index": len(steps),
            "description": "Mark task complete",
            "tool": "done",
            "tool_input": {
                "summary": "{output}" if _goal_requests_readback(goal) else f"Created {filename} with content"
            }
        })
        return {
            "plan_summary": f"Create file {filename}",
            "steps": steps,
        }
    return {
        "plan_summary": "Plain file creation",
        "steps": []
    }

def create_plan(goal: str, context: str = "", memory=None) -> dict:
    """Generate a step-by-step plan for a goal, optionally using episodic memory."""
    # Check for plain file creation FIRST (txt, md, json, etc.)
    if _is_plain_file_creation_goal(goal):
        plan = _plain_file_creation_plan(goal)
        if plan["steps"]:
            return plan
    if _is_file_read_goal(goal):
        plan = _file_read_plan(goal)
        if plan["steps"]:
            return plan
    if _is_gui_or_game_goal(goal) or _is_code_generation_goal(goal):
        return _raw_code_fallback_plan(goal)
    tools_desc = get_tool_descriptions()

    # If memory provided, recall recent episodes and inject into context
    episodes_text = ""
    if memory and hasattr(memory, 'episodic') and hasattr(memory.episodic, 'recall_recent'):
        recent_episodes = memory.episodic.recall_recent(3)
        if recent_episodes:
            episodes_text = "Past task strategies (learn from these):\n"
            for ep in recent_episodes:
                # Assuming each episode has goal and outcome
                ep_goal = ep.get('goal', 'unknown')
                ep_outcome = ep.get('outcome', 'unknown')
                ep_strategy = ep.get('strategy_notes', '')
                episodes_text += f"- Goal: {ep_goal} → Outcome: {ep_outcome}. Strategy: {ep_strategy}\n"
            episodes_text += "\n"

    user_prompt = f"{episodes_text}Goal: {goal}"
    if context:
        user_prompt += f"\n\nAdditional context:\n{context}"

    system_prompt = PLANNER_SYSTEM.format(
        tools=tools_desc
    )

    env = memory.short.get("environment", "") if memory else ""
    user_prompt = goal
    if env:
        user_prompt += f"\n\nCurrent environment:\n{env}"

    if context:
        user_prompt += f"\n\nAdditional context:\n{context}"

    try:
        raw = _call_llm(user_prompt, system_prompt)
        plan = _parse_plan(raw)
        if _plan_has_empty_python_write(plan):
            return _raw_code_fallback_plan(goal)
        return _normalize_plan(plan)

    except (json.JSONDecodeError, ValueError) as e:
        # Handle JSON parsing errors
        print("\n[Planner Parse Error]")
        print(str(e)[:200])
        print()

        return _raw_code_fallback_plan(goal)
    except Exception as e:
        # Handle Groq API errors (json_validate_failed, 400 errors, etc.)
        print("\n[Planner API Error]")
        print(f"Error type: {type(e).__name__}")
        print(str(e)[:200])
        print()

        return _raw_code_fallback_plan(goal)


def replan(
    goal: str,
    completed_steps: list,
    failed_step: dict,
    failure_reason: str,
    memory=None
) -> dict:
    """Generate a revised plan after a step failure."""

    if _is_gui_or_game_goal(goal) or _is_code_generation_goal(goal):
        return _raw_code_fallback_plan(goal)

    tools_desc = get_tool_descriptions()

    def _result_summary(step: dict) -> str:
        result = step.get("result", "")
        if isinstance(result, str):
            return result[:100]
        try:
            return json.dumps(result, default=str)[:100]
        except (TypeError, ValueError):
            return str(result)[:100]

    completed_summary = "\n".join(
        [
            f"Step {s.get('step_index')}: "
            f"{s.get('description')} → "
            f"{_result_summary(s)}"
            for s in completed_steps
        ]
    ) or "None"

    system_prompt = REPLAN_SYSTEM.format(
        goal=goal,
        completed=completed_summary,
        failed_step=json.dumps(failed_step),
        failure_reason=failure_reason,
        tools=tools_desc
    )

    user_prompt = (
        "Generate a revised remaining plan."
    )

    try:
        raw = _call_llm(user_prompt, system_prompt)
        return _normalize_plan(_parse_plan(raw), "Replanned task completed")

    except (json.JSONDecodeError, ValueError) as e:
        # Handle JSON parsing errors
        print("\n[Replan Parse Error]")
        print(str(e)[:200])
        print()

        return {
            "plan_summary": "Replan failed",
            "steps": []
        }
    except Exception as e:
        # Handle Groq API errors (json_validate_failed, 400 errors, etc.)
        print("\n[Replan API Error]")
        print(f"Error type: {type(e).__name__}")
        print(str(e)[:200])
        print()

        # Return empty steps instead of trying another LLM call that will also fail
        return {
            "plan_summary": "Replan failed due to API error",
            "steps": []
        }
