> Implementation workflow (06/11/2026): coding agents start with `AGENTS.md` and `STATUS.md`; reviews use `prompts/REVIEW_SESSION.md` and `docs/REVIEW_LOG.md`. Dev environment: `docs/DEV_ENVIRONMENT.md`.

# Enterprise Agent Platform — Integrated Blueprint 1.1
Released 05/11/2026; revision 1.1 on 06/11/2026 following architecture review (`docs/ARCHITECTURE_REVIEW.md`). Vietnamese design documentation, contracts and coder prompts in English. Package includes platform design, HUD, PPM v2, phase 2 harness optimization and agent onboarding kit.

## Getting started
1. Read `MASTER_BLUEPRINT.md` to understand architecture and phase 1/2 scope.
2. Read `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` to understand consolidated design decisions.
3. Read `docs/02_RUNTIME_AND_SECURITY.md`, `docs/03_HUD_SPEC.md`, `docs/04_PPM_INTEGRATION.md`.
4. Use `guides/NEW_AGENT.md` or `guides/EXISTING_AGENT.md` for agent onboarding.
5. Read `docs/ARCHITECTURE_REVIEW.md` (findings, readiness) and `docs/IMPLEMENTATION_PLAN.md` (work packages). Assign coders to start with `prompts/START_NEXT_MILESTONE.md` (P0); `prompts/01_START_PLATFORM_P0.md` is the original summary prompt for the same scope.
6. Phase 2 has its own specification at `docs/05_PHASE2_HARNESS_OPTIMIZATION.md`.

## Delivery status
This is a specification and integration kit. The validation report records checks executed on the package. The execution runtime, live adapters, realtime HUD, security/load tests and model benchmarks will be implemented across subsequent milestones.
`visuals/HUD_MOCKUP.svg` and PNG are mockups containing illustrative data, used for layout review.

## Authority rules
The authoritative documents of this package are MASTER_BLUEPRINT, docs, contracts and guides. `ppm-v2/` contains contracts, templates and guides for the PPM domain. Platform governs auth, run lifecycle, scheduling and integration. PPM governs project assessment, criteria and acceptance. Provenance of consolidated source archives is recorded in SOURCE_MANIFEST.json.

## Using templates
JSON files in templates use example values. Generate real IDs, real owners and deployment binding credentials outside the repository before onboarding. The agent manifest is a proposal; the deployment binding must be confirmed by a trusted administrator. Tenant and principal fields in internal events are established by the gateway.

## Structural validation
Python 3.10+ with jsonschema and PyYAML (`pip install -r tools/requirements-validated.txt`). Run from the package root:
```bash
python tools/validate_package.py
sha256sum -c SHA256SUMS.txt
```
`validate_package.py` validates 16 schemas, fixtures, the transition table, approval binding, negative cases and calls `ppm-v2/tools/run_negative_cases.py`; it records `VALIDATION_REPORT.md` with timestamp and git revision.
Validated dependency versions are recorded in `tools/requirements-validated.txt`. The script checks schemas, examples and selected semantic invariants. Security and recovery test plans are documented separately; runtime execution occurs in subsequent milestones.
