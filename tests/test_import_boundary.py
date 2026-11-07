"""Test import boundary for ppm_reducer.

Rule: ppm_reducer has no I/O, does not read .env, and never imports eap, requests,
socket, subprocess, importlib, os, pathlib, http, io, or calls open/__import__.
"""

import ast
import pathlib

FORBIDDEN_MODULES = {
    "eap",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "dotenv",
    "importlib",
    "os",
    "pathlib",
    "http",
    "io",
}
FORBIDDEN_CALLS = {"open", "__import__"}


def test_ppm_reducer_forbidden_imports_and_calls():
    # Resolve directory relative to this test file so it passes from any working directory
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    reducer_dir = repo_root / "ppm-v2" / "reducer" / "ppm_reducer"
    py_files = list(reducer_dir.rglob("*.py"))
    assert len(py_files) > 0, f"No Python files found in {reducer_dir}"

    violations = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_name = alias.name.split(".")[0]
                    if top_name in FORBIDDEN_MODULES:
                        violations.append((py_file.name, node.lineno, f"import {top_name}"))
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_name = node.module.split(".")[0]
                if top_name in FORBIDDEN_MODULES:
                    violations.append((py_file.name, node.lineno, f"from {top_name} import ..."))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                    violations.append((py_file.name, node.lineno, f"call to {func.id}()"))
                elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
                    violations.append((py_file.name, node.lineno, f"call to {func.attr}()"))

    assert not violations, f"Forbidden imports/calls detected in ppm_reducer: {violations}"
