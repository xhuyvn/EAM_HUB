> Translated from docs/05_PHASE2_HARNESS_OPTIMIZATION.md @ fb857f4

# Phase 2 — Harness Engineering & Optimization
## Charter
Cải thiện reliability, grounding và chi phí của các domain agent qua các experiment có thể tái lập. Harness gồm instructions, context builder, tool contract, planner/stop conditions, memory/compaction, recovery, verifier và observability. Policy enforcement và domain source of truth tiếp tục do platform services sở hữu.

## Harness Optimization Agent
Input là trace đã redact/authorize, failure labels, domain eval summaries, runtime/profile versions và engineering constraints. Output là hypothesis, candidate change, experiment plan và recommendation có evidence. Agent được tạo branch/config candidate trong sandbox, gọi experiment service với dataset được cấp. Promotion vào deployment do release workflow với reviewer có quyền.

### Workflow
Detect repeated failure → cluster failure taxonomy → inspect authorized evidence → propose minimal candidate → validate schema/security diff → offline eval with frozen baseline → reviewer assessment → shadow read-only → limited canary → compare → approve promotion hoặc rollback.

Các failure classes gồm retrieval miss, context overflow, stale evidence, tool selection/arguments, unauthorized attempts, loop/nontermination, schema invalid, wrong acceptance, provider timeout, resume/replay errors và high-cost redundant calls.

## Optimization backlog
| Thử nghiệm | Thay đổi có thể làm | Thước đo |
|---|---|---|
| Context selection | Scope filtering, semantic/keyword fusion, evidence ordering | Relevant evidence recall, answer grounding, tokens |
| Compaction | Structured handoff, summary provenance, restore plan state | Long-run continuity, missing requirement rate |
| Tools | Schema/descriptions, bounded output, tool discovery | Tool argument validity, correct action rate |
| Planning | Fixed workflow vs bounded planner, delegation threshold | Completion, tool count, coordination overhead |
| Model routing | Local/cloud profile by task class | Quality by class, cost and latency under policy |
| Verification | Acceptance checks and citation rules | False success, reviewer correction rate |
| Recovery | Retry classification, idempotency, checkpoint granularity | Lost/duplicate work, resume success |
| Reuse | Context/cache keyed by input hash + ACL/policy version | Cost saving without stale/unauthorized access |

Mọi cache có tenant/resource ACL và invalidation khi source/policy/model thay đổi. Model compatibility được probe, OpenAI-compatible API không đủ chứng minh tool/structured output correctness. Local-only domain không chuyển cloud trong experiment.

## Experiment protocol
Mỗi experiment có experiment_id, candidate/base bundle digest, task set version, execution environment, provider/model immutable config, seed nếu hỗ trợ, trial count, budgets, gates và evaluator version. Giữ freeze holdout ngoài optimizer permission, chỉ trả aggregate/curated failure summaries. Synthetic/anonymized fixtures trước live data, xử lý quyền source và output đầy đủ.

Đề xuất baseline ít nhất 100 tasks phân tầng theo domain/risk, 5 trials cho critical scenarios. Report numerator/denominator, confidence intervals và per-domain failures. Chạy paired trials trên cùng task/environment, ghi thời điểm vì provider drift có thể ảnh hưởng. LLM grader bổ sung semantic rubric, deterministic oracle cho authorization/state/idempotency. Có SME review cho PPM acceptance và governance content.

## Promotion gates
Critical policy/security regressions phải bằng 0 trên suite đã định. Primary task-quality metric không giảm vượt tolerance đã duyệt. Ví dụ tolerance 2 percentage points là cấu hình experiment, cần justification. Cost target đề xuất giảm 15% ở chất lượng tương đương hoặc tăng completion 5 points trong budget; lựa chọn một objective trước chạy. Test set hữu hạn không chứng minh zero production risk.

Canary giới hạn deployment subset, task class và budget. Shadow mode chỉ đọc hoặc dùng simulated side effects. Canary real write chỉ khi grant/approval cho phép. Triggers rollback gồm false acceptance, unauthorized action, elevated failure rate so baseline và budget runaway. Run đã bắt đầu pin old release; new release áp new runs. Khi security revocation, dừng new calls và reconcile active work. Rollback config khác với hoàn tác external side effects, cần compensation procedure riêng.

## Phase 2 milestones
H0 trace quality baseline + benchmark report.
H1 context/tool/model routing experiments với ít nhất hai candidates và control.
H2 Optimization Agent tạo candidate và experiment có bounded scope, reviewer chọn release.
H3 canary/rollback drill và regression monitoring, acceptance bởi domain owner + platform owner.

## Bàn giao
Experiment manifest, dataset lineage, version locks, paired result table, quality/cost scatter, failure examples đã redact, reviewer decision, deployment record và rollback proof. HUD Harness Lab hiển thị thông tin này cùng trạng thái candidate/approved/canary/promoted/reverted.

## Revision 1.1 — phát hiện candidate rẻ hơn nhưng giảm chất lượng
Experiment contract 1.1 bắt buộc khai báo trước khi chạy: `change_kind` (prompt/tools/retrieval/context/model_routing/…), một `primary_metric` với `non_inferiority_margin`, ít nhất một `guardrail_metrics` (khuyến nghị PPM: false_verified_rate=0 per stratum, unsupported_claim_acceptance=0, critical_policy_violations=0), `pairing=paired_same_case_same_environment`, `holdout_access=evaluator_only`, `proposer_ref ≠ reviewer_ref`.

Quy trình đánh giá:
1. Baseline selection: base_release_digest là release đang active, chạy lại trên cùng task set/environment/model profile trong cùng cửa sổ thời gian với candidate (không dùng số liệu lịch sử) để loại provider drift.
2. Paired evaluation: mỗi case chạy base và candidate với cùng input snapshot, seed nếu hỗ trợ, `trials_per_case` lần; báo cáo hiệu số theo cặp và khoảng tin cậy (bootstrap theo case).
3. Non-inferiority: candidate chỉ đạt khi cận dưới khoảng tin cậy của (candidate − base) ≥ −margin cho primary metric, và mọi guardrail đạt ở từng stratum (domain × risk). Giảm chi phí không bù cho guardrail fail.
4. Silent quality loss: so sánh thêm phân phối độ dài output, số evidence ref, tỷ lệ "unknown/missing evidence" và tỷ lệ tool call bị bỏ — candidate rẻ hơn thường giảm bằng cách làm ít việc hơn. Mỗi tín hiệu lệch quá ngưỡng đã khai báo chuyển experiment sang `review_required` với ví dụ đã redact.
5. Grader drift: LLM grader pinned theo evaluator_version, được hiệu chỉnh trên tập nhãn người; deterministic oracle cho authorization/state/idempotency/verified-criterion. Holdout chỉ evaluator service đọc; optimizer chỉ nhận aggregate.
6. Rollout: shadow (read-only/simulated side effects) → canary giới hạn `max_traffic_percent` và task class → promote. Rollback triggers khai báo trong `rollout.rollback_trigger_refs`; run đang chạy giữ release cũ.

Phase 1 vẫn sở hữu permissions, recovery, audit và thu thập eval evidence (baseline dataset, trace redacted); Phase 2 chỉ thêm optimizer và experiment pipeline trên nền đó.
