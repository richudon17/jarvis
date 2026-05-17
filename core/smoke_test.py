"""Runtime smoke testing for generated Python artifacts.

This module performs safe compile and bounded execution checks for Python files.
It only executes code when the artifact appears safe and non-interactive.
"""

from __future__ import annotations

import os
import re
import sys
import time
import py_compile
import subprocess
from pathlib import Path
from typing import Any

from core.artifact import make_artifact_record

BANNED_RUNTIME_PATTERNS = [
    # Interactive/input patterns
    r"\binput\(",
    r"\bgetpass\b",
    
    # GUI frameworks
    r"\bimport\s+pygame",
    r"\bfrom\s+pygame",
    r"\bimport\s+tkinter",
    r"\bfrom\s+tkinter",
    r"\bimport\s+turtle",
    r"\bfrom\s+turtle",
    r"\bimport\s+ PyQt",
    r"\bfrom\s+ PyQt",
    r"\bimport\s+wx",
    r"\bfrom\s+wx",
    r"\bmainloop\(",
    r"\b\.mainloop\(",
    
    # Infinite loops without exit condition
    r"\bwhile\s+true\b",  # Lowercase because code is lowercased before matching
    r"\bwhile\s+1\b",
    r"\bwhile\s+True\b",
    
    # Networking (can be slow, unpredictable, or blocked)
    r"\bimport\s+requests",
    r"\bfrom\s+requests",
    r"\bimport\s+urllib",
    r"\bfrom\s+urllib",
    r"\bimport\s+socket",
    r"\bfrom\s+socket",
    r"\bimport\s+http",
    r"\bfrom\s+http",
    
    # Subprocess/shell execution (security risk)
    r"\bimport\s+subprocess",
    r"\bfrom\s+subprocess",
    r"\bsubprocess\.Popen\b",
    r"\bsubprocess\.run\b",
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bos\.exec",
    r"\bsubprocess\.check_output\b",
    
    # Concurrency (can cause timeouts/hangs)
    r"\bthreading\b",
    r"\bthread\b",
    r"\basyncio\b",
    r"\bmultiprocessing\b",
    
    # Dangerous functions
    r"\beval\(",
    r"\bexec\(",
    r"\b__import__\b",
    r"\bcompile\(",
    
    # File system operations that could be destructive
    r"\bshutil\.rmtree\b",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bos\.rmdir\b",
]

CLI_HELP_PATTERNS = [
    r"\bargparse\b",
    r"\bclick\b",
    r"\bfire\b",
    r"\btyper\b",
    r"\bif __name__ == ['\"]__main__['\"]:",
]


def _read_source(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _matches_any(text: str, patterns: list[str]) -> bool:
    lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, lower):
            return True
    return False


def _safe_to_execute_python(code: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if _matches_any(code, BANNED_RUNTIME_PATTERNS):
        issues.append("Detected interactive, GUI, networking, or subprocess patterns.")
    if _matches_any(code, [r"\bwhile\s+true\b"]):  # Lowercase because code is lowercased
        if not _matches_any(code, [r"\bsleep\(", r"\btime\.sleep\(", r"\btick\("]):
            issues.append("Detected unbounded while True loop without sleep/tick.")
    return (len(issues) == 0, issues)


def _run_subprocess(args: list[str], timeout: int) -> dict[str, Any]:
    start = time.time()
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        runtime_seconds = time.time() - start
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "timeout": False,
            "runtime_seconds": runtime_seconds,
        }
    except subprocess.TimeoutExpired as exc:
        runtime_seconds = time.time() - start
        return {
            "exit_code": -1,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timeout": True,
            "runtime_seconds": runtime_seconds,
        }
    except Exception as exc:
        runtime_seconds = time.time() - start
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "timeout": False,
            "runtime_seconds": runtime_seconds,
        }


def smoke_test_python_file(path: str, timeout: int = 8) -> dict[str, Any]:
    path_expanded = str(Path(path).expanduser())
    source = _read_source(path_expanded)
    safe, safety_issues = _safe_to_execute_python(source)

    result = {
        "compiled": False,
        "executed": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "timeout": False,
        "runtime_seconds": 0.0,
        "safe_to_execute": safe,
        "execution_skipped": not safe,
        "compile_error": None,
        "safety_issues": safety_issues,
        "help_run": False,
        "runtime_mode": "skipped" if not safe else "pending",
    }

    if not Path(path_expanded).exists():
        result["compile_error"] = "file not found"
        return result

    try:
        py_compile.compile(path_expanded, doraise=True)
        result["compiled"] = True
    except py_compile.PyCompileError as exc:
        result["compile_error"] = str(exc)
        result["runtime_mode"] = "compile_failed"
        return result
    except Exception as exc:
        result["compile_error"] = str(exc)
        result["runtime_mode"] = "compile_failed"
        return result

    if not safe:
        result["runtime_mode"] = "unsafe"
        return result

    args = [sys.executable, path_expanded]
    if _matches_any(source, CLI_HELP_PATTERNS):
        help_result = _run_subprocess(args + ["--help"], timeout)
        if help_result["exit_code"] == 0 and not help_result["timeout"]:
            result.update(help_result)
            result["executed"] = True
            result["help_run"] = True
            result["runtime_mode"] = "help"
            return result

    execution = _run_subprocess(args, timeout)
    result.update(execution)
    result["executed"] = result["exit_code"] == 0 and not result["timeout"]
    result["runtime_mode"] = "executed"
    return result


def smoke_test_artifact_metadata(path: str, artifact_type: str = "python") -> dict[str, Any]:
    smoke = smoke_test_python_file(path)
    verified = smoke["compiled"] and (smoke["execution_skipped"] or smoke["executed"])
    issues = []
    warnings = []
    evidence = []

    if smoke["compiled"]:
        evidence.append("py_compile successful")
    if smoke["execution_skipped"]:
        warnings.append("Execution skipped for safety reasons.")
        evidence.append("safe execution skipped")
    if smoke["executed"]:
        evidence.append("runtime execution succeeded")
    if smoke["timeout"]:
        issues.append("runtime execution timed out")
    if smoke["exit_code"] is not None and smoke["exit_code"] != 0:
        issues.append(f"runtime exit code {smoke['exit_code']}")

    metadata = make_artifact_record(
        artifact_type=artifact_type,
        path=path,
        created=True,
        verified=verified,
        quality_score=0.0,
        semantic_confidence=0.0,
        runtime_validated=smoke["compiled"] and (smoke["execution_skipped"] or smoke["executed"]),
        issues=issues,
        warnings=warnings,
        evidence=evidence,
    )
    metadata["smoke_test"] = smoke
    return metadata
