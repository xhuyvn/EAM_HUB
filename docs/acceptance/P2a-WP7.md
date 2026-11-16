# P2a-WP7 Acceptance Packet — Analysis Step (Fake Model) & Model Gateway

## 1. Milestone & Scope
- **Work Package**: `P2a-WP7` — Analysis step (fake model) & Model Gateway
- **Branch**: `wp/P2a-WP7` (based on `main` `f980b94`)
- **Migration**: None required (`0010_hud_sse_and_policy` remains head; `ppm_assessments.analysis_status` already supports `complete`, `model_failed`, `skipped`).
- **Scope delivered**:
  1. `src/eap/model_gateway/`: `ModelGateway`, `generate_structured(binding, profile_ref, prompt, schema)`, `generate_embeddings(binding, profile_ref, texts)`, `ProfileRegistry`, `enforce_egress`, `FakeModelProvider` (deterministic & scriptable: success, invalid JSON, timeout, refusal), and `OllamaProvider` stub (`NotImplementedError` until DG-3 / P0-WP6).
  2. `src/eap/ppm/analysis.py`: `LockedAnalysisInputs`, `load_locked_analysis_inputs`, `build_analysis_prompt`, `run_analysis`, `to_reducer_claims`, `ANALYSIS_OUTPUT_SCHEMA`. Extracts proposed claims (`origin=model_analysis`, `trust_class=none`) from locked notes & update summaries; repairs invalid JSON up to 2 times (`attempt_count <= 3`) before returning `analysis_status=model_failed`.
  3. Prompt-injection hardening (C22): delimited untrusted data (`UNTRUSTED_INPUT_DATA_BEGIN`/`END`), no tools, strict output schema without `verified`/`status`/`tool_calls`/`url`, unknown fields and unknown `source_artifact_path`s dropped and counted.
  4. Feature flag `PPM_ANALYSIS_ENABLED` (default `false`; rollback = `false` -> `analysis_status=skipped`, 0 model calls), `extra_claims` hook on `build_assessment_inputs` / `create_assessment` (not wired into orchestrator — owned by P2a-WP6), and `python -m eap.ppm.cli analyze` fixture CLI.
  5. Conformance artifacts: `artifacts/conformance/P2a/C12.json`, `artifacts/conformance/P2a/C22.json`.

## 2. Rule -> Test -> Observed Table

| Rule / Requirement | Test(s) | Observed Result |
|---|---|---|
| C22: Untrusted note injection cannot verify criterion, create evidence, or trigger network | `test_c22_prompt_injection_cannot_verify_or_trigger_network`, `test_db_load_locked_inputs_and_create_assessment_with_timeout_and_c22` | `PASS`: 6 disallowed fields/paths dropped & counted; `origin=model_analysis`, `trust_class=none`; `AC-001` remains `unverified` with `evidence_refs=[]`; 0 network calls |
| C12: Local profile fails, cloud fallback under `egress:local-only` | `test_c12_local_fails_cloud_fallback_egress_denied_zero_cloud_calls` | `PASS`: refused with `egress_denied`, `analysis_status=model_failed`, 0 calls to cloud provider |
| C12: Cloud embedding profile under `egress:local-only` | `test_c12_embedding_profile_cloud_egress_denied_zero_calls` | `PASS`: `EgressDeniedError` (`code=egress_denied`), 0 provider calls |
| Binding profile authorization & fail-closed registry | `test_c12_unauthorized_and_unknown_profiles_refused` | `PASS`: `ProfileNotAuthorizedError` / `ProfileNotFoundError` raised before provider call |
| Invalid JSON twice then valid -> repair success | `test_invalid_json_twice_then_valid_succeeds_after_repair` | `PASS`: `analysis_status=complete`, `attempt_count=3` |
| Invalid JSON three times -> `model_failed` | `test_invalid_json_three_times_fails_with_attempt_count_3` | `PASS`: `analysis_status=model_failed`, `attempt_count=3`, `reason=invalid_json` |
| Timeout -> `model_failed`, reducer still produces assessment | `test_timeout_yields_model_failed_and_reducer_still_produces_assessment`, `test_db_load_locked_inputs_and_create_assessment_with_timeout_and_c22` | `PASS`: `analysis_status=model_failed` persisted in `ppm_assessments`, `AC-001` `unverified` |
| Logs & telemetry never leak clear text; telemetry off unless allowed | `test_logs_and_telemetry_never_leak_clear_text` | `PASS`: `caplog` has `prompt_sha256`/`prompt_length`/`output_sha256`/`output_length` only; 0 export calls when `local_only` |
| Determinism with fake model | `test_determinism_same_inputs_produce_identical_claims_and_hash` | `PASS`: identical `inputs_hash`, `analysis_claims`, and `reducer_claims` |
| Feature flag `PPM_ANALYSIS_ENABLED=false` rollback | `test_feature_flag_disabled_skips_analysis_without_model_call` | `PASS`: `analysis_status=skipped`, `attempt_count=0`, 0 provider calls |
| `OllamaProvider` stub | `test_ollama_provider_stub_not_callable` | `PASS`: raises `NotImplementedError` |
| Fixture CLI `analyze` | `test_cli_analyze_fixture` | `PASS`: exit 0, valid JSON output |

