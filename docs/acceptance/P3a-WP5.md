# Acceptance Packet — P3a-WP5: Agent inventory (read-only)

- **Work Package**: `P3a-WP5` — Agent inventory (read-only) (`ISS-037`, `ISS-038`)
- **Branch**: `wp/P3a-WP5`
- **Base commit (`main`)**: `6c8a24b`
- **Implementation commit**: `2f7dfd9`
- **Scope basis**: `docs/ISSUES.md` (`ISS-037`, owner approved 2026-11-15, `MVP1 = local`), `docs/03_HUD_SPEC.md`, `docs/06_AGENT_INTEGRATION_STANDARD.md`, `contracts/agent.schema.json`, `contracts/binding.schema.json`, `src/eap/contracts/models/binding.py`, `templates/`

---

## 1. Summary of Changes

1. **Migration & Schema (`migrations/versions/0008_agents.py`, `src/eap/db/agent_tables.py`, `src/eap/db/__init__.py`)**
   - Reversible Alembic migration `0008_agents` (`down_revision = "0006_assessments"`) adding:
     - `agents`: composite unique constraint `(tenant_id, agent_id)`, canonical `manifest` JSONB, `manifest_sha256`, `name`, `version`, `domain`, `owner_refs` JSONB (`verification_status="unverified"`, `verification_label="unverified owner"` until OIDC/IdP), `runtime_kind` check constraint (`native|http|cli|vendor_sdk`), `task_types` JSONB, `registered_at`, `status` check constraint (`proposed|active|retired`).
     - `agent_bindings`: composite unique constraint `(tenant_id, binding_id)`, composite tenant-scoped FK `(tenant_id, agent_id) -> agents(tenant_id, agent_id)` (per DG-1 A1), `payload` JSONB, `release_version`, `release_sha256`, `mode` (`observe|managed|governed`), `execution_boundary` (`platform_gateway|sandboxed|external_trusted`), `certified_capabilities` JSONB, `capability_evidence_ref`, `egress_policy_ref`, `secret_ref` (reference-only), `endpoint_ref` (reference-only), `created_at`, `revoked_at`.
   - Core 16 tables in `src/eap/db/tables.py` were **not** altered (preserving exact parity with `migrations/draft/0001_core.sql`); `runs.binding_id` already exists on `runs` and is joined safely against `agent_bindings(tenant_id, binding_id)`.
   - `tools/run_sc06.py` updated to seed `agents` and `agent_bindings` in Cycle 2; `tests/db/test_tenant_isolation_fks.py` updated (`fk_count == 19` + cross-tenant FK rejection test for `agent_bindings`).

2. **Dispatch Binding Attribution & Unattributed Runs (`src/eap/task/service.py`)**
   - `TaskService.create` resolves an active (`revoked_at IS NULL`), most-recent `agent_bindings.binding_id` for `(tenant_id, request.agent_id)` when `binding_id` is omitted, falling back to `binding-<task_type>` when no active binding exists.
   - Runs whose `binding_id` has no matching row in `agent_bindings` for that tenant (e.g., pre-existing rows or unregistered fallback bindings) are classified cleanly as `"unattributed"` (`unattributed_runs_count` at inventory root, `unattributed_run_count` per agent) and never fabricated into healthy metrics.

