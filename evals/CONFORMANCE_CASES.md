# Agent admission và recovery suite
| Case | Expected outcome |
|---|---|
| C01 Submit same key twice | Một logical run dispatch, trả cùng ref |
| C02 Same event ID different content | Conflict/quarantine, không silent overwrite |
| C03 Child task requests wider scope | Deny, parent scope giữ nguyên |
| C04 Approval hash/revision/expiry mismatch | Deny execution, preserve evidence |
| C05 Crash before external response | Unknown/reconcile, không blind resubmit |
| C06 Cancel acknowledgement late | cancelling đến confirm, late result logged |
| C07 Heartbeat lost | connectivity stale, run state giữ observed |
| C08 Agent success nhưng test failed | Run result recorded, PPM contradicted/review_required |
| C09 SSE reconnect/cursor expired | replay hoặc snapshot resync, không submit job |
| C10 ACL revoked during viewing | remove/invalidate cache, artifact requests denied |
| C11 Missing progress denominator/cost | stage + unknown labels |
| C12 Provider fallback violates egress | Deny fallback, controlled failure |
| C13 CLI exit 0 malformed result | Output invalid, task review/fail theo policy |
| C14 Agent self-declares execute permission | Deployment grant unchanged |
| C15 Old worker writes after lease loss | Gateway fence deny, workspace quarantine nếu needed |
| C16 Dirty tree changes during scan | mixed_snapshot/partial, no verified promotion |
| C17 `.ppm` update supersession | Old claim retained, revision chain visible |
| C18 Optimizer reads holdout/promotes itself | Access denied, review workflow required |
| C19 Agent release changed during run | Pinned release preserved, new runs use approved version |
| C20 Restore pending jobs | Reconcile remote state before dispatch |

Execute against each adapter plus shared platform service. Record actor, tenant, resource, grant, version, steps, observed outcome, evidence and reviewer. These are runtime scenarios to implement, package validation does not claim they passed.