## 3. Egress Decision Table (`endpoint_class` x `egress_policy_ref`)

Verified by `test_egress_decision_matrix` (`12/12` parametrized cases):

| `egress_policy_ref` | `local` | `approved_vpc` | `cloud` |
|---|---|---|---|
| `egress:local-only` | **allowed** | denied (`egress_denied`) | denied (`egress_denied`) |
| `egress:vpc-only` | **allowed** | **allowed** | denied (`egress_denied`) |
| `egress:cloud-allowed` | **allowed** | **allowed** | **allowed** |
| `egress:deny-all` | denied (`egress_denied`) | denied (`egress_denied`) | denied (`egress_denied`) |
| `<unknown policy>` (fail-closed) | denied (`egress_denied`) | denied (`egress_denied`) | denied (`egress_denied`) |

## 4. Example Analysis Output from a Fixture

Command:
```powershell
$env:PPM_ANALYSIS_ENABLED="true"; $env:EAP_COLLECTOR_ALLOW_REAL_ROOTS="false"
uv run python -m eap.ppm.cli analyze --fixture wp7_fixture.json --fake-response wp7_fake_response.json
```

Observed output:
```json
{
  "analysis_claims": [
    {
      "claim_kind": "claimed_done",
      "confidence": 0.85,
      "excerpt": "Export endpoint implemented for AC-001",
      "origin": "model_analysis",
      "proposed_criterion_id": "AC-001",
      "quote_span": {
        "end": 39,
        "start": 0
      },
      "source_artifact_path": ".ppm/notes/status.md",
      "trust_class": "none"
    },
    {
      "claim_kind": "claimed_in_progress",
      "confidence": 0.9,
      "excerpt": "AC-002 still in progress.",
      "origin": "model_analysis",
      "proposed_criterion_id": "AC-002",
      "quote_span": {
        "end": 65,
        "start": 41
      },
      "source_artifact_path": ".ppm/notes/status.md",
      "trust_class": "none"
    }
  ],
  "analysis_status": "complete",
  "attempt_count": 1,
  "dropped_fields_count": 0,
  "error_code": null,
  "inputs_hash": "1a68e32f94ffc7f3ef841764680afd55cdb762d32903c72e5f02f9217067eaa3",
  "profile_ref": "local-scoped-default",
  "reason": null,
  "reducer_claims": [
    {
      "claim_id": "analysis:a02008fabc57:0",
      "criterion_ids": ["AC-001"],
      "reported_status": "claimed_done",
      "source_file": ".ppm/notes/status.md",
      "summary": "Export endpoint implemented for AC-001"
    },
    {
      "claim_id": "analysis:069d87730f16:1",
      "criterion_ids": ["AC-002"],
      "reported_status": "claimed_in_progress",
      "source_file": ".ppm/notes/status.md",
      "summary": "AC-002 still in progress."
    }
  ]
}
```

## 5. Checks Executed & Real Output

| Check | Command | Observed Result |
|---|---|---|
| WP7 unit & PG 18.6 integration tests | `$env:CI="true"; $env:TEST_DATABASE_URL="postgresql+psycopg://eap:***@127.0.0.1:15432/eap_test"; uv run pytest tests/ppm/test_model_gateway_and_analysis.py -q` | `26 passed, 1 warning in 3.02s` |
| Full pytest suite (`CI=true`, PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="postgresql+psycopg://eap:***@127.0.0.1:15432/eap_test"; uv run pytest -q` | `698 passed, 7 warnings in 240.13s (0:04:00)` |
| Ruff lint | `uv run ruff check src tests ppm-v2/reducer tools` | `All checks passed!` |
| Mypy typecheck | `uv run mypy src tests ppm-v2/reducer tools` | `Success: no issues found in 186 source files` |
| SC-01 package contracts | `uv run python tools/validate_package.py` | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` |
| PPM v2 negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | `PASS 5 templates, 20 cases` |
| PPM v2 fixtures | `uv run python ppm-v2/tools/check_fixtures.py` | `PASS 23 fixtures, 3 rollup` |
| PPM v2 reducer fixtures | `uv run python ppm-v2/tools/run_reducer_fixtures.py` | `PASS 23 fixtures` |
| SC-08 translation check (git clone) | `uv run python tools/check_translation.py` | `SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.` |
| GitHub Actions CI | — | `not_run` (pending push of `wp/P2a-WP7`) |

## 6. Residual Risks
- **Real Ollama untested until DG-3 / P0-WP6**: `OllamaProvider` is a stub raising `NotImplementedError`; live local LLM latency, JSON adherence, and throughput remain unverified until pilot GPU + DG-3.
- **Orchestrator wiring deferred to P2a-WP6**: `run_analysis` and `create_assessment(..., extra_claims=...)` are exposed and tested, but automatic invocation on snapshot/recovery events belongs to `P2a-WP6`.

## 7. Rollback Procedure
- Set `PPM_ANALYSIS_ENABLED=false` (the default). `run_analysis` immediately returns `analysis_status="skipped"` with zero Model Gateway or provider calls, and `ppm_reducer` evaluates structured claims and evidence normally.
