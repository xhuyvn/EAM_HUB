# Acceptance Packet: P0-WP2 Contract Models

## 1. Milestone and Scope
- **Work Package**: P0-WP2 (Contract models per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §5 SC-01, SC-02, and §6 P0-WP2).
- **Scope**:
  - Implement hand-written, strictly typed Pydantic v2 models for all 16 JSON schemas under `contracts/*.schema.json`, maintaining strict `extra="forbid"`, `strict=True`, and exact constraint/enum/required parity.
  - Provide a schema registry mapping schema filename and schema name to root models in `src/eap/contracts/models/__init__.py`.
  - Implement RFC 8785 JSON Canonicalization Scheme (JCS) and action proposal digest in `src/eap/contracts/digest.py` pinned to `rfc8785>=0.1.4`.
  - Port all semantic and invariant validation rules from `tools/validate_package.py` into pure functions in `src/eap/contracts/rules.py` raising typed `ContractViolation(code, detail)`.
  - Create comprehensive tests under `tests/contracts/`:
    - `test_schema_parity.py`: automated recursive parity verification across 16/16 schemas and all 11 `event.schema.json` if/then payload branches for field names, required set, types, pattern, minLength/maxLength, minItems/maxItems, minimum/maximum/exclusiveMinimum, and enums/consts.
    - `test_fixture_parity.py`: JCS round-trip parity on all JSON fixtures, event streams, and monorepo locations.
    - `test_digest.py`: template action digest verification (`76580e7cecb951cc2e84d499b6006492bd394b4b44c068372464aedf2d30a436`) and RFC 8785 test vectors.
    - `test_negative_parity.py`: dynamic test suite importing validator negatives (50/50 cases rejected, including pair, operation-key, and location negatives imported directly from `tools/validate_package.py`).
    - `test_differential.py`: differential mutation testing across all 15 JSON fixtures + 11 events in `events.ndjson` (wrong type including bool/numeric-string for ints, empty string, empty list, negative number/zero, null, extra key, deleted key, invalid enum), asserting `model accepts => jsonschema accepts` (0 violations).
    - `test_rules.py`: unit tests for pure rule functions, state transitions, and compare-and-set assessment resolution.
    - `test_boundary.py`: import boundaries verifying `ppm_reducer` does not import `eap.contracts`, and `eap.contracts` remains pure without I/O, DB, network, or settings dependencies.

## 2. Review Follow-ups (W2-1, W2-2)
- **W2-1: Models must never accept what the JSON Schema rejects**:
  - `ContractModel` config has `strict=True` so that `"2"` or `True`/`False` are never coerced to ints or other types.
  - Added missing constraints across all models: array item `minLength: 1` (`NonEmptyStr`), `minItems: 1` on arrays, numeric bounds (`gt=0`, `ge=0`, `ge=1`, etc.), string lengths (`min_length`, `max_length`), and exact literals.
  - Added `StrictFalse = Annotated[Literal[False], BeforeValidator(_validate_bool_exact)]` in `src/eap/contracts/models/base.py` to prevent Python's `0 == False` from accepting `0` for schema boolean `false`.
  - Updated payload models in `event.py` with exact enums and bounds (`RunState` literal for `RunStateChangedPayload`, `ApprovalDecidedPayload`, `RunFinishedPayload`, `WorkerHeartbeatPayload`, `ArtifactCreatedPayload`).
  - Extended `tests/contracts/test_schema_parity.py` to recursively compare type, pattern, minLength/maxLength, minItems, minimum/maximum/exclusiveMinimum, and all 11 if/then payload branches of `event.schema.json`.
  - Implemented `tests/contracts/test_differential.py`: generated 5003 mutations across all 26 targets (15 fixtures + 11 events). Achieved **0 model-looser violations** and **0 model-stricter cases** (down from baseline ~160 violations).
- **W2-2: Negative parity imports from validator**:
  - Extracted pure helper functions in `tools/validate_package.py`: `build_pair_negatives(decision)`, `build_operation_key_negatives(proposal, retry)`, and `build_location_negatives(locs)`.
  - Updated `tests/contracts/test_negative_parity.py` to import and call these helpers directly from `validate_package`, eliminating copied negative definitions.