3. **Service, Secret Safety & Fixture Mock Agent (`src/eap/agents/service.py`, `src/eap/agents/cli.py`, `src/eap/agents/fixtures/`)**
   - Strict schema + model + semantic rule validation (`contracts/agent.schema.json`, `contracts/binding.schema.json`, `Agent`, `Binding`, `check_binding_rules`, plus capability subset check against `desired_capabilities`) returning structured `field_path` on rejection with zero DB writes.
   - Reference fields (`secret_ref`, `endpoint_ref`, `capability_evidence_ref`, `egress_policy_ref`, `model_profile_ref`, `embedding_profile_ref`, list refs) reject inline secrets (`sk-...`, `password=`, URL userinfo credentials, PEM, bearer/basic tokens, etc.) BEFORE persistence/logging, registering detected substrings with `register_secret(...)` so logs and stderr never contain secret values.
   - Version immutability enforced: identical manifest digest is idempotent (no duplicate row); modified manifest with the same `version` raises `AgentVersionImmutableError` (`AGENT_VERSION_IMMUTABLE`).
   - Local MVP CLI loader `python -m eap.agents.cli register --manifest <agent.json> --binding <binding.json>` restricted to loopback `eap_bind`, fixtures/admin mode (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`), and owner principal.
   - Committed mock provider fixture manifest & binding (`src/eap/agents/fixtures/mock_agent.json`, `mock_binding.json`, aligned with P1a-WP4 `MockProvider`).

4. **Derived Health & Read-Only HTTP API (`src/eap/api/agent_routes.py`, `src/eap/api/app.py`, `src/eap/hud/snapshot.py`)**
   - Health strictly derived from attributed runs in `runs`, `usage_ledger`, and `dispatch_intents`:
     - `0` runs -> `health.status = "no_data"` (never `"healthy"`), `last_run_at = null`, `last_observed_at = null`, `cost.status = "unknown"`.
     - Lost ACK / ambiguous dispatch / `remote_outcome = "unknown"` -> `unknown_outcome_count >= 1`, `health.status = "unknown"` (never `"healthy"`).
     - Missing usage rows -> `cost.status = "unknown"`, `cost.amount_usd = null`.
     - Revoked bindings shown with `status = "revoked"`, non-null `revoked_at`, and excluded from `active_binding_count`.
   - Routes `GET /v1/agents` and `GET /v1/agents/{agent_id}` derive tenant strictly from server-side `get_principal` (`401` on missing/invalid Bearer, `422` on forbidden tenant/principal query/headers, `404` on unknown or cross-tenant `agent_id`, `405` on write verbs).
   - Added `AgentInventoryRowBuilder` (`kind="agent"`) implementing `RowKindBuilder` in `src/eap/hud/snapshot.py`. Default `GET /v1/hud/snapshot` (`_DEFAULT_BUILDER`) stays strictly conforming to `hud-projection` v1.1 (`ISS-038` extended in `docs/ISSUES.md`; `contracts/` untouched).

5. **Plan Synchronization (`docs/IMPLEMENTATION_PLAN.md`, `docs/vi/docs/IMPLEMENTATION_PLAN.md`)**
   - Added owner-approved `P3a-WP5` and `MVP1-WP1` (local loopback MVP) to both EN and VI plans with matched identifiers and bumped VI translation header `@ 2f7dfd9` for `SC-08`.

---

## 2. Rule / Requirement -> Test -> Observed Table

| Rule / Requirement | Test(s) | Observed Result |
|---|---|---|
| Schema-invalid manifest/binding rejected with `field_path`; nothing stored | `tests/agents/test_agent_inventory.py::test_invalid_manifest_and_binding_rejected_with_field_path_nothing_stored` | PASSED (`/runtime_kind`, `/task_types`, `/execution_boundary`, `/certified_capabilities` reported; DB row count `(0, 0)`) |
| Inline secrets in `secret_ref` / `endpoint_ref` rejected; nothing stored; no secret in `caplog` or CLI stderr | `tests/agents/test_agent_inventory.py::test_inline_secret_in_ref_fields_rejected_nothing_stored_and_not_logged` (`sk-live-...`, `password=...`, `https://user:pass@...`) | PASSED (3 parametrized cases; `INLINE_SECRET_REJECTED`, row count `(0, 0)`, canary absent from `caplog`, `stdout`, `stderr`) |
| Re-register same manifest idempotent; changed manifest with same version rejected | `tests/agents/test_agent_inventory.py::test_reregister_idempotent_and_changed_manifest_same_version_rejected` | PASSED (`agent_created=False`/`binding_created=False`; mutated manifest raises `AGENT_VERSION_IMMUTABLE`, digest preserved) |
| Zero runs -> `health.status = "no_data"` (never `"healthy"`); pre-existing unlinked run -> `"unattributed"` | `tests/agents/test_agent_inventory.py::test_health_no_data_lost_ack_missing_usage_and_unattributed_runs` | PASSED (`status == "no_data"`, `total_runs == 0`, `unattributed_runs_count == 1`, `unattributed_run_count == 1`) |
| Lost ACK run -> `unknown_outcome_count = 1`, health not `"healthy"`, missing usage -> `cost.status = "unknown"` | `tests/agents/test_agent_inventory.py::test_health_no_data_lost_ack_missing_usage_and_unattributed_runs` | PASSED (`Dispatcher` + `MockProvider(drop_ack=True)` -> `unknown_outcome_count == 1`, `status == "unknown"`, `cost.status == "unknown"`, `amount_usd is None`; recorded usage yields `status == "healthy"`, `cost.amount_usd == 0.0425`) |
| `TaskService.create` attributes active registered binding & skips revoked binding | `tests/agents/test_agent_inventory.py::test_health_...` & `test_api_cross_tenant_isolation_revoked_binding_and_hud_builder` | PASSED (`runs.binding_id == "binding-mock-provider-local"` when active; falls back after `revoke_binding`) |
| Cross-tenant isolation (`GET /v1/agents`, `GET /v1/agents/{id}` -> `404`, `RowKindBuilder`, `/v1/hud/snapshot`) & read-only enforcement | `tests/agents/test_agent_inventory.py::test_api_cross_tenant_isolation_revoked_binding_and_hud_builder` | PASSED (`401` unauthenticated, `422` header/query tenant, `404` cross-tenant detail, `405` POST/DELETE, HUD snapshot & builder isolated) |
| Revoked binding shown as `revoked`, excluded from active count | `tests/agents/test_agent_inventory.py::test_api_cross_tenant_isolation_revoked_binding_and_hud_builder` | PASSED (`active_binding_count == 0`, `revoked_binding_count == 1`, `status == "revoked"`) |
| Local CLI loader registers mock provider idempotently; enforces loopback, fixtures-only, owner principal | `tests/agents/test_agent_inventory.py::test_cli_register_mock_fixture_and_security_guardrails` | PASSED (subprocess `python -m eap.agents.cli register` + idempotent re-run exit `0`; refused non-loopback/real-roots/non-owner exit `1`) |
| Tenant-scoped composite FKs in migration `0008_agents` & autogenerate/DDL parity | `tests/db/test_tenant_isolation_fks.py::test_cross_tenant_fk_agent_bindings_rejected`, `tests/db/test_autogenerate_diff.py`, `tests/db/test_draft_ddl_parity.py` | PASSED (`39 passed` in `tests/db`; SC-06 `0008_agents` up/down/up + seeded cycle PASS) |

---

## 3. Example Observed Response (`GET /v1/agents` with Fixture Mock Agent)

Captured directly against PostgreSQL 18.6 (`0008_agents`) after registering `src/eap/agents/fixtures/mock_agent.json` and `src/eap/agents/fixtures/mock_binding.json`:

```json
{
  "schema_version": "1.0",
  "tenant_id": "tenant-owner",
  "generated_at": "2026-11-15T15:03:12.622705Z",
  "window": {
    "mode": "all_recorded",
    "hours": null
  },
  "unattributed_runs_count": 0,
  "agents": [
    {
      "agent_id": "agent-mock-provider",
      "name": "Deterministic Mock Provider Agent",
      "version": "1.0.0",
      "domain": "platform",
      "status": "active",
      "runtime_kind": "native",
      "task_types": [
        "ppm.project.assess",
        "code.patch"
      ],
      "manifest_sha256": "65c8ed5b644cc304b158c7c29da6bebf229d3ae8ce60196a0b09805c56849e3b",
      "owner": {
        "business_owner_ref": "owner-platform-example",
        "verification_label": "unverified owner",
        "technical_owner_ref": "owner-platform-tech",
        "verification_status": "unverified"
      },
      "registered_at": "2026-11-15T15:03:12.576804Z",
      "active_binding_count": 1,
      "revoked_binding_count": 0,
      "bindings": [
        {
          "binding_id": "binding-mock-provider-local",
          "release_version": "1.0.0",
          "release_sha256": "65c8ed5b644cc304b158c7c29da6bebf229d3ae8ce60196a0b09805c56849e3b",
          "mode": "managed",
          "execution_boundary": "platform_gateway",
          "endpoint_ref": "inproc:eap.adapters.mock_provider.MockProvider",
          "secret_ref": null,
          "egress_policy_ref": "egress:local-only",
          "telemetry_export": "local_only",
          "policy_version": "policy-1.0",
          "model_profile_ref": "local-scoped-default",
          "certified_capabilities": {
            "cancel": true,
            "events": true,
            "resume": false,
            "status": true,
            "submit": true,
            "approve_tool": false,
            "collect_result": true
          },
          "capability_evidence_ref": "artifact:mock-provider-p1a-wp4-capabilities",
          "capability_evidence_level": "self_declared",
          "capability_evidence_status": "self_declared_unverified",
          "status": "active",
          "created_at": "2026-11-15T15:03:12.576804Z",
          "revoked_at": null
        }
      ],
      "health": {
        "status": "no_data",
        "total_runs": 0,
        "last_run_at": null,
        "last_observed_at": null,
        "counts_by_state": {},
        "unknown_outcome_count": 0,
        "unattributed_run_count": 0,
        "cost": {
          "status": "unknown",
          "amount_usd": null,
          "currency": "USD",
          "known_runs": 0,
          "missing_usage_runs": 0
        }
      }
    }
  ]
}
```

---

## 4. Verification Runs & Observed Output

| Check | Exact Command | Observed Output |
|---|---|---|
| SC-01 contracts | `uv run python tools/validate_package.py` | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` |
| PPM negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | `PASS 5 templates, 20 cases` |
| PPM fixtures & reducer | `uv run python ppm-v2/tools/check_fixtures.py` & `uv run python ppm-v2/tools/run_reducer_fixtures.py` | `PASS 23 fixtures, 3 rollup`; `PASS 23 fixtures` |
| SC-06 migration cycle | `$env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run python tools/run_sc06.py` | `SC-06 Verification: ALL CYCLES PASSED` (`0008_agents (head)`) |
| Full pytest (PG 18.6, CI=true) | `$env:CI="true"; $env:TEST_DATABASE_URL=...; uv run pytest -q` | `642 passed, 5 warnings in 186.74s` |
| Ruff lint | `uv run ruff check src tests ppm-v2/reducer migrations` | `All checks passed!` |
| Mypy typecheck | `uv run mypy src tests ppm-v2/reducer` | `Success: no issues found in 163 source files` |
| HUD build & tests | `npm --prefix web/hud run build && npm --prefix web/hud test` | `tsc -b && vite build` built 33 modules; `vitest`: `1 passed (2 tests)` |
| SC-08 translation & clone | `uv run python tools/check_translation.py --compare` & `--strict` (verified in worktree and fresh `git clone`) | `SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.`; `PASS: 0 identifier differences across 38 file pairs.` |
| Checksums | `sha256sum -c SHA256SUMS.txt` | `0 mismatches` (539 entries, LF line endings verified) |
| GitHub Actions CI | Push `wp/P3a-WP5` (`6db509b`) | Run [`43727878833`](https://github.com/xhuyvn/EAM_HUB/actions/runs/43727878833) (`validate-and-test`) **PASS** (`3m1s`) |

---

## 5. Residual Risks & Explicit Boundaries

1. **Unverified owner identity**: `business_owner_ref` and `technical_owner_ref` are stored as proposed manifest metadata and surfaced with `verification_status: "unverified"` / `verification_label: "unverified owner"` until OIDC/IdP integration (`P1b-WP1`).
2. **Self-declared capability evidence**: `capability_evidence_ref` values are surfaced with `capability_evidence_level: "self_declared"` and `capability_evidence_status: "self_declared_unverified"` until independent verification/certification is implemented in `P4`.
3. **HUD snapshot v1.1 contract gap (`ISS-038`)**: `contracts/hud-projection.schema.json` (v1.1) rejects additional row shapes via `additionalProperties: false`. `AgentInventoryRowBuilder` (`kind="agent"`) implements the `RowKindBuilder` extension point cleanly and is tested for tenant isolation, while live read traffic uses `GET /v1/agents` and `GET /v1/agents/{agent_id}` without altering `contracts/`.
4. **Read-only boundary**: No HTTP routes create, modify, suspend, or approve agents/bindings (`405 Method Not Allowed`). Mutation remains restricted to the local MVP CLI (`python -m eap.agents.cli register`) gated behind loopback bind, `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`, and owner principal until RBAC + Action Service approvals land in `P1b-WP3` / `P3b` / `P4`.
5. **Unattributed historical runs**: Existing `runs` rows without a matching `agent_bindings(tenant_id, binding_id)` row remain attributed to `"unattributed"` so missing history never masquerades as `"healthy"`.
