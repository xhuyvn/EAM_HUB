# Project Management Contract 2.1 (compatible with 2.0)

## 1. Nature
MUST = mandatory requirement of this contract; SHOULD = recommendation with allowed exception if rationale is recorded. This is a proposed internal standard, not a new ISO standard.
All portable documents use `contract_version: '2.0'` and `kind`. JSON Schema Draft 2020-12 verifies structure; rule engine verifies semantics, authority, and provenance.

## 2. Authority matrix
| Data | Canonical source | Who may write |
|---|---|---|
| Absolute location, scan schedule, cloud/access | Registry on PPM host | Owner/trusted admin |
| Identity/proposed objectives/mapping | `.ppm/project.yaml` | Owner or assigned coding agent |
| Work journal | `.ppm/updates/*.yaml` | Person/agent executing within granted scope |
| Reported summary | `.ppm/status.yaml` | Single designated writer; reconstructible from updates |
| Proposed criteria | `.ppm/acceptance.yaml` | Owner/coding agent; approved in domain DB |
| Proposed decisions | `.ppm/decisions/*.md` | Authoring agent/person |
| Baseline, assessment, acceptance | Domain DB + audit | Domain service according to permissions |

MUST not treat `owner_ref`, actor, or the word accepted in repo files as identity certification or approval. Do not import approved baseline from repo without reconciling with a trusted approval event.

## 3. Minimal structure
`.ppm/project.yaml`, `.ppm/status.yaml`, `.ppm/updates/` form the minimal adoption profile; the updates directory may be empty when first initialized. `.ppm/acceptance.yaml` is mandatory prior to verified-delivery assessment; if missing, a coverage gap must be reported. `.ppm/decisions/` and notes are supplementary. Sample `README.md` and `.gitignore` assist implementation.

Paths in portable mapping MUST be relative POSIX paths, no `..`, no absolute paths or URLs. Registry is permitted absolute host paths; collector still checks root and realpath. All actual read paths MUST resolve within trusted root, including symlink/reparse checks. JSON Schema only blocks certain path patterns and does not replace containment checks.

## 4. Identity and versioning
Project ID MUST remain stable across rename/path change. Initialization recommends `proj-<UUID>`; human-readable templates are permitted but inventory must verify uniqueness within namespace. Relocation does not create a new project; clone/worktree is a new location associated with the same project/repo identity. Copying to create a new product must be assigned a new project ID.
Monorepo: each project has `.ppm/` under its own subdirectory; registry specifies metadata_root and scan_scope. Multi-repo project: primary metadata root, location_ref in artifact mapping resolves secondary repos. Artifact paths must not traverse outside root via `../`.
Unrecognized contract major: quarantine unsupported_version. Migration MUST preserve original files/backups and must not change registered IDs. Moving from v1 `schema_version: 1` must go through explicit migration, never guessed.

## 5. Project/status
Project declares objectives in draft form: id, statement, metric nullable; artifact mapping mandatorily includes architecture/blueprint/implementation_plan with path or null. Missing documents must remain null and record gap, never fabricating false content.
Status records only lifecycle_reported, current_milestone, reported_progress_percent nullable, blockers, and next_actions. `reporting_revision` is the version of the summary, not the baseline revision. Past status is preserved via snapshots; update history is never deleted.
Reported progress lacking a baseline MUST remain null. 0 is used only when the reporter genuinely intends to report 0, not to represent unknown. Project delivery and operational status remain separated.

## 6. Session update
Each update MUST have a unique ID and corresponding filename, project_id, recorded_at with timezone, self-reported actor string, session_id, summary, base_revision/result_revision nullable, working_tree_state, completed/in_progress/checks/blockers/next_actions.
Work item consists of item_ref nullable and description. Check consists of name, result (`passed`, `failed`, `not_run`, `inconclusive`), evidence_path nullable, tested_revision nullable, environment nullable. This data remains a claim of the producer; collector independently collects hash/report metadata and verifier confirms.
A check with result `passed` SHOULD have an artifact report; if unavailable, set evidence_path=null and mark unsupported claim during assessment. Do not record passed when not run. Evidence mismatched with code/criteria is insufficient for verified.

Per-session update is append-only in semantics. Correcting errors requires a new file with `supersedes_update_id` and rationale in summary; retain old file. Same ID/same bytes => duplicate no-op; same ID/different bytes => conflict, not last-write-wins. Late-arriving updates are still processed; timestamp is not an ingestion cursor.

Producer SHOULD generate a UUID ID to prevent collisions, write to a temporary file, and atomically rename within the same filesystem. Temp suffix `.tmp` is not ingested; unparseable files are retried with limits. Do not commit/push solely to satisfy contract if the task does not require it.

## 7. Notes and decisions
Freeform notes in `.ppm/notes/` or mapping `notes` may be Markdown. They help preserve context but do not unilaterally alter status, scope, or acceptance. Distinguish note kinds: idea, blocker, question, observation. To report progress, create a structured update.
Decision note must contain scope, alternatives, rationale, impact, evidence_refs, and state proposed. Only decisions reconciled against an authority/approval event take effect in DB.

## 8. Source revision and self-reference
Updates are not required to contain a commit hash that includes the update itself: creating a self-referential commit hash is impossible. `result_revision` may point to code commit prior to documentation commit; or null if uncommitted. Collector independently collects HEAD, tree/diff manifest, and update hash. Do not modify code merely to match revision in the journal.

