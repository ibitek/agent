"""Shared pytest configuration.

``pyproject.toml`` already sets ``pythonpath = ["src"]``; this module also makes
the test helpers importable regardless of how pytest was invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"

for path in (str(TESTS_DIR), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