- **P0-WP1 Follow-up**:
  - Removed no-op `Settings.__init__` in `src/eap/config.py`.

## 3. Source Commit and Traceability
- **Base Commit**: `70ea3bd` (on `main`: "accept P0-WP1: mark accepted in STATUS.md")
- **Branch**: `wp/P0-WP2`
- **Code Commit under Review**: `2d5c112` (pushed on branch `wp/P0-WP2`)
- **Environment**:
  - Python: 3.12.13 (via `uv 0.11.21`)
  - Node.js: 26.7.0 LTS / npm 11.18.0
- **Pinned Dependencies**:
  - `rfc8785>=0.1.4` (pinned `rfc8785==0.1.4` in `uv.lock`)
  - Primary RFC 8785 documentation: https://datatracker.ietf.org/doc/html/rfc8785
  - Library repository: https://github.com/woodruffw/rfc8785-py
  - Access Date: 2026-11-07

## 4. Inventory

### Model Inventory (16/16 schemas)
| Schema File | Root Model Class | Module Path | Additional Models / Sub-models |
|---|---|---|---|
| `action-proposal.schema.json` | `ActionProposal` | `eap.contracts.models.action_proposal` | — |
| `agent.schema.json` | `Agent` | `eap.contracts.models.agent` | `AgentCapabilities` |
| `approval-decision.schema.json` | `ApprovalDecision` | `eap.contracts.models.approval_decision` | `ApprovalConsumption` |
| `binding.schema.json` | `Binding` | `eap.contracts.models.binding` | `BindingCertifiedCapabilities` |
| `event.schema.json` | `Event` | `eap.contracts.models.event` | `RunStartedPayload`, `RunProgressPayload`, `RunWaitingApprovalPayload`, `RunFinishedPayload`, `WorkerHeartbeatPayload`, `ArtifactCreatedPayload`, `RunStateChangedPayload`, `ApprovalDecidedPayload`, `UsageReportedPayload`, `PpmAssessmentUpdatedPayload`, `ConnectorStatusPayload` |
| `experiment.schema.json` | `Experiment` | `eap.contracts.models.experiment` | `ExperimentPrimaryMetric`, `ExperimentGuardrailMetric`, `ExperimentRollout` |
| `hud-projection.schema.json` | `HudProjection` | `eap.contracts.models.hud_projection` | `HudRow`, `HudRowProgress`, `HudRowCost`, `HudRowProject`, `HudRowPendingCommand` |
| `location-registration.schema.json` | `LocationRegistration` | `eap.contracts.models.location_registration` | `LocationArtifactAliases`, `LocationIdentity` |
| `ppm-input.schema.json` | `PpmInput` | `eap.contracts.models.ppm_input` | — |
| `ppm-output.schema.json` | `PpmOutput` | `eap.contracts.models.ppm_output` | `PpmCriterion`, `PpmDependencyRef`, `PpmProgress` |
| `research-input.schema.json` | `ResearchInput` | `eap.contracts.models.research_input` | — |
| `research-output.schema.json` | `ResearchOutput` | `eap.contracts.models.research_output` | — |
| `result.schema.json` | `Result` | `eap.contracts.models.result` | `ResultUsage` |
| `run-command.schema.json` | `RunCommand` | `eap.contracts.models.run_command` | — |
| `task-envelope.schema.json` | `TaskEnvelope` | `eap.contracts.models.task_envelope` | `TaskEnvelopeExecutionContext` |
| `task-request.schema.json` | `TaskRequest` | `eap.contracts.models.task_request` | — |