## 9. Scan contract
MUST support initial/manual/interval/full_reconciliation; watcher is an optimization. Checkpoint advances only after successful transaction ingest. Identify ingestion by project+location+artifact path+content hash; domain update dedup adds project+update_id.
Scan status: queued/running/succeeded/partial/failed/cancelled. Assessment validity: current/stale/unknown/conflicted. File parse failure may produce partial scan but must not mark entire project fresh.
Missing path, revoked permissions, system sleep, and cloud errors must not produce progress 0 or conclude no_change. `last_successful_scan_at`, `last_scan_attempt_at`, `last_assessment_at`, and source_as_of MUST be distinguished.
Full reconciliation detects delete/rename and lost watcher events. Journal does not delete historical records when files are removed; creates source tombstone and evaluates impact.

## 10. Multiple writers
Each agent writes its own update. Status is updated only by designated writer using expected content hash or CAS. Without CAS, let PPM reconstruct and report status stale rather than blindly overwriting. When coding agents have isolated worktrees, registry must select an integration branch/snapshot to aggregate; do not sum all worktrees into accepted progress.

## 11. Git and sensitive data
Metadata/updates/criteria SHOULD be committed for traceability, after checking for sensitive data. Local cache, host paths, tokens, and raw output containing secrets MUST not be committed. `.gitignore` is not a security control; check tracked files as well. Project repo does not autonomously enable cloud, cron, MCP, or arbitrary shell.

## 12. Definition of session done
- Record explicit done/in-progress, actual checks, and blockers.
- Create one new update; update summary if designated writer.
- Do not claim accepted/deployed/benefit realized from code/test pass.
- Do not conceal failure or fabricate history.
- Hand over next actions and unresolved decisions.

## 13. Interpretation examples
Update reports M2 done, test pass but evidence_path null: reported done, verification gap.
Test report fail at current implementation: contradicted, requires resolution.
Owner says "used and works fine": record observation/acceptance proposal, define scope prior to formal acceptance.
Project closed, product running: lifecycle closed and operation running coexist.

## 14. Contract 2.1 (06/11/2026) — backward compatible additions
Added following review `ppm-v2/docs/ARCHITECTURE_REVIEW.md`. 2.0 documents remain valid, no migration required.

### 14.1 Version
`contract_version` accepts `'2.0'` or `'2.1'`. 2.1 fields may only be used when file declares `'2.1'`; files declaring 2.0 that use 2.1 fields are rejected (validator). Reader MUST be able to read both 2.0 and 2.1. Higher minor (2.2+) or different major → quarantine `unsupported_version` and read-only gap report.

### 14.2 New fields (all optional)
- `acceptance.yaml` → `criteria[].verification_refs[]`: `{kind: test_id | probe_id | artifact_path | manual_review, ref}`. This is a proposed mapping; takes effect only when owner approves into baseline in PPM DB. Reducer uses baseline mapping to determine which test covers criterion; missing mapping → `coverage_not_mapped`, do not guess.
- `updates/*.yaml` → `completed[]/in_progress[]/checks[].criterion_ids[]`, `checks[].test_ids[]`: links claim with criterion avoiding LLM guessing. IDs must exist in `acceptance.yaml` under the same metadata root.
- `project.yaml` → relation `uses_component`: this project uses deliverable of another project (shared component). Do not import component criteria into consumer's `acceptance.yaml`.

### 14.3 Evidence trust classes (MUST)
| Trust class | Source | Can verify criterion? |
|---|---|---|
| `verifier_observed` | Verifier sandbox of PPM/platform reruns mapped test/probe at exact revision | Yes |
| `ci_attested` | CI result with verifiable provenance (pipeline identity, revision, signature/attestation) configured as trusted by owner | Yes |
| `reported_artifact` | Report file residing in repository (JUnit/JSON/log) created by coding agent or person, even when update points `evidence_path` to it | **No**; only claim with file |
| `none` | None | No |
Rationale: producer authors code, update, and report file in the same repo, so in-repo report files are not independent from claims. FAIL in `reported_artifact` is still used as refutation signal (conservative), PASS is not.

### 14.4 Project identity on copy/clone
Same `project_id` appearing at two sources with different `repo_identity` (copying repo to create new product while forgetting to change ID) → `identity_conflict`, quarantine new location, do not merge updates; owner chooses: issue new ID or confirm as worktree/clone of same project. Confirmed clone/worktree still does not sum individual progress (§10).

### 14.5 Baseline from draft
`acceptance.yaml` is always `draft`. Owner creates baseline via PPM API from snapshot hash of `acceptance.yaml` (+ edits if needed); baseline stores revision, approver, timestamp, and verification mapping. Prior to baseline: assessment displays each criterion status as *provisional*, `progress.basis=none`, no acceptance readiness. Afterward, `acceptance.yaml` differing from baseline → `baseline_drift` signal and change proposal suggestion, never unilaterally altering baseline.

### 14.6 Waiver
Waivers exist only in PPM DB (approver, rationale, scope, expiry), never in repo. Waived criterion displays separately, excluded from progress denominator and counted as satisfied for acceptance readiness until waiver expires.

### 14.7 Registry and platform mode
`templates/registry.example.yaml` is configuration for standalone mode and is a **one-time import format** when migrating to platform. In platform mode, single source of truth is `location-registration` 1.1 (`contracts/location-registration.schema.json`): import creates `proposed` record, owner supplements `source_id`, `host_id`, `artifact_aliases`, `classification`, `egress_policy_ref` and approves. `access_policy.cloud_processing: approved_only` does not automatically convert into egress permission; owner must select `egress_policy_ref`.

### 14.8 Additional validator checks
Beyond schema: duplicate criterion IDs; supersession pointing to nonexistent update or forming a loop; update referencing nonexistent criterion; multiple `project_id` in single metadata root; `recorded_at/updated_at` skewed into future > 24 hours; 2.1 fields in 2.0 file. Run `python tools/run_negative_cases.py` to check cases in `evals/negative_cases.json`.
