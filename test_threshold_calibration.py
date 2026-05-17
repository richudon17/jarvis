import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from core.quality import review_quality
from core.verifier import verify_goal


# Labeled validation cases for calibration.
# Each entry: (goal, completed_steps, expected_verify_pass, expected_quality_pass)
VALIDATION_CASES: list[tuple[str, list[dict], bool, bool]] = []


def _tmp_suffix(suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    return f.name


def _write_bytes(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def _mk_file_write_step(path: str, *, bytes_written: int | None = None, metadata: dict[str, Any] | None = None) -> dict:
    meta = metadata or {}
    if bytes_written is None:
        bytes_written = Path(path).stat().st_size if Path(path).exists() else 0
    # Keep schema similar to executor.
    meta = {"bytes_written": bytes_written, **meta}
    return {
        "status": "success",
        "evaluation": {"passed": True},
        "tool": "file_write",
        "tool_input": {"path": path},
        "result": {"ok": True, "data": None, "error": None, "metadata": meta},
    }


# ----------------------
# Dataset construction
# ----------------------

# Good Python artifact
p_good = _tmp_suffix(".py")
_write_bytes(
    p_good,
    b"def add(a,b):\n    return a+b\n\nif __name__=='__main__':\n    print(add(2,3))\n",
)
VALIDATION_CASES.append(
    (
        f"Create a file called {p_good}",
        [_mk_file_write_step(p_good, bytes_written=Path(p_good).stat().st_size)],
        True,
        True,
    )
)

# Placeholder code (should fail quality)
p_stub = _tmp_suffix(".py")
_write_bytes(p_stub, b"# TODO: implement\npass\n")
VALIDATION_CASES.append(
    (
        f"Create a file called {p_stub}",
        [_mk_file_write_step(p_stub, bytes_written=Path(p_stub).stat().st_size)],
        True,
        False,
    )
)

# Python file: syntax error (verifier should fail)
p_syntax = _tmp_suffix(".py")
_write_bytes(p_syntax, b"def oops(:\n    pass\n")
VALIDATION_CASES.append(
    (
        f"Create a file called {p_syntax}",
        [_mk_file_write_step(p_syntax, bytes_written=Path(p_syntax).stat().st_size)],
        False,
        False,
    )
)

# Research summary good
p_research = _tmp_suffix(".md")
_write_bytes(
    p_research,
    b"# Summary\nThis is a clear summary of topic.\n\n## Sources\n- http://example.com\n",
)
VALIDATION_CASES.append(
    (
        f"Research Python and save to {p_research}",
        [_mk_file_write_step(p_research, bytes_written=Path(p_research).stat().st_size)],
        True,
        True,
    )
)

# Research raw snippets (should fail quality)
p_snip = _tmp_suffix(".md")
_write_bytes(p_snip, b"title: a\nurl: http://x\nsnippet: foo\ntitle: b\n")
VALIDATION_CASES.append(
    (
        f"Research something and save to {p_snip}",
        [_mk_file_write_step(p_snip, bytes_written=Path(p_snip).stat().st_size)],
        True,
        False,
    )
)

# Research: too-short summary (quality should fail)
p_short = _tmp_suffix(".md")
_write_bytes(p_short, b"# Summary\nToo short.\n")
VALIDATION_CASES.append(
    (
        f"Research Python and save to {p_short}",
        [_mk_file_write_step(p_short, bytes_written=Path(p_short).stat().st_size)],
        True,
        False,
    )
)

# Calculation: stdout present
VALIDATION_CASES.append(
    (
        "Calculate sum",
        [
            {
                "status": "success",
                "evaluation": {"passed": True},
                "tool": "run_python",
                "tool_input": {"path": "calc.py"},
                "result": {"ok": True, "data": {"stdout": "42"}, "metadata": {}},
            }
        ],
        True,
        True,
    )
)

# Calculation: missing run_python (verifier should fail)
VALIDATION_CASES.append(("Calculate something", [], False, False))

# Calculation: stdout empty (quality should fail)
VALIDATION_CASES.append(
    (
        "Calculate sum",
        [
            {
                "status": "success",
                "evaluation": {"passed": True},
                "tool": "run_python",
                "tool_input": {"path": "calc.py"},
                "result": {"ok": True, "data": {"stdout": ""}, "metadata": {}},
            }
        ],
        False,
        False,
    )
)


# ----------------------
# Smoke-test evidence variants
# ----------------------

def _mk_python_write_with_smoke(path: str, smoke: dict[str, Any]) -> dict:
    return _mk_file_write_step(
        path,
        bytes_written=Path(path).stat().st_size,
        metadata={"smoke_test": smoke},
    )


# Runtime validated: compiled+executed
p_rt_ok = _tmp_suffix(".py")
_write_bytes(p_rt_ok, b"print('x')\n")
VALIDATION_CASES.append(
    (
        f"Create a file called {p_rt_ok}",
        [
            _mk_python_write_with_smoke(
                p_rt_ok,
                {"compiled": True, "executed": True, "execution_skipped": False, "exit_code": 0, "stdout": "x", "stderr": ""},
            )
        ],
        True,
        True,
    )
)

# Compiled only (no runtime evidence): verifier should pass but with lower confidence.
p_rt_compiled_only = _tmp_suffix(".py")
_write_bytes(p_rt_compiled_only, b"print('y')\n")
VALIDATION_CASES.append(
    (
        f"Create a file called {p_rt_compiled_only}",
        [
            _mk_python_write_with_smoke(
                p_rt_compiled_only,
                {"compiled": True, "executed": False, "execution_skipped": False, "exit_code": 0, "stdout": "", "stderr": ""},
            )
        ],
        True,
        True,
    )
)

# Adversarial: claim executed but compiled step writes bytes to a non-existent file.
# Here we point smoke_test at a path that doesn't exist, so verifier should fail.
nonexistent = "/tmp/nonexistent_calib_artifact.py"
VALIDATION_CASES.append(
    (
        f"Create a file called {nonexistent}",
        [
            {
                "status": "success",
                "evaluation": {"passed": True},
                "tool": "file_write",
                "tool_input": {"path": nonexistent},
                "result": {
                    "ok": True,
                    "data": None,
                    "error": None,
                    "metadata": {
                        "bytes_written": 10,
                        "smoke_test": {"compiled": True, "executed": True, "execution_skipped": False, "exit_code": 0, "stdout": "hi"},
                    },
                },
            }
        ],
        False,
        False,
    )
)


def evaluate(threshold_quality: float, verifier_conf: float) -> tuple[float, float]:
    v_hits = 0
    q_hits = 0

    for goal, steps, expect_v, expect_q in VALIDATION_CASES:
        v = verify_goal(goal, steps, attempted_steps=[])
        q = review_quality(goal, completed_steps=steps, attempted_steps=[])

        v_pass = bool(v["passed"] and v["confidence"] >= verifier_conf)
        q_pass = bool(q["passed"] and q["score"] >= threshold_quality)

        if v_pass == expect_v:
            v_hits += 1
        if q_pass == expect_q:
            q_hits += 1

    n = len(VALIDATION_CASES)
    return v_hits / n, q_hits / n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--q-grid", default=None, help="Comma-separated thresholds, e.g. 0.6,0.7")
    parser.add_argument("--v-grid", default=None, help="Comma-separated verifier confidence thresholds, e.g. 0.5,0.6")
    parser.add_argument("--q-start", type=float, default=0.6)
    parser.add_argument("--q-end", type=float, default=0.85)
    parser.add_argument("--q-step", type=float, default=0.05)
    parser.add_argument("--v-start", type=float, default=0.5)
    parser.add_argument("--v-end", type=float, default=0.95)
    parser.add_argument("--v-step", type=float, default=0.1)
    args = parser.parse_args()

    if args.q_grid:
        q_grid = [float(x.strip()) for x in args.q_grid.split(",") if x.strip()]
    else:
        q_grid = []
        x = args.q_start
        while x <= args.q_end + 1e-9:
            q_grid.append(round(x, 6))
            x += args.q_step

    if args.v_grid:
        v_grid = [float(x.strip()) for x in args.v_grid.split(",") if x.strip()]
    else:
        v_grid = []
        x = args.v_start
        while x <= args.v_end + 1e-9:
            v_grid.append(round(x, 6))
            x += args.v_step

    print(f"Loaded validation cases: {len(VALIDATION_CASES)}")
    print("Q grid:", q_grid)
    print("V grid:", v_grid)

    best = None
    for q_thr in q_grid:
        for v_thr in v_grid:
            v_acc, q_acc = evaluate(threshold_quality=q_thr, verifier_conf=v_thr)
            acc = (v_acc + q_acc) / 2.0
            print(f"q={q_thr:.2f} v={v_thr:.2f} -> v_acc={v_acc:.2f} q_acc={q_acc:.2f} avg={acc:.2f}")
            if best is None or acc > best[0]:
                best = (acc, q_thr, v_thr, v_acc, q_acc)

    print("BEST:", best)


if __name__ == "__main__":
    main()

