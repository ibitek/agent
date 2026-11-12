"""Syntax-check optional backends too, even when runtime tests do not import them."""

import ast
from pathlib import Path


def test_all_source_modules_have_valid_syntax():
    root = Path(__file__).resolve().parents[1] / "src"
    modules = list(root.rglob("*.py"))
    assert modules
    for module in modules:
        ast.parse(module.read_text(encoding="utf-8-sig"), filename=str(module))
