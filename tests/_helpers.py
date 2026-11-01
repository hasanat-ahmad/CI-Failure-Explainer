"""Shared paths and imports for the test suite (standard library only)."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "ci-failure-explainer"
SCRIPTS = SKILL / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def expected() -> dict:
    return json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def secrets_fixture() -> list:
    """(Re)generate the git-ignored secrets fixture and return its fake secret values.

    It isn't committed because realistic fake tokens trip secret scanners; the
    generator is deterministic, so the file is identical on every run.
    """
    if str(FIXTURES) not in sys.path:
        sys.path.insert(0, str(FIXTURES))
    import generate_fixtures

    return generate_fixtures.log_with_secrets()


def fixture_text(name: str) -> str:
    if name == "log_with_secrets.log":
        secrets_fixture()
    return (FIXTURES / name).read_text(encoding="utf-8")
