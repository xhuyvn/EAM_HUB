"""Parity test ensuring src/eap/db metadata matches migrations/draft/0001_core.sql.

Parses migrations/draft/0001_core.sql using pglast to catch any schema drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pglast

from eap.db import metadata

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


def test_metadata_defines_all_draft_tables() -> None:
    """Every table in the draft DDL exists in the SQLAlchemy Core metadata."""
    draft_tables = _get_table_defs()
    assert len(draft_tables) == 16
    for tbl_name in EXPECTED_TABLES:
        assert tbl_name in draft_tables, f"Table {tbl_name} missing from draft DDL"
        assert tbl_name in metadata.tables, f"Table {tbl_name} missing from src/eap/db metadata"


def test_column_parity_between_draft_and_metadata() -> None:
    """For every table, column names in the draft DDL match the SQLAlchemy Core metadata."""
    draft_tables = _get_table_defs()
    for tbl_name, stmt in draft_tables.items():
        draft_cols = [
            elt.colname
            for elt in stmt.tableElts
            if hasattr(elt, "colname") and elt.colname
        ]
        meta_table = metadata.tables[tbl_name]
        meta_cols = list(meta_table.columns.keys())

        assert set(draft_cols) == set(meta_cols), (
            f"Column mismatch on '{tbl_name}': draft={sorted(draft_cols)} vs meta={sorted(meta_cols)}"
        )


def test_unique_constraint_parity() -> None:
    """Named unique constraints in the draft DDL match the SQLAlchemy Core metadata."""
    draft_tables = _get_table_defs()
    for tbl_name, expected_connames in EXPECTED_NAMED_CONSTRAINTS.items():
        stmt = draft_tables[tbl_name]
        draft_named = [
            elt.conname
            for elt in stmt.tableElts
            if hasattr(elt, "conname") and elt.conname
        ]
        meta_table = metadata.tables[tbl_name]
        meta_named = [
            c.name for c in meta_table.constraints if getattr(c, "name", None)
        ]

        for con in expected_connames:
            assert con in draft_named, f"Constraint '{con}' missing from draft table '{tbl_name}'"
            assert con in meta_named, f"Constraint '{con}' missing from metadata table '{tbl_name}'"


def test_foreign_key_parity() -> None:
    """Foreign key relationships in the draft DDL match the SQLAlchemy Core metadata."""
    for tbl_name, expected_fks in EXPECTED_FOREIGN_KEYS.items():
        meta_table = metadata.tables[tbl_name]
        meta_fks = [
            (fk.parent.name, fk.column.table.name)
            for fk in meta_table.foreign_keys
        ]
        for col_name, target_table in expected_fks:
            assert (col_name, target_table) in meta_fks, (
                f"Table '{tbl_name}' missing FK from '{col_name}' to '{target_table}' in metadata"
            )


def test_tenant_id_presence_parity() -> None:
    """Every table in both draft and metadata has a non-nullable tenant_id column."""
    draft_tables = _get_table_defs()
    for tbl_name, stmt in draft_tables.items():
        draft_cols = [
            elt.colname for elt in stmt.tableElts if hasattr(elt, "colname") and elt.colname
        ]
        assert "tenant_id" in draft_cols, f"Draft table '{tbl_name}' missing tenant_id"

        meta_table = metadata.tables[tbl_name]
        assert "tenant_id" in meta_table.columns, f"Metadata table '{tbl_name}' missing tenant_id"
        assert meta_table.columns["tenant_id"].nullable is False, (
            f"Metadata table '{tbl_name}.tenant_id' must be NOT NULL"
        )
