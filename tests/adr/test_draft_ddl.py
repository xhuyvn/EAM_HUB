"""Test verification of the draft core DDL (migrations/draft/0001_core.sql).

Parses the draft SQL using pglast (PostgreSQL native AST parser) and asserts:
1. Every listed table exists (all 16 tables required by P1a-WP1 and DG-1/DG-2).
2. Every table contains a 'tenant_id' column.
3. Every required UNIQUE, Primary Key, and Foreign Key constraint exists.
4. The 'outbox' table contains no offset column (ADR-020 invariant).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pglast
from pglast.enums.parsenodes import ConstrType

ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = ROOT / "migrations/draft/0001_core.sql"

EXPECTED_TABLES = [
    "tasks",
    "runs",
    "run_attempt_history",
    "task_intents",
    "dispatch_intents",
    "action_executions",
    "leases",
    "commands",
    "outbox",
    "event_log",
    "artifacts",
    "usage_ledger",
    "sources",
    "project_scopes",
    "ppm_scan_schedules",
    "project_current_assessment",
]

EXPECTED_NAMED_CONSTRAINTS = {
    "tasks": ["uq_tasks_tenant_task_intent_key", "uq_tasks_tenant_client_key"],
    "runs": ["uq_runs_tenant_run_id", "uq_runs_tenant_task_attempt"],
    "task_intents": ["uq_task_intents_slot"],
    "dispatch_intents": ["uq_dispatch_intents_op"],
    "action_executions": ["uq_action_executions_key"],
    "leases": ["uq_leases_resource"],
    "event_log": ["uq_event_log_stream_offset", "uq_event_log_event_id"],
    "artifacts": ["uq_artifacts_tenant_artifact"],
    "usage_ledger": ["uq_usage_ledger_provider_id"],
    "sources": ["uq_sources_tenant_canonical_root", "uq_sources_tenant_repo_identity"],
    "project_scopes": ["uq_project_scopes_source_metadata"],
    "ppm_scan_schedules": ["uq_ppm_scan_schedules_source"],
}

EXPECTED_FOREIGN_KEYS = {
    "runs": [("task_id", "tasks")],
    "run_attempt_history": [("task_id", "tasks")],
    "task_intents": [("task_id", "tasks")],
    "commands": [("task_id", "tasks")],
    "artifacts": [("task_id", "tasks")],
    "project_scopes": [("source_id", "sources")],
    "ppm_scan_schedules": [("source_id", "sources")],
}


def _get_table_defs() -> dict[str, Any]:
    assert DDL_PATH.exists(), f"DDL file missing at {DDL_PATH}"
    sql = DDL_PATH.read_text(encoding="utf-8")
    parsed = pglast.parse_sql(sql)
    tables: dict[str, Any] = {}
    for raw in parsed:
        stmt = raw.stmt
        if stmt is not None and hasattr(stmt, "relation") and hasattr(stmt.relation, "relname"):
            tables[stmt.relation.relname] = stmt
    return tables


def test_all_16_tables_exist() -> None:
    tables = _get_table_defs()
    assert len(tables) == 16, f"Expected 16 tables, found {len(tables)}"
    for tbl in EXPECTED_TABLES:
        assert tbl in tables, f"Missing required table: {tbl}"


def test_every_table_has_tenant_id() -> None:
    tables = _get_table_defs()
    for tbl_name, stmt in tables.items():
        columns = [
            elt.colname
            for elt in stmt.tableElts
            if hasattr(elt, "colname")
        ]
        assert "tenant_id" in columns, f"Table '{tbl_name}' is missing tenant_id column"


def test_every_table_has_primary_key() -> None:
    tables = _get_table_defs()
    for tbl_name, stmt in tables.items():
        has_pk = False
        # Check column-level PK
        for elt in stmt.tableElts:
            if hasattr(elt, "constraints") and elt.constraints:
                for c in elt.constraints:
                    if getattr(c, "contype", None) == ConstrType.CONSTR_PRIMARY:
                        has_pk = True
                        break
            # Check table-level PK
            if getattr(elt, "contype", None) == ConstrType.CONSTR_PRIMARY:
                has_pk = True
                break
        assert has_pk, f"Table '{tbl_name}' must have a primary key"


def test_expected_named_unique_constraints() -> None:
    tables = _get_table_defs()
    for tbl_name, expected_connames in EXPECTED_NAMED_CONSTRAINTS.items():
        assert tbl_name in tables, f"Table {tbl_name} missing"
        stmt = tables[tbl_name]
        table_constraints = [
            elt.conname
            for elt in stmt.tableElts
            if hasattr(elt, "conname") and elt.conname
        ]
        for con in expected_connames:
            assert con in table_constraints, (
                f"Table '{tbl_name}' is missing expected constraint '{con}'. Found: {table_constraints}"
            )


def test_expected_foreign_keys() -> None:
    tables = _get_table_defs()
    for tbl_name, fk_list in EXPECTED_FOREIGN_KEYS.items():
        stmt = tables[tbl_name]
        for col_name, target_table in fk_list:
            found_fk = False
            for elt in stmt.tableElts:
                if getattr(elt, "colname", None) == col_name and hasattr(elt, "constraints"):
                    for c in elt.constraints or []:
                        if (
                            getattr(c, "contype", None) == ConstrType.CONSTR_FOREIGN
                            and hasattr(c, "pktable")
                            and c.pktable.relname == target_table
                        ):
                            found_fk = True
                            break
            assert found_fk, f"Table '{tbl_name}' missing FK from '{col_name}' to '{target_table}'"


def test_outbox_no_offset_column() -> None:
    """ADR-020 invariant: outbox table MUST NOT possess an offset column."""
    tables = _get_table_defs()
    outbox_stmt = tables["outbox"]
    columns = [
        elt.colname
        for elt in outbox_stmt.tableElts
        if hasattr(elt, "colname")
    ]
    assert "offset" not in columns, "outbox table must not have 'offset' column (ADR-020)"
    assert "ingest_offset" not in columns, "outbox table must not have 'ingest_offset' column (ADR-020)"
    assert "stream_offset" not in columns, "outbox table must not have 'stream_offset' column (ADR-020)"


def test_event_log_has_stream_offset() -> None:
    """event_log MUST possess ingest_offset assigned by relay."""
    tables = _get_table_defs()
    event_stmt = tables["event_log"]
    columns = [
        elt.colname
        for elt in event_stmt.tableElts
        if hasattr(elt, "colname")
    ]
    assert "ingest_offset" in columns, "event_log table must have 'ingest_offset' column"


def test_project_scopes_allows_multiple_scopes_per_project() -> None:
    """W7-3: Owner decision: a project may have multiple scopes; each scope belongs to exactly one project."""
    tables = _get_table_defs()
    stmt = tables["project_scopes"]
    table_constraints = [
        elt.conname
        for elt in stmt.tableElts
        if hasattr(elt, "conname") and elt.conname
    ]
    assert "uq_project_scopes_tenant_project" not in table_constraints, (
        "project_scopes must not restrict project_id to a single scope"
    )
    assert "uq_project_scopes_source_metadata" in table_constraints

