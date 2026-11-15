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

from eap.config import get_settings
from eap.db import get_session_factory
from eap.ppm.assessments import (
    approve_baseline,
    create_assessment,
    load_fixture_evidence,
    load_fixture_waivers,
)


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
        row = approve_baseline(
            session,
            args.tenant,
            args.project,
            baseline_revision=rev,
            criteria=criteria,
            approved_by=args.approved_by,
            source=str(args.fixture),
        )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eap.ppm.cli", description="EAP PPM manual CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_assess = sub.add_parser("assess", help="Run deterministic reducer assessment for a project")
    p_assess.add_argument("--tenant", required=True)
    p_assess.add_argument("--project", required=True)
    p_assess.add_argument("--trigger", default="manual")
    p_assess.add_argument("--assessed-revision", default=None)
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
