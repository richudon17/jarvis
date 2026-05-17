from core.deterministic_repair import deterministic_repair


def make_step(tool, tool_input=None, step_index=1, description=""):
    return {
        "step_index": step_index,
        "description": description or "",
        "tool": tool,
        "tool_input": tool_input or {},
    }


def make_executed(status="failed", result=None):
    return {"status": status, "result": result or {}}


def test_file_write_empty_content_repair():
    step = make_step("file_write", {"path": "out.txt", "content": ""})
    executed = make_executed(result={"ok": False, "error": "empty content", "metadata": {}})
    r = deterministic_repair(step=step, executed_step=executed, goal="Create a file called out.txt", completed_steps=[], attempted_steps=[])
    assert r.get("handled")
    assert r.get("action") == "retry"
    assert isinstance(r.get("new_steps"), list) and len(r.get("new_steps")) > 0


def test_run_python_syntax_convert():
    step = make_step("run_python", {"code": "def oops(:\n pass\n", "path": "script.py"})
    executed = make_executed(result={"ok": False, "error": "SyntaxError: invalid syntax", "metadata": {}})
    r = deterministic_repair(step=step, executed_step=executed, goal="Run python script", completed_steps=[], attempted_steps=[])
    assert r.get("handled")
    assert r.get("action") == "convert"
    assert isinstance(r.get("new_steps"), list)


def test_web_search_rate_limit_retry():
    step = make_step("web_search", {"query": "python"})
    executed = make_executed(result={"ok": False, "error": "429 Too Many Requests", "metadata": {}})
    r = deterministic_repair(step=step, executed_step=executed, goal="Research python", completed_steps=[], attempted_steps=[])
    assert r.get("handled")
    assert r.get("action") == "retry"