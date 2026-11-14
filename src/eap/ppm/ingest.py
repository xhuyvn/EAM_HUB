"""ingest.py — Deterministic, idempotent PPM snapshot ingestor (P2a-WP3).

Turns a committed ``project_snapshots`` row into durable, deduplicated domain
records (``ppm_ingested_artifacts``, ``ppm_updates``, ``ppm_notes``,
``ppm_ingest_checkpoints``) and emits ``ppm.ingest.completed`` to ``outbox``
in the same transaction.

Invariants:
- Ingest never decides status or acceptance. Claims and notes are untrusted data.
- Re-ingesting the same snapshot produces zero new rows and does not advance
  the checkpoint again.
- Same ``update_id`` + same bytes -> duplicate no-op.
- Same ``update_id`` + different bytes -> conflict (first payload preserved).
- ``contract_version`` '2.0' and '2.1' accepted; 2.0 using 2.1 fields -> rejected;
  2.2+ / 3.0 / v1 ``schema_version: 1`` -> quarantined ``unsupported_version``.
- Notes never alter status, scope, or acceptance; body stored verbatim with
  ``max_file_bytes`` cap.
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from eap.db import (
    collector_snapshots,
    location_registrations,
    outbox,
    ppm_ingest_checkpoints,
    ppm_ingested_artifacts,
    ppm_notes,
    ppm_updates,
    project_snapshots,
)
from ppm_reducer.contract_validation import (
    parse_ts,
    semantic_doc_errors,
    uses_21_fields,
)

DEFAULT_MAX_FILE_BYTES = 2_097_152  # 2 MiB
ALLOWED_NOTE_KINDS = frozenset({"idea", "blocker", "question", "observation", "unknown"})
ALLOWED_ARTIFACT_KINDS = frozenset({"update", "status", "project", "acceptance", "decision", "note"})


class SnapshotNotFoundError(ValueError):
    """Raised when the target project_snapshot_id does not exist for tenant_id."""


@dataclass
class IngestReport:
    """Summary of ingesting a single project snapshot."""

    project_snapshot_id: str
    snapshot_id: str
    source_id: str
    project_id: str
    snapshot_seq: int
    already_ingested: bool = False
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "ingested": 0,
            "duplicate": 0,
            "conflict": 0,
            "quarantined": 0,
            "rejected": 0,
        }
    )
    gaps: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    outbox_event_id: str | None = None


@functools.lru_cache(maxsize=1)
def _get_contract_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[3] / "ppm-v2" / "schemas" / "contract.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _canonical_sha256(obj: Any) -> str:
    encoded = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _infer_kind(rel_path: str, doc: Any = None) -> str:
    if isinstance(doc, dict):
        k = doc.get("kind")
        if isinstance(k, str) and k in ALLOWED_ARTIFACT_KINDS:
            return k
    norm = rel_path.replace("\\", "/")
    if "/updates/" in norm or norm.startswith("updates/"):
        return "update"
    if norm.endswith("/status.yaml") or norm == "status.yaml":
        return "status"
    if norm.endswith("/project.yaml") or norm == "project.yaml":
        return "project"
    if norm.endswith("/acceptance.yaml") or norm == "acceptance.yaml":
        return "acceptance"
    if "/decisions/" in norm or norm.startswith("decisions/"):
        return "decision"
    return "note"


def _record_artifact(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    artifact_path: str,
    content_hash: str,
    snapshot_id: str,
    kind: str,
    outcome: str,
    reason_code: str | None,
    detail: dict[str, Any] | None = None,
    now: dt.datetime,
) -> tuple[int, bool]:
    """Insert into ppm_ingested_artifacts if identity not yet seen.

    Returns ``(artifact_id, inserted)``.
    """
    existing = conn.execute(
        sa.select(ppm_ingested_artifacts.c.id).where(
            ppm_ingested_artifacts.c.tenant_id == tenant_id,
            ppm_ingested_artifacts.c.project_id == project_id,
            ppm_ingested_artifacts.c.source_id == source_id,
            ppm_ingested_artifacts.c.artifact_path == artifact_path,
            ppm_ingested_artifacts.c.content_hash == content_hash,
        )
    ).scalar_one_or_none()

    if existing is not None:
        return int(existing), False

    res = conn.execute(
        ppm_ingested_artifacts.insert()
        .values(
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            artifact_path=artifact_path,
            content_hash=content_hash,
            first_seen_snapshot_id=snapshot_id,
            kind=kind,
            outcome=outcome,
            reason_code=reason_code,
            detail=detail or {},
            created_at=now,
        )
        .returning(ppm_ingested_artifacts.c.id)
    )
    return int(res.scalar_one()), True


def _detect_cycle_nodes(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    project_id: str,
    start_update_id: str,
    initial_target_id: str,
) -> list[str] | None:
    """Walk supersession pointers from *initial_target_id*; return cycle node IDs if *start_update_id* is reached."""
    rows = conn.execute(
        sa.select(
            ppm_updates.c.update_id,
            ppm_updates.c.supersedes_update_id,
        ).where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
        )
    ).all()
    parent_map: dict[str, str | None] = {r[0]: r[1] for r in rows}
    parent_map[start_update_id] = initial_target_id

    visited: list[str] = [start_update_id]
    seen: set[str] = {start_update_id}
    cur: str | None = initial_target_id
    while cur is not None:
        if cur in seen:
            idx = visited.index(cur)
            return visited[idx:]
        visited.append(cur)
        seen.add(cur)
        cur = parent_map.get(cur)
    return None


def _ingest_update(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    project_snapshot_id: str,
    artifact_path: str,
    content_hash: str,
    doc: dict[str, Any],
    now: dt.datetime,
    report: IngestReport,
) -> None:
    update_id = str(doc["update_id"])
    contract_version = str(doc["contract_version"])
    supersedes_id: str | None = doc.get("supersedes_update_id")

    existing = conn.execute(
        sa.select(
            ppm_updates.c.id,
            ppm_updates.c.content_hash,
            ppm_updates.c.supersession_state,
        ).where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
            ppm_updates.c.update_id == update_id,
        )
    ).mappings().first()

    if existing is not None:
        if existing["content_hash"] == content_hash:
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=content_hash,
                snapshot_id=project_snapshot_id,
                kind="update",
                outcome="duplicate",
                reason_code="duplicate_update_id",
                now=now,
            )
            report.counts["duplicate"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": "update", "outcome": "duplicate", "reason_code": "duplicate_update_id"}
            )
            return

        # Same update_id + different bytes -> conflict (never overwrite existing payload)
        _record_artifact(
            conn,
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            artifact_path=artifact_path,
            content_hash=content_hash,
            snapshot_id=project_snapshot_id,
            kind="update",
            outcome="conflict",
            reason_code="update_id_conflict",
            detail={"update_id": update_id, "existing_hash": existing["content_hash"]},
            now=now,
        )
        report.counts["conflict"] += 1
        report.artifacts.append(
            {"path": artifact_path, "kind": "update", "outcome": "conflict", "reason_code": "update_id_conflict"}
        )
        return

    artifact_id, inserted_art = _record_artifact(
        conn,
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        artifact_path=artifact_path,
        content_hash=content_hash,
        snapshot_id=project_snapshot_id,
        kind="update",
        outcome="ingested",
        reason_code=None,
        now=now,
    )
    if not inserted_art:
        report.counts["duplicate"] += 1
        return

    supersession_state = "active"
    if supersedes_id is not None:
        target = conn.execute(
            sa.select(
                ppm_updates.c.id,
                ppm_updates.c.supersession_state,
            ).where(
                ppm_updates.c.tenant_id == tenant_id,
                ppm_updates.c.project_id == project_id,
                ppm_updates.c.update_id == supersedes_id,
            )
        ).mappings().first()

        if target is None:
            supersession_state = "orphan_supersedes"
        else:
            # Check if another update already supersedes target
            rivals = conn.execute(
                sa.select(ppm_updates.c.update_id).where(
                    ppm_updates.c.tenant_id == tenant_id,
                    ppm_updates.c.project_id == project_id,
                    ppm_updates.c.supersedes_update_id == supersedes_id,
                    ppm_updates.c.update_id != update_id,
                )
            ).scalars().all()

            cycle_nodes = _detect_cycle_nodes(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                start_update_id=update_id,
                initial_target_id=supersedes_id,
            )

            if cycle_nodes is not None:
                supersession_state = "conflict"
                conn.execute(
                    ppm_updates.update()
                    .where(
                        ppm_updates.c.tenant_id == tenant_id,
                        ppm_updates.c.project_id == project_id,
                        ppm_updates.c.update_id.in_(cycle_nodes),
                    )
                    .values(supersession_state="conflict")
                )
            elif rivals:
                supersession_state = "conflict"
                conn.execute(
                    ppm_updates.update()
                    .where(
                        ppm_updates.c.tenant_id == tenant_id,
                        ppm_updates.c.project_id == project_id,
                        ppm_updates.c.update_id.in_(list(rivals)),
                    )
                    .values(supersession_state="conflict")
                )
            else:
                if target["supersession_state"] != "conflict":
                    conn.execute(
                        ppm_updates.update()
                        .where(
                            ppm_updates.c.tenant_id == tenant_id,
                            ppm_updates.c.project_id == project_id,
                            ppm_updates.c.update_id == supersedes_id,
                        )
                        .values(supersession_state="superseded")
                    )

    recorded_at_val: dt.datetime | None = None
    if "recorded_at" in doc and doc["recorded_at"] is not None:
        try:
            recorded_at_val = parse_ts(doc["recorded_at"])
        except ValueError:
            recorded_at_val = None

    conn.execute(
        ppm_updates.insert().values(
            id=f"upd-{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            project_id=project_id,
            update_id=update_id,
            content_hash=content_hash,
            contract_version=contract_version,
            supersedes_update_id=supersedes_id,
            supersession_state=supersession_state,
            payload=doc,
            source_id=source_id,
            source_snapshot_id=project_snapshot_id,
            artifact_path=artifact_path,
            ingested_artifact_id=artifact_id,
            recorded_at=recorded_at_val,
            ingested_at=now,
        )
    )

    # Re-resolve any earlier orphan_supersedes rows that point to this newly arrived update_id
    orphans = conn.execute(
        sa.select(ppm_updates.c.update_id).where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
            ppm_updates.c.supersedes_update_id == update_id,
            ppm_updates.c.supersession_state == "orphan_supersedes",
        )
    ).scalars().all()

    if len(orphans) == 1:
        orphan_uid = orphans[0]
        cycle_nodes = _detect_cycle_nodes(
            conn,
            tenant_id=tenant_id,
            project_id=project_id,
            start_update_id=update_id,
            initial_target_id=supersedes_id,
        ) if supersedes_id else None
        # Also check if orphan -> update_id closes a cycle
        if cycle_nodes is None:
            cycle_nodes = _detect_cycle_nodes(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                start_update_id=orphan_uid,
                initial_target_id=update_id,
            )
        if cycle_nodes is not None:
            conn.execute(
                ppm_updates.update()
                .where(
                    ppm_updates.c.tenant_id == tenant_id,
                    ppm_updates.c.project_id == project_id,
                    ppm_updates.c.update_id.in_(cycle_nodes),
                )
                .values(supersession_state="conflict")
            )
        else:
            # Check if something else already superseded the orphan
            orphan_superseded = conn.execute(
                sa.select(ppm_updates.c.update_id).where(
                    ppm_updates.c.tenant_id == tenant_id,
                    ppm_updates.c.project_id == project_id,
                    ppm_updates.c.supersedes_update_id == orphan_uid,
                )
            ).first()
            new_orphan_state = "superseded" if orphan_superseded else "active"
            conn.execute(
                ppm_updates.update()
                .where(
                    ppm_updates.c.tenant_id == tenant_id,
                    ppm_updates.c.project_id == project_id,
                    ppm_updates.c.update_id == orphan_uid,
                )
                .values(supersession_state=new_orphan_state)
            )
            if supersession_state != "conflict":
                conn.execute(
                    ppm_updates.update()
                    .where(
                        ppm_updates.c.tenant_id == tenant_id,
                        ppm_updates.c.project_id == project_id,
                        ppm_updates.c.update_id == update_id,
                    )
                    .values(supersession_state="superseded")
                )
    elif len(orphans) > 1:
        conn.execute(
            ppm_updates.update()
            .where(
                ppm_updates.c.tenant_id == tenant_id,
                ppm_updates.c.project_id == project_id,
                ppm_updates.c.update_id.in_(list(orphans)),
            )
            .values(supersession_state="conflict")
        )

    report.counts["ingested"] += 1
    report.artifacts.append(
        {"path": artifact_path, "kind": "update", "outcome": "ingested", "reason_code": None}
    )


def _process_partial_reasons(
    conn: sa.Connection | Session,
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    project_snapshot_id: str,
    partial_reasons: list[str],
    manifest: dict[str, Any],
    now: dt.datetime,
    report: IngestReport,
) -> None:
    """Record collector-rejected files (duplicate YAML keys, oversized) without crashing."""
    for reason in partial_reasons:
        if not isinstance(reason, str):
            continue
        if reason.startswith("parse_error: "):
            rest = reason[len("parse_error: "):]
            rel_path, _, msg = rest.partition(": ")
            reason_code = (
                "duplicate_yaml_key"
                if ("Duplicate YAML key" in msg or "Duplicate JSON key" in msg)
                else "parse_error"
            )
        elif reason.startswith("file_oversized: "):
            rest = reason[len("file_oversized: "):]
            rel_path, _, msg = rest.partition(": ")
            reason_code = "file_oversized"
        else:
            continue

        rel_path = rel_path.strip()
        if not rel_path:
            continue
        c_hash = (
            manifest.get(rel_path, {}).get("sha256")
            or hashlib.sha256(reason.encode("utf-8")).hexdigest()
        )
        kind = _infer_kind(rel_path)
        _, inserted = _record_artifact(
            conn,
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            artifact_path=rel_path,
            content_hash=c_hash,
            snapshot_id=project_snapshot_id,
            kind=kind,
            outcome="rejected",
            reason_code=reason_code,
            detail={"collector_reason": reason},
            now=now,
        )
        outcome = "rejected" if inserted else "duplicate"
        report.counts[outcome] += 1
        report.artifacts.append(
            {"path": rel_path, "kind": kind, "outcome": outcome, "reason_code": reason_code}
        )


def ingest_snapshot(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_snapshot_id: str,
) -> IngestReport:
    """Ingest a committed ``project_snapshots`` row into durable domain tables.

    Runs inside the caller's active transaction (does not commit).
    """
    psnap = conn.execute(
        sa.select(project_snapshots).where(
            project_snapshots.c.tenant_id == tenant_id,
            project_snapshots.c.id == project_snapshot_id,
        )
    ).mappings().first()

    if psnap is None:
        raise SnapshotNotFoundError(
            f"project_snapshot '{project_snapshot_id}' not found for tenant '{tenant_id}'"
        )

    snapshot_id = str(psnap["snapshot_id"])
    source_id = str(psnap["source_id"])
    project_id = str(psnap["project_id"])
    snapshot_seq = int(psnap["snapshot_seq"])
    metadata_root = str(psnap["metadata_root"])

    report = IngestReport(
        project_snapshot_id=project_snapshot_id,
        snapshot_id=snapshot_id,
        source_id=source_id,
        project_id=project_id,
        snapshot_seq=snapshot_seq,
    )

    # Lock checkpoint row if it exists to serialize concurrent ingests for same (tenant, source, project)
    ckpt = conn.execute(
        sa.select(ppm_ingest_checkpoints)
        .where(
            ppm_ingest_checkpoints.c.tenant_id == tenant_id,
            ppm_ingest_checkpoints.c.source_id == source_id,
            ppm_ingest_checkpoints.c.project_id == project_id,
        )
        .with_for_update()
    ).mappings().first()

    if ckpt is not None and (
        ckpt["last_ingested_snapshot_id"] == project_snapshot_id
        or int(ckpt["last_ingested_snapshot_seq"]) >= snapshot_seq
    ):
        report.already_ingested = True
        return report

    csnap = conn.execute(
        sa.select(collector_snapshots.c.manifest).where(
            collector_snapshots.c.tenant_id == tenant_id,
            collector_snapshots.c.id == snapshot_id,
        )
    ).mappings().first()
    manifest: dict[str, Any] = dict(csnap["manifest"]) if csnap and csnap["manifest"] else {}

    loc_row = conn.execute(
        sa.select(location_registrations.c.max_file_bytes).where(
            location_registrations.c.tenant_id == tenant_id,
            location_registrations.c.source_id == source_id,
            location_registrations.c.metadata_root == metadata_root,
        )
    ).mappings().first()
    max_file_bytes = int(loc_row["max_file_bytes"]) if loc_row else DEFAULT_MAX_FILE_BYTES

    now = dt.datetime.now(dt.UTC)
    validator = _get_contract_validator()

    # 1. Record collector-rejected files from partial_reasons
    _process_partial_reasons(
        conn,
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        project_snapshot_id=project_snapshot_id,
        partial_reasons=list(psnap["partial_reasons"] or []),
        manifest=manifest,
        now=now,
        report=report,
    )

    # 2. Process structured claims (updates, project, status, acceptance)
    for claim in list(psnap["claims"] or []):
        if not isinstance(claim, dict):
            continue
        artifact_path = str(claim.get("path", ""))
        doc = claim.get("data")
        c_hash = (
            claim.get("content_hash")
            or manifest.get(artifact_path, {}).get("sha256")
            or _canonical_sha256(doc)
        )
        kind = _infer_kind(artifact_path, doc)

        if not isinstance(doc, dict):
            _, inserted = _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="rejected",
                reason_code="invalid_document",
                now=now,
            )
            outcome = "rejected" if inserted else "duplicate"
            report.counts[outcome] += 1
            continue

        # Check artifact-level dedup before validation
        existing_art = conn.execute(
            sa.select(ppm_ingested_artifacts.c.id).where(
                ppm_ingested_artifacts.c.tenant_id == tenant_id,
                ppm_ingested_artifacts.c.project_id == project_id,
                ppm_ingested_artifacts.c.source_id == source_id,
                ppm_ingested_artifacts.c.artifact_path == artifact_path,
                ppm_ingested_artifacts.c.content_hash == c_hash,
            )
        ).scalar_one_or_none()
        if existing_art is not None:
            report.counts["duplicate"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "duplicate", "reason_code": "duplicate_artifact"}
            )
            continue

        # Version check (quarantine unsupported versions: 2.2+, 3.0, v1 schema_version: 1)
        if "schema_version" in doc and "contract_version" not in doc:
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="quarantined",
                reason_code="unsupported_version",
                detail={"schema_version": doc.get("schema_version")},
                now=now,
            )
            report.counts["quarantined"] += 1
            report.gaps.append(
                {
                    "kind": kind,
                    "artifact_path": artifact_path,
                    "reason_code": "unsupported_version",
                    "detail": f"v1 schema_version={doc.get('schema_version')!r} requires explicit migration",
                }
            )
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "quarantined", "reason_code": "unsupported_version"}
            )
            continue

        cv = doc.get("contract_version")
        if cv not in ("2.0", "2.1"):
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="quarantined",
                reason_code="unsupported_version",
                detail={"contract_version": cv},
                now=now,
            )
            report.counts["quarantined"] += 1
            report.gaps.append(
                {
                    "kind": kind,
                    "artifact_path": artifact_path,
                    "reason_code": "unsupported_version",
                    "detail": f"unsupported contract_version={cv!r}",
                }
            )
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "quarantined", "reason_code": "unsupported_version"}
            )
            continue

        # Semantic checks (2.0 using 2.1 fields, >24h future timestamps, self-supersession, etc.)
        sem_errors = semantic_doc_errors(doc)
        if sem_errors:
            reason_code = (
                "v2_1_field_in_v2_0"
                if (cv == "2.0" and uses_21_fields(doc))
                else "semantic_validation_failed"
            )
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="rejected",
                reason_code=reason_code,
                detail={"errors": sem_errors},
                now=now,
            )
            report.counts["rejected"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "rejected", "reason_code": reason_code}
            )
            continue

        # JSON Schema validation
        schema_errors = [e.message for e in validator.iter_errors(doc)]
        if schema_errors:
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="rejected",
                reason_code="schema_validation_failed",
                detail={"errors": schema_errors[:10]},
                now=now,
            )
            report.counts["rejected"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "rejected", "reason_code": "schema_validation_failed"}
            )
            continue

        # Project ID consistency check
        if "project_id" in doc and doc["project_id"] != project_id:
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="rejected",
                reason_code="project_id_mismatch",
                detail={"declared_project_id": doc["project_id"], "expected_project_id": project_id},
                now=now,
            )
            report.counts["rejected"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "rejected", "reason_code": "project_id_mismatch"}
            )
            continue

        if kind == "update":
            if Path(artifact_path).stem != str(doc["update_id"]):
                _record_artifact(
                    conn,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_id=source_id,
                    artifact_path=artifact_path,
                    content_hash=c_hash,
                    snapshot_id=project_snapshot_id,
                    kind="update",
                    outcome="rejected",
                    reason_code="filename_update_id_mismatch",
                    detail={"update_id": doc["update_id"], "stem": Path(artifact_path).stem},
                    now=now,
                )
                report.counts["rejected"] += 1
                report.artifacts.append(
                    {"path": artifact_path, "kind": "update", "outcome": "rejected", "reason_code": "filename_update_id_mismatch"}
                )
                continue

            _ingest_update(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                project_snapshot_id=project_snapshot_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                doc=doc,
                now=now,
                report=report,
            )
        else:
            _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind=kind,
                outcome="ingested",
                reason_code=None,
                now=now,
            )
            report.counts["ingested"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": kind, "outcome": "ingested", "reason_code": None}
            )

    # 3. Process notes verbatim (never alter status, scope, or acceptance)
    for note in list(psnap["notes"] or []):
        if not isinstance(note, dict):
            continue
        artifact_path = str(note.get("path", ""))
        body = str(note.get("content", ""))
        byte_length = len(body.encode("utf-8"))
        c_hash = (
            note.get("content_hash")
            or hashlib.sha256(body.encode("utf-8")).hexdigest()
        )
        raw_kind = str(note.get("kind", "unknown")).lower()
        note_kind = raw_kind if raw_kind in ALLOWED_NOTE_KINDS else "unknown"

        if byte_length > max_file_bytes:
            _, inserted = _record_artifact(
                conn,
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                artifact_path=artifact_path,
                content_hash=c_hash,
                snapshot_id=project_snapshot_id,
                kind="note",
                outcome="rejected",
                reason_code="note_exceeds_max_file_bytes",
                detail={"byte_length": byte_length, "max_file_bytes": max_file_bytes},
                now=now,
            )
            outcome = "rejected" if inserted else "duplicate"
            report.counts[outcome] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": "note", "outcome": outcome, "reason_code": "note_exceeds_max_file_bytes"}
            )
            continue

        existing_note = conn.execute(
            sa.select(ppm_notes.c.id).where(
                ppm_notes.c.tenant_id == tenant_id,
                ppm_notes.c.project_id == project_id,
                ppm_notes.c.source_id == source_id,
                ppm_notes.c.artifact_path == artifact_path,
                ppm_notes.c.content_hash == c_hash,
            )
        ).scalar_one_or_none()

        if existing_note is not None:
            report.counts["duplicate"] += 1
            report.artifacts.append(
                {"path": artifact_path, "kind": "note", "outcome": "duplicate", "reason_code": "duplicate_note"}
            )
            continue

        _, inserted_art = _record_artifact(
            conn,
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            artifact_path=artifact_path,
            content_hash=c_hash,
            snapshot_id=project_snapshot_id,
            kind="note",
            outcome="ingested",
            reason_code=None,
            now=now,
        )
        if not inserted_art:
            report.counts["duplicate"] += 1
            continue

        conn.execute(
            ppm_notes.insert().values(
                id=f"note-{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=source_id,
                metadata_root=metadata_root,
                artifact_path=artifact_path,
                content_hash=c_hash,
                kind=note_kind,
                body=body,
                byte_length=byte_length,
                first_seen_snapshot_id=project_snapshot_id,
                ingested_at=now,
            )
        )
        report.counts["ingested"] += 1
        report.artifacts.append(
            {"path": artifact_path, "kind": "note", "outcome": "ingested", "reason_code": None}
        )

    # 4. Advance checkpoint in the same transaction
    if ckpt is None:
        conn.execute(
            ppm_ingest_checkpoints.insert().values(
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                last_ingested_snapshot_seq=snapshot_seq,
                last_ingested_snapshot_id=project_snapshot_id,
                updated_at=now,
            )
        )
    else:
        conn.execute(
            ppm_ingest_checkpoints.update()
            .where(
                ppm_ingest_checkpoints.c.tenant_id == tenant_id,
                ppm_ingest_checkpoints.c.source_id == source_id,
                ppm_ingest_checkpoints.c.project_id == project_id,
            )
            .values(
                last_ingested_snapshot_seq=snapshot_seq,
                last_ingested_snapshot_id=project_snapshot_id,
                updated_at=now,
            )
        )

    # 5. Emit ppm.ingest.completed outbox event in the same transaction (ISS-025/ISS-029 pattern)
    event_id = f"evt-{uuid.uuid4().hex[:16]}"
    conn.execute(
        outbox.insert().values(
            tenant_id=tenant_id,
            stream_id=f"ingest:{project_id}",
            event_id=event_id,
            event_type="ppm.ingest.completed",
            payload={
                "project_snapshot_id": project_snapshot_id,
                "snapshot_id": snapshot_id,
                "source_id": source_id,
                "project_id": project_id,
                "snapshot_seq": snapshot_seq,
                "counts": dict(report.counts),
                "gaps": list(report.gaps),
                "completed_at": now.isoformat(),
            },
            created_at=now,
        )
    )
    report.outbox_event_id = event_id

    return report


def get_supersession_chain(
    conn: sa.Connection | Session,
    tenant_id: str,
    project_id: str,
    update_id: str,
) -> list[dict[str, Any]]:
    """Return the ordered supersession chain (oldest -> newest) containing *update_id*."""
    rows = conn.execute(
        sa.select(
            ppm_updates.c.update_id,
            ppm_updates.c.supersedes_update_id,
            ppm_updates.c.supersession_state,
            ppm_updates.c.content_hash,
            ppm_updates.c.contract_version,
            ppm_updates.c.payload,
            ppm_updates.c.ingested_at,
        ).where(
            ppm_updates.c.tenant_id == tenant_id,
            ppm_updates.c.project_id == project_id,
        )
    ).mappings().all()

    by_id: dict[str, dict[str, Any]] = {str(r["update_id"]): dict(r) for r in rows}
    if update_id not in by_id:
        return []

    # Walk backwards to oldest ancestor (guarding against cycles)
    seen: set[str] = set()
    cur_id: str = update_id
    while cur_id not in seen:
        seen.add(cur_id)
        parent_id = by_id[cur_id].get("supersedes_update_id")
        if parent_id and parent_id in by_id and parent_id not in seen:
            cur_id = str(parent_id)
        else:
            break

    # Build child map and walk forward
    children_map: dict[str, list[str]] = {}
    for uid, row in by_id.items():
        pid = row.get("supersedes_update_id")
        if pid:
            children_map.setdefault(str(pid), []).append(uid)

    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    walk_id: str | None = cur_id
    while walk_id is not None and walk_id not in visited:
        visited.add(walk_id)
        chain.append(by_id[walk_id])
        kids = sorted(children_map.get(walk_id, []))
        walk_id = kids[0] if kids else None

    return chain


__all__ = [
    "IngestReport",
    "SnapshotNotFoundError",
    "get_supersession_chain",
    "ingest_snapshot",
]