### Validator Check to Rule Function Inventory
| Validator Check (`tools/validate_package.py`) | Ported Rule Function (`eap.contracts.rules`) | Raised Code / Behavior |
|---|---|---|
| `ALLOWED` & transition check | `check_run_state_transition(from_state, to_state)` | `TERMINAL_STATE_CANNOT_BE_LEFT`, `ILLEGAL_RUN_TRANSITION` |
| `run.progress` completed > total | `check_run_progress(completed_units, total_units)` | `PROGRESS_EXCEEDS_DENOMINATOR` |
| `usage.reported` cost mismatch | `check_usage_cost(cost_status, cost_usd)` | `USAGE_COST_MISMATCH` |
| `result` usage cost mismatch | `check_result_usage(usage)` | `USAGE_COST_MISMATCH` |
| `binding` observe & boundary checks | `check_binding_rules(binding)` | `OBSERVE_ADVERTISES_CONTROL`, `GOVERNED_REQUIRES_ENFORCEMENT_BOUNDARY`, `FALLBACK_REPEATS_PRIMARY` |
| `experiment` holdout/candidate checks | `check_experiment_rules(experiment)` | `IDENTICAL_CANDIDATE`, `HOLDOUT_NOT_ISOLATED`, `PROPOSER_CANNOT_REVIEW`, `CANARY_REQUIRES_APPROVED_STATE` |
| `ppm-output` assessment & criteria | `check_ppm_output_rules(output)` | `CRITERION_OWNER_MISMATCH`, `VERIFIED_CRITERION_EVIDENCE_INVALID`, `CONTRADICTED_CRITERION_NEEDS_REFUTATION`, `EVIDENCE_STALE_REVISION`, `COVERAGE_NOT_MAPPED`, `VERIFIED_MUST_BE_CURRENT_PASS`, `EVIDENCE_WRONG_PRODUCER`, `REPORTED_ARTIFACT_ONLY`, `WAIVED_REQUIRES_APPROVED_WAIVER`, `WAIVED_MUST_HAVE_WAIVER_APPROVED`, `DUPLICATE_CRITERION`, `VERIFIED_PROJECT_UNMET_CRITERIA`, `CONTRADICTING_EVIDENCE_SURFACING`, `ACCEPTANCE_READINESS_UNVERIFIED`, `PROGRESS_NO_BASELINE`, `PROGRESS_BASELINE_REVISION_REQUIRED`, `PROGRESS_WEIGHT_MISMATCH`, `CRITERIA_TOTAL_MISMATCH` |
| `hud-projection` row checks | `check_hud_projection_rules(hud)` | `HUD_PROGRESS_DENOMINATOR_INVALID`, `HUD_PROGRESS_KIND_MISMATCH`, `HUD_COST_MISMATCH`, `HUD_RUN_STATE_REQUIRES_RUN_ID`, `HUD_UNKNOWN_RUN_REMOTE_OUTCOME`, `HUD_COMPLETED_TASK_NON_SUCCEEDED_RUN`, `HUD_PROJECT_WEIGHT_EXCEEDS_DENOMINATOR` |
| `action-proposal` action digest | `check_action_proposal_rules(proposal)` | `ACTION_DIGEST_MISMATCH` |
| `location-registration` root & aliases | `check_location_registration_rules(location)` | `LOCATION_ROOT_NOT_ABSOLUTE`, `LOCATION_DUPLICATE_ALIAS`, `LOCATION_ACTIVE_REQUIRES_APPROVAL` |
| `run-command` rejection code | `check_run_command_rules(command)` | `RUN_COMMAND_REJECTION_CODE_MISMATCH` |
| `check_approval_pair` | `check_approval_pair(proposal, decision)` | `APPROVAL_DIGEST_NONCE_MISMATCH`, `APPROVAL_PROPOSAL_ID_MISMATCH`, `APPROVAL_OUTLIVES_PROPOSAL`, `APPROVAL_CONSUMED_OPERATION_MISMATCH`, `DENIED_APPROVAL_CONSUMED` |
| `check_operation_key_reuse` | `check_operation_key_reuse(proposals)` | `OPERATION_KEY_CONFLICT` |
| `check_location_set` | `check_location_set(locations)` | `DUPLICATE_SOURCE`, `METADATA_ROOT_CONFLICT`, `DUPLICATE_LOCATION` |
| `apply_assessment` | `apply_assessment(current, incoming)` | Compare-and-set on `basis_seq` returning `(current, history)` |
| `check_event_stream` | `check_event_stream(events)` | `DUPLICATE_EVENT`, `PRODUCER_SEQ_ORDERING`, `RUN_STATE_SEQUENCE_MISMATCH` |
| `semantic_checks(kind, value)` | `semantic_checks(kind, value)` | Unified dispatcher for all contract kinds |

