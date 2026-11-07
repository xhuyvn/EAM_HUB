"""Boundary tests for eap.contracts and ppm_reducer (SC-01 / P0-WP2).

Rules:
1. ppm_reducer must NOT import eap or eap.contracts (stays pure).
2. eap.contracts must have no I/O, DB, network, or settings dependency:
   it must not import sqlalchemy, psycopg, redis, qdrant_client, requests,
   httpx, socket, subprocess, urllib, eap.db, or eap.config.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CONTRACTS_FORBIDDEN_MODULES: set[str] = {
    "sqlalchemy",
    "alembic",
    "psycopg",
    "psycopg2",
    "redis",
    "qdrant_client",
    "requests",
    "httpx",
    "http",
    "socket",
    "subprocess",
    "urllib",
    "eap.db",
    "eap.config",
    "eap.api",
    "eap.run",
    "eap.task",
    "eap.hud",
}

CONTRACTS_FORBIDDEN_CALLS: set[str] = {"open", "__import__"}


def test_contracts_pure_boundary() -> None:
    contracts_dir = REPO_ROOT / "src" / "eap" / "contracts"
    py_files = list(contracts_dir.rglob("*.py"))
    assert len(py_files) > 0, f"No python files in {contracts_dir}"

    violations: list[tuple[str, int, str]] = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in CONTRACTS_FORBIDDEN_MODULES:
                        if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                            violations.append((py_file.name, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in CONTRACTS_FORBIDDEN_MODULES:
                    if node.module == forbidden or node.module.startswith(forbidden + "."):
                        violations.append((py_file.name, node.lineno, f"from {node.module} import ..."))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in CONTRACTS_FORBIDDEN_CALLS:
                    violations.append((py_file.name, node.lineno, f"call to {func.id}()"))
                elif isinstance(func, ast.Attribute) and func.attr in CONTRACTS_FORBIDDEN_CALLS:
                    violations.append((py_file.name, node.lineno, f"call to {func.attr}()"))

    assert not violations, f"Forbidden dependencies/calls detected in eap.contracts: {violations}"


def test_ppm_reducer_does_not_import_eap_contracts() -> None:
    reducer_dir = REPO_ROOT / "ppm-v2" / "reducer" / "ppm_reducer"
    py_files = list(reducer_dir.rglob("*.py"))
    assert len(py_files) > 0

    violations: list[tuple[str, int, str]] = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "eap" or alias.name.startswith("eap."):
                        violations.append((py_file.name, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom) and node.module and (node.module == "eap" or node.module.startswith("eap.")):
                violations.append((py_file.name, node.lineno, f"from {node.module} import ..."))

    assert not violations, f"ppm_reducer must not import eap.contracts: {violations}"
