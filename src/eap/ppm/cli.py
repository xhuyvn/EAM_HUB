"""Manual CLI triggers for D0-thin-slice and fixture workflows (P2a-WP4).

Commands:
- ``python -m eap.ppm.cli assess --tenant <t> --project <p>``
- ``python -m eap.ppm.cli approve-baseline --tenant <t> --project <p> --fixture <path>``
- ``python -m eap.ppm.cli load-evidence --tenant <t> --project <p> --fixture <path>``

Fixture loaders are strictly guarded by ``EAP_COLLECTOR_ALLOW_REAL_ROOTS=false``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from eap.api.dependencies import Principal
from eap.config import get_settings
from eap.db import (
    collector_snapshots,
    get_session_factory,
    location_registrations,
    ppm_assessments,
    project_current_assessment,
)
from eap.ppm.assessments import (
    BaselineRevisionConflict,
    approve_baseline,
    create_assessment,
    load_fixture_evidence,
    load_fixture_waivers,
)
from eap.ppm.collector.collector import PpmCollector
from eap.ppm.service import LocationService


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping in fixture file {path}")
    return data


def _db_url(settings_obj: Any) -> str | None:
    return settings_obj.database_url.get_secret_value() if settings_obj.database_url else None


def _cmd_assess(args: argparse.Namespace) -> int:
    settings = get_settings()
    factory = get_session_factory(_db_url(settings))
    with factory() as session:
        if getattr(args, "skip_if_revision_exists", False) and args.assessed_revision:
            existing = session.execute(
                sa.select(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == args.tenant,
                    ppm_assessments.c.project_id == args.project,
                    ppm_assessments.c.assessed_revision == args.assessed_revision,
                )
                .order_by(ppm_assessments.c.basis_seq.desc())
            ).mappings().first()
            if existing is not None:
                curr_id = session.execute(
                    sa.select(project_current_assessment.c.assessment_id).where(
                        project_current_assessment.c.tenant_id == args.tenant,
                        project_current_assessment.c.project_id == args.project,
                    )
                ).scalar_one_or_none()
                out = dict(existing["output"] or {})
                print(
                    json.dumps(
                        {
                            "assessment_id": str(existing["id"]),
                            "tenant_id": str(existing["tenant_id"]),
                            "project_id": str(existing["project_id"]),
                            "basis_seq": int(existing["basis_seq"]),
                            "snapshot_seq": int(existing["snapshot_seq"]),
                            "baseline_revision": existing["baseline_revision"],
                            "assessed_revision": existing["assessed_revision"],
                            "verification": out.get("verification"),
                            "acceptance_readiness": out.get("acceptance_readiness"),
                            "is_current": str(existing["id"]) == curr_id,
                            "deduplicated": True,
                        },
                        indent=2,
                    )
                )
                return 0

        rec = create_assessment(
            session,
            args.tenant,
            args.project,
            trigger=args.trigger,
            assessed_revision=args.assessed_revision,
        )
        session.commit()
        print(
            json.dumps(
                {
                    "assessment_id": rec.id,
                    "tenant_id": rec.tenant_id,
                    "project_id": rec.project_id,
                    "basis_seq": rec.basis_seq,
                    "snapshot_seq": rec.snapshot_seq,
                    "baseline_revision": rec.baseline_revision,
                    "assessed_revision": rec.assessed_revision,
                    "verification": rec.output.get("verification"),
                    "acceptance_readiness": rec.output.get("acceptance_readiness"),
                    "is_current": rec.is_current,
                    "deduplicated": rec.deduplicated,
                },
                indent=2,
            )
        )
    return 0


def _cmd_approve_baseline(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.eap_collector_allow_real_roots:
        raise RuntimeError("approve-baseline --fixture is disabled when EAP_COLLECTOR_ALLOW_REAL_ROOTS=true")

    data = _load_json_or_yaml(Path(args.fixture))
    baseline = data.get("baseline") if "baseline" in data else data
    if not isinstance(baseline, dict) or "criteria" not in baseline:
        raise ValueError("Fixture does not contain a valid baseline object with 'criteria'")

    rev = int(baseline.get("baseline_revision", args.revision))
    criteria = list(baseline["criteria"])

    factory = get_session_factory(_db_url(settings))
    with factory() as session:
        try:
            row = approve_baseline(
                session,
                args.tenant,
                args.project,
                baseline_revision=rev,
                criteria=criteria,
                approved_by=args.approved_by,
                source=str(args.fixture),
            )
        except BaselineRevisionConflict as exc:
            session.rollback()
            print(
                json.dumps({"error": str(exc), "reason": exc.reason}),
                file=sys.stderr,
            )
            return 1
        session.commit()
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "tenant_id": row["tenant_id"],
                    "project_id": row["project_id"],
                    "baseline_revision": row["baseline_revision"],
                    "status": row["status"],
                    "approved_by": row["approved_by"],
                    "criteria_count": len(row["criteria"]),
                },
                indent=2,
            )
        )
    return 0


def _cmd_load_evidence(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.eap_collector_allow_real_roots:
        raise RuntimeError("load-evidence --fixture is disabled when EAP_COLLECTOR_ALLOW_REAL_ROOTS=true")

    data = _load_json_or_yaml(Path(args.fixture))
    evidence_items = list(data.get("evidence") or [])
    waiver_items = list(data.get("waivers") or [])

    factory = get_session_factory(_db_url(settings))
    with factory() as session:
        ev_count = load_fixture_evidence(
            session,
            args.tenant,
            args.project,
            evidence_items,
            simulated=True,
            settings=settings,
        )
        wav_count = load_fixture_waivers(
            session,
            args.tenant,
            args.project,
            waiver_items,
            settings=settings,
        )
        session.commit()
        print(json.dumps({"evidence_loaded": ev_count, "waivers_loaded": wav_count}, indent=2))
    return 0


def _cmd_register_source(args: argparse.Namespace) -> int:
    settings = get_settings()
    factory = get_session_factory(_db_url(settings))
    host_os = settings.get_server_host_os()
    host_id = settings.eap_host_id
    location_id = args.location_id or f"loc-{args.project}"

    with factory() as session:
        src_res, src_status = LocationService.register_source(
            session,
            tenant_id=args.tenant,
            source_id=args.source,
            canonical_root=args.root,
            repo_identity=args.repo_identity,
            principal_id="principal-system",
            host_id=host_id,
            host_os=host_os,
        )

        existing_loc = session.execute(
            sa.select(location_registrations).where(
                location_registrations.c.tenant_id == args.tenant,
                location_registrations.c.id == location_id,
            )
        ).mappings().first()

        if existing_loc is not None and existing_loc["state"] == "active":
            loc_state = "active"
        else:
            if existing_loc is None:
                loc_payload: dict[str, Any] = {
                    "contract_version": "1.1",
                    "location_id": location_id,
                    "project_id": args.project,
                    "host_id": host_id,
                    "connector_id": "connector-d0-cli",
                    "root_path": args.root,
                    "metadata_root": args.metadata_root,
                    "scan_scope": ["."],
                    "artifact_aliases": {
                        "architecture": ["architecture.md", "docs/ARCHITECTURE.md"],
                        "blueprint": ["MASTER_BLUEPRINT.md"],
                        "implementation_plan": ["implement_plan.md", "docs/IMPLEMENTATION_PLAN.md"],
                    },
                    "notes_globs": [".ppm/notes/*.md"],
                    "follow_symlinks": False,
                    "allow_hardlinked_files": False,
                    "max_file_bytes": 2097152,
                    "deny_globs": [".env", ".env.*", "*.pem", "*.key"],
                    "classification": "internal",
                    "egress_policy_ref": "egress:local-only",
                    "identity": {
                        "canonical_root_fingerprint": None,
                        "repo_identity": args.repo_identity,
                    },
                    "state": "proposed",
                    "revision": 1,
                    "registered_by_ref": "principal:principal-system",
                    "approved_by_ref": None,
                    "source_id": args.source,
                }
                LocationService.register_location(
                    session,
                    tenant_id=args.tenant,
                    data=loc_payload,
                    principal_id="principal-system",
                    host_os=host_os,
                )
            approved = LocationService.approve_location(
                session,
                tenant_id=args.tenant,
                location_id=location_id,
                principal=Principal(
                    tenant_id=args.tenant,
                    principal_id=args.approved_by,
                    roles=["owner"],
                ),
            )
            loc_state = str(approved["state"])

        session.commit()
        print(
            json.dumps(
                {
                    "source_id": src_res["source_id"],
                    "source_status": src_status,
                    "location_id": location_id,
                    "location_state": loc_state,
                    "project_id": args.project,
                },
                indent=2,
            )
        )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    settings = get_settings()
    factory = get_session_factory(_db_url(settings))
    with factory() as session:
        if args.skip_if_commit:
            existing = session.execute(
                sa.select(collector_snapshots.c.id).where(
                    collector_snapshots.c.tenant_id == args.tenant,
                    collector_snapshots.c.source_id == args.source,
                    collector_snapshots.c.status == "captured",
                    collector_snapshots.c.git_commit == args.skip_if_commit,
                )
            ).scalar_one_or_none()
            if existing is not None:
                print(
                    json.dumps(
                        {
                            "skipped": True,
                            "snapshot_id": str(existing),
                            "git_commit": args.skip_if_commit,
                        },
                        indent=2,
                    )
                )
                return 0

        collector = PpmCollector(fixtures_root=settings.eap_collector_fixtures_root)
        res = collector.scan_source(session, tenant_id=args.tenant, source_id=args.source)
        session.commit()
        print(
            json.dumps(
                {
                    "skipped": False,
                    "snapshot_id": res.snapshot_id,
                    "status": res.status,
                    "ingest_status": res.ingest_status,
                    "git_commit": res.git_commit,
                    "partial_reasons": res.partial_reasons,
                },
                indent=2,
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eap.ppm.cli", description="EAP PPM manual CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_assess = sub.add_parser("assess", help="Run deterministic reducer assessment for a project")
    p_assess.add_argument("--tenant", required=True)
    p_assess.add_argument("--project", required=True)
    p_assess.add_argument("--trigger", default="manual")
    p_assess.add_argument("--assessed-revision", default=None)
    p_assess.add_argument("--skip-if-revision-exists", action="store_true")
    p_assess.set_defaults(func=_cmd_assess)

    p_bl = sub.add_parser("approve-baseline", help="Approve a baseline from a fixture file (fixtures-only)")
    p_bl.add_argument("--tenant", required=True)
    p_bl.add_argument("--project", required=True)
    p_bl.add_argument("--fixture", required=True)
    p_bl.add_argument("--revision", type=int, default=1)
    p_bl.add_argument("--approved-by", default="owner")
    p_bl.set_defaults(func=_cmd_approve_baseline)

    p_ev = sub.add_parser("load-evidence", help="Load simulated fixture evidence/waivers (fixtures-only)")
    p_ev.add_argument("--tenant", required=True)
    p_ev.add_argument("--project", required=True)
    p_ev.add_argument("--fixture", required=True)
    p_ev.set_defaults(func=_cmd_load_evidence)

    p_reg = sub.add_parser("register-source", help="Idempotently register and approve source + location")
    p_reg.add_argument("--tenant", required=True)
    p_reg.add_argument("--project", required=True)
    p_reg.add_argument("--source", required=True)
    p_reg.add_argument("--root", required=True)
    p_reg.add_argument("--repo-identity", required=True)
    p_reg.add_argument("--metadata-root", default=".ppm")
    p_reg.add_argument("--location-id", default=None)
    p_reg.add_argument("--approved-by", default="principal-owner")
    p_reg.set_defaults(func=_cmd_register_source)

    p_scan = sub.add_parser("scan", help="Run PpmCollector.scan_source and ingest")
    p_scan.add_argument("--tenant", required=True)
    p_scan.add_argument("--source", required=True)
    p_scan.add_argument("--skip-if-commit", default=None)
    p_scan.set_defaults(func=_cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