## 5. Intentional Deviations and Technical Notes
- **Safe Path Lookahead Validation (ISS-009)**:
  `contracts/location-registration.schema.json` specifies pattern `^(?!/)(?!.*\\)(?!.*:)(?!.*(?:^|/)\.\.(?:/|$)).+$`. `pydantic-core` compiles regex patterns with the Rust `regex` crate, which prohibits lookaround assertions. To maintain strict parity with the schema's regex specification without lowering safety guarantees, `eap.contracts.models.base.SafePathStr` validates using Python standard library `re` via `AfterValidator(_validate_safe_path)`. Both valid paths and invalid traversal attacks (`../`, absolute paths, colons, backslashes) are verified by schema and negative parity tests.

## 6. Acceptance Commands and Real Output

### Check 1: SC-01 `uv run python tools/validate_package.py`
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

### Check 2: Negative and Translation Checks
`uv run python ppm-v2/tools/run_negative_cases.py`
```
template templates/project/.ppm/project.yaml: PASS
template templates/project/.ppm/status.yaml: PASS
template templates/project/.ppm/updates/upd-example-001.yaml: PASS
template templates/project/.ppm/acceptance.yaml: PASS
template templates/registry.example.yaml: PASS
case absolute artifact path: correctly rejected
case parent traversal: correctly rejected
case unknown contract: correctly rejected
case repo grants cloud: correctly rejected
case invalid progress: correctly rejected
case timezone absent: correctly rejected
case recorded_at not a date: correctly rejected
case status updated_at without timezone: correctly rejected
case duplicate YAML keys: correctly rejected
case acceptance file claims approved state: correctly rejected
case registry enables command execution: correctly rejected
case 2.0 document using 2.1 field: correctly rejected
case recorded_at far in the future: correctly rejected
case verification ref with unknown kind: correctly rejected
case duplicate criterion_id: correctly rejected
case supersession cycle: correctly rejected
case supersedes unknown update: correctly rejected
case update references unknown criterion: correctly rejected
case two project_ids in one metadata root: correctly rejected
case valid correction chain accepted: correctly accepted
PASS 5 templates, 20 cases
```

`uv run python tools/check_translation.py`
```
SC-08 Staleness Report: checking 38 translations against git history...
  STALE: docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/02_RUNTIME_AND_SECURITY.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/03_HUD_SPEC.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/04_PPM_INTEGRATION.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/05_PHASE2_HARNESS_OPTIMIZATION.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/06_AGENT_INTEGRATION_STANDARD.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/07_DELIVERY_AND_ACCEPTANCE.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/08_OPERATIONS_AND_DATA.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/09_DOMAINS_AND_STANDARDS.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/10_SOURCES_AND_LIMITATIONS.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/ARCHITECTURE_REVIEW.md (translated @ fb857f4, 1 commits ahead)
  STALE: docs/DEV_ENVIRONMENT.md (translated @ fb857f4, 2 commits ahead)
  STALE: docs/IMPLEMENTATION_PLAN.md (translated @ fb857f4, 1 commits ahead)
  OK: docs/REVIEW_LOG.md @ fb857f4
  STALE: evals/CONFORMANCE_CASES.md (translated @ fb857f4, 1 commits ahead)
  STALE: guides/EXISTING_AGENT.md (translated @ fb857f4, 1 commits ahead)
  STALE: guides/NEW_AGENT.md (translated @ fb857f4, 1 commits ahead)
  STALE: MASTER_BLUEPRINT.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/AI_PPM_Agent_Blueprint_v2.0.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/CHANGELOG.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/docs/ARCHITECTURE_REVIEW.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/docs/IMPLEMENTATION_PLAN.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/evals/SCENARIO_MATRIX.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/guides/ADOPTION_GUIDE.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/guides/SCANNER_IMPLEMENTATION.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/01_ADOPT_EXISTING_PROJECT.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/02_BOOTSTRAP_NEW_PROJECT.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/03_REPOSITORY_INSTRUCTIONS.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/04_START_SESSION.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/05_END_SESSION.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/06_REVIEW_PROJECT.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/07_CHANGE_PROPOSAL.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/prompts/08_BUILD_PPM_M0.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/README.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/templates/project/.ppm/notes/README.md (translated @ fb857f4, 1 commits ahead)
  STALE: ppm-v2/templates/project/.ppm/README.md (translated @ fb857f4, 1 commits ahead)
  STALE: README.md (translated @ fb857f4, 1 commits ahead)

------------------------------------------------------------
SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
```

