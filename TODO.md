# TODO

- [ ] Parameterize orchestration thresholds (quality + semantic confidence, plus optional retry/step bounds) via env vars and/or config file.
- [ ] Expand `test_threshold_calibration.py` validation dataset substantially (more deterministic good/bad cases across code/research/calculation).
- [ ] Re-run/extend automated grid search in `test_threshold_calibration.py` with configurable grids.
- [ ] Expand adversarial tests with additional spoofing vectors (smoke-test metadata inconsistencies, missing referenced files, empty-content persistence spoof).
- [ ] Run full test suite (`pytest -q`) and fix any regressions/bugs.
- [ ] Update `TESTING_QUICK_SUMMARY.md` with new results, best grid parameters, added tests, and bugfix notes.

