"""Repository paths shared by the tests (kept out of conftest.py so tests can import them)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE_DATA = FIXTURES / "data"
INVALID = FIXTURES / "invalid"
SEED_PROGRAMMES = REPO_ROOT / "seed" / "programmes"
