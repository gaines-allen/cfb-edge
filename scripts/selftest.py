#!/usr/bin/env python3
"""
Runs the test suite. Kept as a script because .github/workflows/daily.yml
calls it by name and the daily job must not break.

The checks themselves moved to tests/ and run under pytest. This is a
wrapper and nothing more, so it stays 1 command in the workflow and 1
command locally.

    python3 scripts/selftest.py
    python3 scripts/selftest.py -k teams -v

Any argument is passed straight through to pytest. The exit code is
pytest's, so a failure still stops the daily run before it rebuilds a page
off a payload nobody has looked at.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def main(argv: list[str]) -> int:
    if not TESTS.exists():
        print(f"ERROR: {TESTS} is missing, so there is nothing to run.",
              file=sys.stderr)
        return 2

    cmd = [sys.executable, "-m", "pytest", str(TESTS)]
    cmd += argv or ["-q"]
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except FileNotFoundError:
        print("ERROR: pytest is not installed. It is in requirements.txt, so "
              "pip install -r requirements.txt fixes this.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
