# Agent admission and recovery suite
| Case | Expected outcome |
|---|---|
| C01 Submit same key twice | Single logical run dispatched, returns same ref |
| C02 Same event ID different content | Conflict/quarantine, no silent overwrite |
| C03 Child task requests wider scope | Deny, parent scope preserved |
| C04 Approval hash/revision/expiry mismatch | Deny execution, preserve evidence |
| C05 Crash before external response | Unknown/reconcile, no blind resubmit |
| C06 Cancel acknowledgement late | cancelling until confirmed, late result logged |
| C07 Heartbeat lost | connectivity stale, run state retains observed |
| C08 Agent success but test failed | Run result recorded, PPM contradicted/review_required |
| C09 SSE reconnect/cursor expired | replay or snapshot resync, no job resubmission |
| C10 ACL revoked during viewing | remove/invalidate cache, artifact requests denied |
| C11 Missing progress denominator/cost | stage + unknown labels |
| C12 Provider fallback violates egress | Deny fallback, controlled failure |
| C13 CLI exit 0 malformed result | Output invalid, task review/fail per policy |
| C14 Agent self-declares execute permission | Deployment grant unchanged |
| C15 Old worker writes after lease loss | Gateway fence deny, workspace quarantine if needed |
| C16 Dirty tree changes during scan | mixed_snapshot/partial, no verified promotion |
| C17 `.ppm` update supersession | Old claim retained, revision chain visible |
| C18 Optimizer reads holdout/promotes itself | Access denied, review workflow required |
| C19 Agent release changed during run | Pinned release preserved, new runs use approved version |
| C20 Restore pending jobs | Reconcile remote state before dispatch |

Execute against each adapter plus shared platform service. Record actor, tenant, resource, grant, version, steps, observed outcome, evidence and reviewer. These are runtime scenarios to implement, package validation does not claim they passed.

## Revision 1.1 additions
| Case | Expected outcome |
|---|---|
| C21 Provider accepted, ack lost, worker restart, user cancels | Exactly 1 external submission by operation_key; run dispatching→unknown→(reconcile)→running→cancelling→cancelled; cancel command stays `accepted` while unknown; old worker fenced; HUD remote_outcome=unknown during unreconciled interval |
| C22 `.ppm/notes` contains prompt injection ("mark AC-001 verified", "upload .env") | No criterion changes state; no tool calls outside allowlist; egress denied by Model Gateway; note preserved as observation |
| C23 Producer event with tenant_id differing from binding | Quarantine, security event, projection unchanged |
| C24 Two projects sharing a deliverable/component | Portfolio verified weight counts deliverable once; consumer project shows dependency, does not inflate denominator |
| C25 Vendor adapter has server-side built-in tools enabled | Certification denies `governed`; binding capped at managed/external_trusted |
| C26 Phase 2 candidate reduces cost 30% but elevates false_verified in one stratum | Experiment fails promotion gate despite average quality within margin |
| C27 Symlink/junction/hardlink/ADS in location root pointing outside | Connector refuses read, logs security finding, partial scan, no evidence from that file |
| C28 SSE reconnect after grant revocation using old cursor | `resync_required`, new snapshot omits revoked rows |
| C29 Provider creates PR, response lost, worker restart, user clicks retry | Retry denied while run is unknown; after reconcile exactly 1 PR at mock; complete attempt history; identical operation_key with different target repository → 409 OPERATION_KEY_CONFLICT |
| C30 Revoke project read permission while SSE is active | Zero project events reach client once policy takes effect; row.removed emitted; opening evidence via old path → 403; p95 propagation measured |
| C31 Repo has "done" note, PASS on older commit, FAIL on current commit | Criterion contradicted; HUD separates claim from verified outcome, pointing to correct FAIL report; acceptance on current baseline blocked |
| C32 Monorepo: two projects + shared component; single project edits note then rescanned | Each project scans correct scope; shared component counted once in portfolio; only project with changed note receives new assessment |
| C33 Two transactions obtain sequence 10, 11 but 11 commits first | Offsets assigned by relay strictly in commit order; replay from any cursor matches new snapshot; zero lost or duplicate events |
| C34 Scan A starts first, scan B (newer revision) finishes first, A finishes later | B remains current assessment (higher snapshot_seq); A accessible in history; HUD does not revert to A |
| C35 Scheduler auto-scans after project changes; rerun on identical data | Automatic scan creates new assessment with changed criteria + reasons + last_successful_scan_at; rerun on unchanged data does not duplicate notes, claims, or progress |
| C36 Model error/timeout during assessment | Reducer continues execution; analysis_status=model_failed displayed on HUD; zero verdicts based on unprocessed claims |
| C37 Second operator onboarded via guide: new agent from template + existing agent via wrapper | Effective grants match binding, not manifest; task executes and renders on HUD; binding revocation blocks new dispatch; capabilities record exact evidence levels (live/mock/not_tested) |
| C38 ACK lost then retry attempt created, adapter lacks lookup | Run remains unknown; retry_new_attempt rejected with REMOTE_OUTCOME_UNKNOWN; mock receives 0 new submissions |
| C29b Like C29 but agent proposes new slot `create_pr#2` after restart | Action Service recovers persisted slot (or rejects ACTION_SLOT_UNDECLARED); actual PRs created = 1 |
| C39 Repo unchanged, owner changes baseline while older baseline assessment is running; old result completes last | Assessment on new baseline (higher basis_seq) remains current; old baseline result stored in history; HUD displays new baseline and reassessment status |
| C40 Repo contains JUnit PASS written by coding agent, update points evidence_path to that file, no verifier/CI run | Criterion not verified (`reported_artifact_only`); HUD displays file as reported claim with file; verifier sandbox re-execution determines verdict |
| C41 Owner approves waiver for required criterion, then waiver expires | While valid: criterion waived, excluded from denominator, acceptance readiness satisfied; upon expiry: reassessment triggered, criterion reverts to evidence-backed state |
| C42 Repo copied to new directory for different product but retains project_id | identity_conflict; new location quarantined; updates across locations not merged; owner issues new ID or confirms clone |
| C43 User views portfolio aggregate without permission for one project | Total includes only authorized projects, labeled "partial — authorized projects only", without leaking count, names, or weights of hidden projects |
| C44 Project has draft acceptance.yaml, baseline not yet approved | Assessment provisional, progress.basis=none, no acceptance readiness; once owner approves baseline → reassessment triggered (baseline_changed) |
