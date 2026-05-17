# TODO: Structured Persistence + Calculation Grounding Fix

## Persistence
- [ ] Add safe JSON serialization helpers (serialize_for_storage / deserialize_from_storage) with backwards compatibility.
- [ ] Update `state/persistence.py` to serialize *all* `tool_input`/`result` fields safely before SQLite inserts.
- [ ] Ensure schema stores structured runtime objects as JSON text (and uses TEXT columns).
- [ ] Add pytest regression tests for:
  - [ ] structured dict persistence roundtrip
  - [ ] nested metadata persistence
  - [ ] backward compatibility when old rows contain primitive/string values

## Calculation grounding
- [ ] Fix planner so calculation/math goals prefer `run_python` + evidence capture.
- [ ] Add regression tests ensuring Fibonacci task uses run_python and doesn't accept hallucinated file_write.
- [ ] Add completion integrity rule: fail if calculation code generated but not executed.

## Validation
- [ ] Run `python3 -m py_compile ...` for the listed modules.
- [ ] Run `pytest -q`.
- [ ] Manual runtime validation: Fibonacci first 20 saved to `fibonacci.txt`.
