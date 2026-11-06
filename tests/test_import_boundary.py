"""Test import boundary for ppm_reducer.

Rule: ppm_reducer has no I/O, does not read .env, and never imports eap, requests,
socket, or subprocess.
"""

import ast
import pathlib

FORBIDDEN_MODULES = {"eap", "requests", "socket", "subprocess", "urllib", "dotenv"}


def test_ppm_reducer_forbidden_imports():
    reducer_dir = pathlib.Path("ppm-v2/reducer/ppm_reducer")
    py_files = list(reducer_dir.rglob("*.py"))
    assert len(py_files) > 0, "No Python files found in ppm_reducer"

    violations = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_name = alias.name.split(".")[0]
                    if top_name in FORBIDDEN_MODULES:
                        violations.append((py_file.name, node.lineno, top_name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_name = node.module.split(".")[0]
                if top_name in FORBIDDEN_MODULES:
                    violations.append((py_file.name, node.lineno, top_name))

    assert not violations, f"Forbidden imports detected in ppm_reducer: {violations}"