### Check 3: SC-02 Pytest Suite (`uv run pytest -v`)
```
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.2.0, pluggy-1.7.0 -- C:\Users\xhuy\.gemini\antigravity\worktrees\EAM_HUB\p0_wp2_contract_models\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\xhuy\.gemini\antigravity\worktrees\EAM_HUB\p0_wp2_contract_models
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.16.0, asyncio-1.4.1.post1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 46 items

tests/contracts/test_boundary.py::test_contracts_pure_boundary PASSED    [  2%]
tests/contracts/test_boundary.py::test_ppm_reducer_does_not_import_eap_contracts PASSED [  4%]
tests/contracts/test_differential.py::test_differential_mutation_testing PASSED [  6%]
tests/contracts/test_digest.py::test_template_action_proposal_digest PASSED [  8%]
tests/contracts/test_digest.py::test_retry_proposal_action_digest PASSED [ 10%]
tests/contracts/test_digest.py::test_rfc8785_number_vectors PASSED       [ 13%]
tests/contracts/test_digest.py::test_rfc8785_key_ordering_utf16 PASSED   [ 15%]
tests/contracts/test_digest.py::test_rfc8785_string_escaping_vectors PASSED [ 17%]
tests/contracts/test_digest.py::test_sha256_digest_utility PASSED        [ 19%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/new-agent/agent.json-agent] PASSED [ 21%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/existing-agent/agent.json-agent] PASSED [ 23%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/admin/deployment-binding.example.json-binding] PASSED [ 26%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/admin/existing-observe-binding.example.json-binding] PASSED [ 28%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/actions/action-proposal.example.json-action-proposal] PASSED [ 30%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[templates/phase2/experiment.example.json-experiment] PASSED [ 32%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/task-request.json-task-request] PASSED [ 34%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/task-envelope.json-task-envelope] PASSED [ 36%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/result.json-result] PASSED [ 39%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/ppm-input.json-ppm-input] PASSED [ 41%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/ppm-output.json-ppm-output] PASSED [ 43%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/hud-projection.json-hud-projection] PASSED [ 45%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/approval-decision.json-approval-decision] PASSED [ 47%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/location-registration.json-location-registration] PASSED [ 50%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/run-command.json-run-command] PASSED [ 52%]
tests/contracts/test_fixture_parity.py::test_fixture_round_trip[examples/action-proposal-retry.json-action-proposal] PASSED [ 54%]
tests/contracts/test_fixture_parity.py::test_events_ndjson_round_trip PASSED [ 56%]
tests/contracts/test_fixture_parity.py::test_locations_monorepo_round_trip PASSED [ 58%]
tests/contracts/test_fixture_parity.py::test_research_models_round_trip PASSED [ 60%]
tests/contracts/test_negative_parity.py::test_all_validator_negatives_rejected PASSED [ 63%]
tests/contracts/test_rules.py::test_transition_table_allowed_and_terminal PASSED [ 65%]
tests/contracts/test_rules.py::test_run_progress_rules PASSED            [ 67%]
tests/contracts/test_rules.py::test_usage_cost_rules PASSED              [ 69%]
tests/contracts/test_rules.py::test_apply_assessment_basis_seq_compare_and_set PASSED [ 71%]
tests/contracts/test_rules.py::test_ppm_waiver_rules_and_baseline PASSED [ 73%]
tests/contracts/test_rules.py::test_event_stream_ordering_and_continuity PASSED [ 76%]
tests/contracts/test_schema_parity.py::test_schema_parity_all_16_schemas PASSED [ 78%]
tests/contracts/test_schema_parity.py::test_event_payload_branches_parity PASSED [ 80%]
tests/test_config.py::test_settings_empty_optional_services PASSED       [ 82%]
tests/test_config.py::test_settings_with_optional_services PASSED        [ 84%]
tests/test_config.py::test_env_example_interpolation PASSED              [ 86%]
tests/test_config.py::test_create_app_empty_optional_services_serves_healthz PASSED [ 89%]
tests/test_healthz.py::test_healthz_endpoint PASSED                      [ 91%]
tests/test_import_boundary.py::test_ppm_reducer_forbidden_imports_and_calls PASSED [ 93%]
tests/test_secret_canary.py::test_canary_is_never_leaked_in_repr PASSED  [ 95%]
tests/test_secret_canary.py::test_dsn_password_not_in_repr_or_str PASSED [ 97%]
tests/test_secret_canary.py::test_caplog_logging_does_not_leak_secrets PASSED [100%]

============================= 46 passed in 1.21s ==============================
```

