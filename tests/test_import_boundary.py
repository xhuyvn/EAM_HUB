"""Test import boundary and runtime purity for ppm_reducer.

Rule: ppm_reducer is pure: zero I/O, no .env access, stdlib allowlist only.
Allowed stdlib modules: dataclasses, datetime, enum, typing, re, json, hashlib,
functools, itertools, collections, decimal, math.
Enforced via:
1. Static AST allowlist check.
2. Runtime sys.addaudithook over the full corpus.
3. Verification that probe files with disallowed imports/calls fail.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from typing import Any

import pytest

from ppm_reducer import InputRejected, assess_criteria

ALLOWED_MODULES = {
    "dataclasses",
    "datetime",
    "enum",
    "typing",
    "re",
    "json",
    "hashlib",
    "functools",
    "itertools",
    "collections",
    "decimal",
    "math",
    "ppm_reducer",
}

FORBIDDEN_CALLS = {"open", "__import__", "eval", "exec"}


def check_ast_purity(source: str, filename: str = "<source>") -> list[str]:
    """Check AST of python source code against pure stdlib allowlist."""
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_name = alias.name.split(".")[0]
                if top_name not in ALLOWED_MODULES:
                    violations.append(f"Line {node.lineno}: import {top_name} (not in allowlist)")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top_name = node.module.split(".")[0]
                if top_name not in ALLOWED_MODULES:
                    violations.append(f"Line {node.lineno}: from {top_name} import ... (not in allowlist)")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                violations.append(f"Line {node.lineno}: call to forbidden builtin '{func.id}()'")
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
                violations.append(f"Line {node.lineno}: call to forbidden '{func.attr}()'")
            elif (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                attr_val = str(node.args[1].value)
                if attr_val in FORBIDDEN_CALLS:
                    violations.append(f"Line {node.lineno}: getattr on forbidden '{attr_val}'")

    return violations


def test_ppm_reducer_allowlist_imports_and_calls() -> None:
    """Verify all files in ppm_reducer strictly adhere to the module allowlist and forbidden calls."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    reducer_dir = repo_root / "ppm-v2" / "reducer" / "ppm_reducer"
    py_files = sorted(reducer_dir.rglob("*.py"))
    assert len(py_files) > 0, f"No Python files found in {reducer_dir}"

    all_violations: list[str] = []
    for py_file in py_files:
        violations = check_ast_purity(py_file.read_text(encoding="utf-8"), filename=py_file.name)
        for v in violations:
            all_violations.append(f"{py_file.name} - {v}")

    assert not all_violations, "Forbidden imports/calls detected in ppm_reducer:\n" + "\n".join(all_violations)


@pytest.mark.parametrize(
    "probe_code,expected_token",
    [
        ("import shutil\nx = shutil.disk_usage('/')", "shutil"),
        ("import builtins\nx = builtins.open('file.txt')", "builtins"),
        ("import os\npid = os.getpid()", "os"),
        ("import subprocess\nsubprocess.run(['ls'])", "subprocess"),
        ("f = getattr(__builtins__, 'open')\nf('secret.txt')", "open"),
        ("open('foo.txt', 'w')", "open"),
    ],
)
def test_allowlist_probe_fails(probe_code: str, expected_token: str) -> None:
    """Demonstrate that probes attempting I/O or disallowed imports fail the static check."""
    violations = check_ast_purity(probe_code, filename="probe.py")
    assert len(violations) > 0, f"Probe unexpectedly passed purity check: {probe_code}"
    assert any(expected_token in v for v in violations)


# Global hook state for runtime purity audit
_AUDIT_HOOK_INSTALLED = False
_AUDIT_ACTIVE = False
_AUDIT_VIOLATIONS: list[tuple[str, tuple[Any, ...]]] = []


def _reducer_audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if not _AUDIT_ACTIVE:
        return
    if event.startswith(("open", "socket", "subprocess", "os.")):
        _AUDIT_VIOLATIONS.append((event, args))


def test_ppm_reducer_runtime_audit_hook() -> None:
    """Run assess_criteria over the whole fixture corpus under sys.addaudithook.

    Fails if any open, socket, subprocess, or os.* event occurs within assess_criteria.
    """
    global _AUDIT_HOOK_INSTALLED, _AUDIT_ACTIVE

    if not _AUDIT_HOOK_INSTALLED:
        sys.addaudithook(_reducer_audit_hook)
        _AUDIT_HOOK_INSTALLED = True

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    fixtures_dir = repo_root / "ppm-v2" / "evals" / "fixtures"

    # Pre-load all fixture inputs into memory so file I/O does not trigger the hook
    corpus_inputs: list[dict[str, Any]] = []
    for fdir in sorted(fixtures_dir.iterdir()):
        if fdir.is_dir() and not fdir.name.startswith("."):
            inp_file = fdir / "inputs.json"
            if inp_file.exists():
                corpus_inputs.append(json.loads(inp_file.read_text(encoding="utf-8")))

    assert len(corpus_inputs) >= 19, f"Expected at least 19 fixtures, found {len(corpus_inputs)}"

    _AUDIT_VIOLATIONS.clear()
    _AUDIT_ACTIVE = True
    try:
        for inp in corpus_inputs:
            try:
                assess_criteria(
                    baseline=inp.get("baseline"),
                    claims=inp.get("claims", []),
                    evidence=inp.get("evidence", []),
                    waivers=inp.get("waivers", []),
                    assessed_revision=inp.get("assessed_revision"),
                    now=inp["now"],
                    evidence_ttl_days=inp.get("evidence_ttl_days", 7.0),
                )
            except InputRejected:
                # s29 legitimately raises InputRejected
                pass
    finally:
        _AUDIT_ACTIVE = False

    assert not _AUDIT_VIOLATIONS, (
        f"Audit hook intercepted forbidden runtime event(s) in ppm_reducer: {_AUDIT_VIOLATIONS}"
    )
