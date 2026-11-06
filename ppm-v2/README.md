> Platform integration notice — 05/11/2026. Host permissions, task/run lifecycle, scheduling and executor dispatch follow the platform MASTER_BLUEPRINT and docs/04_PPM_INTEGRATION. PPM contract 2.1 (reads 2.0) retains domain reporting semantics. Use platform P0–P5 for delivery and map legacy PPM M0–M6 as described in docs/04.

# AI PPM Blueprint v2.0
Date: 01/11/2026. Design suite + standard + prompts; not yet a complete running application.

## Read and apply
1. Read `AI_PPM_Agent_Blueprint_v2.0.md` and `standards/PROJECT_MANAGEMENT_CONTRACT.md`.
2. Active projects: use `prompts/01_ADOPT_EXISTING_PROJECT.md`.
3. New projects: use `prompts/02_BOOTSTRAP_NEW_PROJECT.md`.
4. Bring contents of `prompts/03_REPOSITORY_INSTRUCTIONS.md` into existing agent guidance additively, preserving project rules.
5. Each session uses start/end session prompts; periodically run review prompt.
6. Register location in the registry of the PPM application. If application does not yet exist, store registry as a draft; if automated scanning does not yet exist, rely only on copying `.ppm/`.

Prompts are for coding agents with owner-granted permissions. PPM collector treats the repository as data only. Prompts do not grant new permissions, do not self-install hooks, OS schedules, or send data to the cloud.

## Contents
- Complete blueprint inheriting from v1, updated with v2 contract.
- Data standard, adoption guide, scanner design.
- 8 copy/paste prompts, portable templates and separate registry.
- JSON Schema and reference validator, test fixtures, and packaged verification results.

## Sample validation
Requires Python 3 and `jsonschema`, `PyYAML` (declared in tools/requirements.txt).
Run from the document suite directory:

```bash
python tools/validate_contract.py templates/project/.ppm --schema schemas/contract.schema.json
python tools/validate_contract.py templates/registry.example.yaml --schema schemas/contract.schema.json
python tools/run_negative_cases.py   # expected: PASS 5 templates, 20 cases
```

The validator does not run project tests, does not send network requests, and does not certify progress. Templates contain illustrative project IDs; replace consistently before use. Research source snapshot is 31/10/2026; v2 focuses on contract specification and adoption.

## Review 06/11/2026
- Contract 2.1 (compatible with 2.0): `verification_refs`, `criterion_ids`, `test_ids`, `uses_component` — see `standards/PROJECT_MANAGEMENT_CONTRACT.md` §14.
- Test reports self-recorded by repo are claims only; only verifier/CI-attested reports verify (§14.3).
- Review: `docs/ARCHITECTURE_REVIEW.md`. Plan: `docs/IMPLEMENTATION_PLAN.md`. Scenario matrix: `evals/SCENARIO_MATRIX.md`. Next milestone: `prompts/START_NEXT_MILESTONE.md` (PPM-M0).