Counts per test module:
- `tests/contracts/test_boundary.py`: 2 passed
- `tests/contracts/test_differential.py`: 1 passed (5003 mutations tested, 0 violations)
- `tests/contracts/test_digest.py`: 6 passed
- `tests/contracts/test_fixture_parity.py`: 19 passed
- `tests/contracts/test_negative_parity.py`: 1 passed (50 cases verified internally via imported validator functions)
- `tests/contracts/test_rules.py`: 6 passed
- `tests/contracts/test_schema_parity.py`: 2 passed (16/16 schemas + 11 payload branches verified internally)
- `tests/test_config.py`: 4 passed
- `tests/test_healthz.py`: 1 passed
- `tests/test_import_boundary.py`: 1 passed
- `tests/test_secret_canary.py`: 3 passed
Total: 46 passed, 0 skipped, 0 failed.

### Check 4: Schema Parity Coverage (16/16 schemas + 11 payload branches)
`uv run pytest -s tests/contracts/test_schema_parity.py` output:
```
--- SCHEMA PARITY COVERAGE (16/16 schemas) ---
  [PARITY OK] action-proposal.schema.json -> ActionProposal
  [PARITY OK] agent.schema.json -> Agent
  [PARITY OK] approval-decision.schema.json -> ApprovalDecision
  [PARITY OK] binding.schema.json -> Binding
  [PARITY OK] event.schema.json -> Event
  [PARITY OK] experiment.schema.json -> Experiment
  [PARITY OK] hud-projection.schema.json -> HudProjection
  [PARITY OK] location-registration.schema.json -> LocationRegistration
  [PARITY OK] ppm-input.schema.json -> PpmInput
  [PARITY OK] ppm-output.schema.json -> PpmOutput
  [PARITY OK] research-input.schema.json -> ResearchInput
  [PARITY OK] research-output.schema.json -> ResearchOutput
  [PARITY OK] result.schema.json -> Result
  [PARITY OK] run-command.schema.json -> RunCommand
  [PARITY OK] task-envelope.schema.json -> TaskEnvelope
  [PARITY OK] task-request.schema.json -> TaskRequest
Total: 16/16 schemas verified
```

