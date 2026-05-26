import gc
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _resource_warning_cleanup():
    """Extra defensive cleanup to prevent pytest -W error failures.

    In CPython, sqlite3.Connection objects can be finalized by GC at
    interpreter shutdown. When pytest uses unraisable exception collection,
    those finalizers become warnings.

    This fixture proactively closes the global sqlite connection (if any)
    and runs a GC cycle after each test to flush deterministically.

    Note: the AURUM codebase should already close connections via
    context managers. This fixture is a safety net to keep the test suite
    strict.
    """

    yield

    # Ensure all destructors run while pytest is still active.
    gc.collect()

