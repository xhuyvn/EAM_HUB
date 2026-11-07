# Validation report — 01/11/2026

Executed template/schema and negative-case validation; not a live collector or SDK test.

- project templates: PASS
- registry template: PASS
- absolute artifact path: PASS
- parent traversal: PASS
- unknown contract: PASS
- repo grants cloud: PASS
- duplicate YAML keys: PASS
- invalid progress: PASS
- timestamp without timezone: PASS

Markdown fences and 8 prompt files: PASS. Registry schedules and repository scans were not activated. Dependencies used only for local document validation. No model/API calls were tested.

## Correction — 06/11/2026 (platform architecture review, finding ARCH-01)
The "timestamp without timezone: PASS" line above was not reproducible. With jsonschema 4.26.0 and no `rfc3339-validator` installed, `format: date-time` is not enforced, so `recorded_at: 2026-11-01T16:00:00` and even `not-a-date` were accepted (reproduced with `tools/run_negative_cases.py` before the fix). `schemas/contract.schema.json` now adds an RFC 3339 pattern with mandatory offset to `status.updated_at` and `update.recorded_at`. No negative-case runner existed in the package; `tools/run_negative_cases.py` now runs `evals/negative_cases.json` (11 cases, including duplicate YAML keys). Current results are recorded in the platform `VALIDATION_REPORT.md`.

## Revision — 06/11/2026 (PPM v2 architecture review)
Before fixes, a metadata root with a duplicate criterion_id, a supersession cycle, a dangling supersession and `recorded_at: 2099-01-01` returned `PASS 8 records; structural checks only` (spike). Validator now runs contract-semantic checks; runner runs directory-level cases.
- `python tools/run_negative_cases.py` → `PASS 5 templates, 20 cases`
- `python tools/validate_contract.py templates/project/.ppm --schema schemas/contract.schema.json` → see platform review §8
Not executed: prompts against a real coding agent, collector/verifier/reducer (no code), scenario evals in `evals/SCENARIO_MATRIX.md`.

## Milestone PPM-M0 Validation — 07/11/2026 (PPM-M0-WP5 packaging & runner)
Deterministic reducer execution against the full fixture corpus via `ppm-v2/tools/run_reducer_fixtures.py`:
- Fixture count: 23 fixtures (20 assessment fixtures including 3 portfolio rollups, plus 3 expected input rejection error fixtures: s29 supersession_cycle, s31 evidence_from_future, s32 missing_assessed_revision)
- All 23 fixtures: PASS (0 failures, byte-for-byte post-JCS normalization, 0 diff)
- Execution time:
  - Benchmark: median of 5 runs = 6.61 ms (0.00661 s) across all 23 fixtures (per-run: 6.61 ms, 8.18 ms, 6.76 ms, 5.78 ms, 6.36 ms)
  - Average per-fixture execution time: ~0.29 ms
- Clean-machine proof: PASS in an isolated Python 3.12 virtual environment with only `pip install -r ppm-v2/tools/requirements.txt` and `pip install -e ppm-v2/reducer`
- Zero external I/O, zero network, zero subprocesses, pure deterministic Python standard library logic.