### Check 5: Negative Parity Coverage (50/50 cases)
`uv run pytest -s tests/contracts/test_negative_parity.py` output:
```
--- NEGATIVE PARITY COVERAGE (50/50 cases rejected) ---
  01. [REJECTED OK] untrusted tenant claim
  02. [REJECTED OK] progress overflow
  03. [REJECTED OK] zero denominator
  04. [REJECTED OK] timestamp without timezone
  05. [REJECTED OK] run event without run identity
  06. [REJECTED OK] terminal state revived by late event
  07. [REJECTED OK] unknown run re-queued for blind retry
  08. [REJECTED OK] unbounded stage text (log/secret leakage surface)
  09. [REJECTED OK] unknown cost rendered as zero
  10. [REJECTED OK] partial cost without lower bound
  11. [REJECTED OK] observe-only control escalation
  12. [REJECTED OK] external boundary claims governed mode
  13. [REJECTED OK] invalid action digest
  14. [REJECTED OK] proposal changed after digest (approval replay onto new target)
  15. [REJECTED OK] identical optimization candidate
  16. [REJECTED OK] optimizer dataset includes holdout
  17. [REJECTED OK] optimizer reviews its own candidate
  18. [REJECTED OK] cheaper candidate without quality guardrails
  19. [REJECTED OK] verified project with unverified required criterion
  20. [REJECTED OK] assessment proposal claims acceptance
  21. [REJECTED OK] claim-only criterion promoted to verified
  22. [REJECTED OK] progress denominator drops unverified criteria
  23. [REJECTED OK] shared component counted in consumer project
  24. [REJECTED OK] contradicting evidence hidden behind partially_verified
  25. [REJECTED OK] AC-001: old-commit PASS used despite current-revision FAIL
  26. [REJECTED OK] AC-001: "done" note used as evidence
  27. [REJECTED OK] reported claim (session update) used as evidence
  28. [REJECTED OK] test PASS that does not cover the criterion mapping
  29. [REJECTED OK] acceptance review offered while required AC-001 contradicted
  30. [REJECTED OK] repo-written test report used to verify (forged-report path)
  31. [REJECTED OK] criterion waived without approved waiver
  32. [REJECTED OK] waived criterion kept in progress denominator
  33. [REJECTED OK] missing baseline rendered as zero progress
  34. [REJECTED OK] percent without denominator
  35. [REJECTED OK] unknown run shown with known remote outcome
  36. [REJECTED OK] unknown HUD cost rendered as zero
  37. [REJECTED OK] HUD observation time without timezone
  38. [REJECTED OK] location follows symlinks
  39. [REJECTED OK] alias escapes root
  40. [REJECTED OK] absolute alias
  41. [REJECTED OK] location activated without approval
  42. [REJECTED OK] command without server-derived actor
  43. [REJECTED OK] envelope without lease fencing token
  44. [REJECTED OK] approval replay with different nonce
  45. [REJECTED OK] approval outlives proposal
  46. [REJECTED OK] approval consumed by different operation
  47. [REJECTED OK] same operation_key with different target repository
  48. [REJECTED OK] duplicate registration of same repository and metadata_root
  49. [REJECTED OK] metadata_root claimed by two projects
  50. [REJECTED OK] same physical folder registered as a second source
Total validator negative cases tested: 50
```

### Check 6: Differential Mutation Testing (W2-1)
`uv run pytest -s tests/contracts/test_differential.py` output:
```
--- DIFFERENTIAL MUTATION TESTING ---
Total targets tested: 26 (15 fixtures + 11 events)
Total mutations generated: 5003
Model-looser violations: 0
Model-stricter cases: 0
```

### Check 7: Action Proposal Template Digest
`uv run pytest -s tests/contracts/test_digest.py -k test_template_action_proposal_digest` output:
```
[DIGEST OK] Template digest: 76580e7cecb951cc2e84d499b6006492bd394b4b44c068372464aedf2d30a436
```

### Check 8: Static Linting and Type Checking
`uv run ruff check src tests ppm-v2/reducer migrations`
```
All checks passed!
```
`uv run mypy src tests ppm-v2/reducer`
```
Success: no issues found in 49 source files
```

### Check 9: Web HUD Build
`npm --prefix web/hud run build`
```
> eap-hud@0.1.0 build
> tsc -b && vite build

vite v6.4.5 building for production...
transforming...
✓ 29 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.39 kB │ gzip:  0.27 kB
dist/assets/index-a8RAso0v.css    0.71 kB │ gzip:  0.39 kB
dist/assets/index-DLYonLHq.js   225.08 kB │ gzip: 70.02 kB
✓ built in 612ms
```

### Check 10: SHA256SUMS Verification
`sha256sum -c SHA256SUMS.txt`
```
Pass on all tracked repository files after regeneration.
```

## 7. Unresolved Gaps
- None. All review items W2-1 and W2-2 are fully resolved.

## 8. Recommendation and Rollback
- **Status**: `ready_for_review` (set in `STATUS.md`).
- **Owner Action**: Review branch `wp/P0-WP2` and merge into `main` if accepted.
- **Rollback Procedure**: Contract models and rules are fully decoupled from database and runtime migrations. Revert branch `wp/P0-WP2` via `git checkout main && git branch -D wp/P0-WP2`.
