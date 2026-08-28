"""Suite-wide pytest bootstrap shared by every functional test folder."""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
sys.dont_write_bytecode = True
for root in (TESTS_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def pytest_sessionfinish(session, exitstatus):
    """Remove the bootstrap bytecode written before this conftest can disable it."""
    bootstrap_cache = TESTS_ROOT / "__pycache__"
    for compiled in bootstrap_cache.glob("conftest.*.pyc"):
        compiled.unlink(missing_ok=True)
    try:
        bootstrap_cache.rmdir()
    except OSError:
        pass
