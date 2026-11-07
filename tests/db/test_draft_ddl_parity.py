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
    "tasks": [
        "uq_tasks_tenant_task_intent_key",
        "uq_tasks_tenant_client_key",
        "uq_tasks_tenant_id",
    ],
    "runs": [
        "uq_runs_tenant_run_id",
        "uq_runs_tenant_task_attempt",
        "fk_runs_tenant_task",
    ],
    "run_attempt_history": ["fk_run_attempt_history_tenant_task"],
    "task_intents": ["uq_task_intents_slot", "fk_task_intents_tenant_task"],
    "dispatch_intents": ["uq_dispatch_intents_op"],
    "action_executions": ["uq_action_executions_key"],
    "leases": ["uq_leases_resource"],
    "commands": ["fk_commands_tenant_task"],
    "event_log": ["uq_event_log_stream_offset", "uq_event_log_event_id"],
    "artifacts": ["uq_artifacts_tenant_artifact", "fk_artifacts_tenant_task"],
    "usage_ledger": ["uq_usage_ledger_provider_id"],
    "sources": [
        "uq_sources_tenant_canonical_root",
        "uq_sources_tenant_repo_identity",
        "uq_sources_tenant_source_id",
    ],
    "project_scopes": [
        "uq_project_scopes_source_metadata",
        "fk_project_scopes_tenant_source",
    ],
    "ppm_scan_schedules": [
        "uq_ppm_scan_schedules_source",
        "fk_ppm_scan_schedules_tenant_source",
    ],
}

EXPECTED_FOREIGN_KEYS = {
    "runs": [("fk_runs_tenant_task", ("tenant_id", "task_id"), "tasks", ("tenant_id", "id"))],
    "run_attempt_history": [
        ("fk_run_attempt_history_tenant_task", ("tenant_id", "task_id"), "tasks", ("tenant_id", "id"))
    ],
    "task_intents": [
        ("fk_task_intents_tenant_task", ("tenant_id", "task_id"), "tasks", ("tenant_id", "id"))
    ],
    "commands": [
        ("fk_commands_tenant_task", ("tenant_id", "task_id"), "tasks", ("tenant_id", "id"))
    ],
    "artifacts": [
        ("fk_artifacts_tenant_task", ("tenant_id", "task_id"), "tasks", ("tenant_id", "id"))
    ],
    "project_scopes": [
        ("fk_project_scopes_tenant_source", ("tenant_id", "source_id"), "sources", ("tenant_id", "source_id"))
    ],
    "ppm_scan_schedules": [
        ("fk_ppm_scan_schedules_tenant_source", ("tenant_id", "source_id"), "sources", ("tenant_id", "source_id"))
    ],
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
    draft_tables = _get_table_defs()
    for tbl_name, expected_fks in EXPECTED_FOREIGN_KEYS.items():
        stmt = draft_tables[tbl_name]
        draft_fks = [
            (
                elt.conname,
                tuple(a.sval for a in elt.fk_attrs),
                elt.pktable.relname,
                tuple(a.sval for a in elt.pk_attrs),
            )
            for elt in stmt.tableElts
            if hasattr(elt, "conname") and hasattr(elt, "fk_attrs") and elt.fk_attrs
        ]
        meta_table = metadata.tables[tbl_name]
        meta_fks = [
            (
                fkc.name,
                tuple(c.name for c in fkc.columns),
                fkc.referred_table.name,
                tuple(elem.column.name for elem in fkc.elements),
            )
            for fkc in meta_table.foreign_key_constraints
        ]
        for exp_conname, exp_cols, exp_target_table, exp_target_cols in expected_fks:
            expected_tuple = (exp_conname, exp_cols, exp_target_table, exp_target_cols)
            assert expected_tuple in draft_fks, (
                f"Draft table '{tbl_name}' missing FK: {expected_tuple}. Found: {draft_fks}"
            )
            assert expected_tuple in meta_fks, (
                f"Metadata table '{tbl_name}' missing FK: {expected_tuple}. Found: {meta_fks}"
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
