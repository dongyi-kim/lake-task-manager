"""Stable project paths for tests that may move between functional folders."""
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_ROOT.parent
DEPLOY_ROOT = REPO_ROOT.parent
STATIC_ROOT = REPO_ROOT / "app" / "static"
