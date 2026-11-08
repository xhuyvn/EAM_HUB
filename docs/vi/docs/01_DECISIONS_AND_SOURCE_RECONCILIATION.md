> Translated from docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md @ d7c9e91

# Quyết định hợp nhất và migration
## Thứ tự thẩm quyền
Master/platform contracts quyết định execution/auth/events. PPM contract 2.0 quyết định repository reporting. Domain specs quyết định business validation. Source archives được ghi hashes trong manifest phục vụ lineage. Các lựa chọn sau là đề xuất đã chốt cho bản thiết kế tích hợp và được kiểm chứng tại P0.

| Xung đột trước | Quyết định tích hợp | Migration |
|---|---|---|
| Secretary TS/SQLite, enterprise Python/PostgreSQL | Python control plane + PostgreSQL, vendor TS worker được phép | Wrapper giữ API riêng, transfer task mapping qua legacy_id |
| Enterprise harness PydanticAI, PPM LangGraph | LangGraph orchestration owner | PydanticAI adapter/candidate phase 2, không dual scheduling |
| Enterprise ưu tiên Knowledge trước PPM | Phase 1 đưa PPM v2 vào trước | Knowledge facade cho evidence retrieval, full corpus roadmap sau pilot |
| GUI/open-session automation | Capability riêng, observe-only trước khi certify control | Explicit handover, scope, session identity |
| Qdrant/S3 mặc định | Search/object-storage theo nhu cầu pilot | Giữ interface và migration path |
| Project task state và execution run trộn | Task/Run/Connectivity/PPM acceptance tách | Mapping state theo bảng runtime |
| `.ppm` file tự khai quyền | Authority ở deployment binding | Registry owner-controlled nhập qua admin API |
| Harness core ở v1 và optimization phase 2 | Core controls phase 1, improvement experiments phase 2 | Tập baseline ngay từ pilot |

PPM package đi kèm kế thừa source contract/templates. File PPM blueprint và prompt M0 được thêm platform integration notice để coder dùng roadmap P0–P5 trong platform. PPM M0–M4 cũ là backlog domain để map, PPM M5 executor dùng shared platform adapters. Các prompt adoption/end-session của `.ppm` tiếp tục dùng cho dự án độc lập.

## ADRs
ADR-001 PostgreSQL authoritative state, connector spool durable nhưng không tự approve offline.
ADR-002 Một workflow owner mỗi run, external agents chạy qua adapter contract.
ADR-003 Agent package proposal và deployment binding là hai trust levels.
ADR-004 Event store + projection transaction là nguồn HUD, trace sampling tách audit.
ADR-005 PPM verification do domain reducer, provider success là observation.
ADR-006 Baseline harness có controls phase 1, optimization releases phase 2.
ADR-007 Tool API/MCP boundary áp policy riêng, MCP transport không thay business authorization.
ADR-008 Reuse existing agent bằng wrapper, capability unknown hiển thị unavailable.
ADR-009 Chat delivery qua durable outbox với user opt-in và verified recipient binding.
ADR-010 Backup restore áp cả DB/blobs/ACL, mục tiêu RPO/RTO chốt qua pilot.

## Legacy import
Inventory phiên bản và schema nguồn → backup → map IDs → import dry-run → compare counts, approvals và source hashes → owner review → cut over → giữ rollback window. Pending approvals nguồn cũ hết hiệu lực khi payload/schema bị đổi và phải tạo proposal mới. Unknown sessions giữ observe-only cho đến nhận dạng/ownership rõ ràng. Không coi nội dung lịch sử mẫu là dự án thật.

## Revision 1.1 (06/11/2026) — quyết định sau architecture review
Bằng chứng và finding IDs nằm trong `docs/ARCHITECTURE_REVIEW.md`.

| ADR | Quyết định | Phương án bị loại và lý do | Bằng chứng sẽ làm đổi quyết định |
|---|---|---|---|
| ADR-011 (ARCH-03) | Run Service/PostgreSQL là owner duy nhất của run state, dispatch journal, lease/fencing, cancel. LangGraph chỉ chạy bên trong một attempt của native agent, không gọi side effect trực tiếp. | Giữ LangGraph là orchestration owner cho mọi run: hai nguồn trạng thái (checkpoint vs bảng runs), replay chạy lại node từ đầu nên adapter submit trong node có thể lặp. Temporal: mạnh về durable workflow nhưng thêm cluster vận hành cho pilot một máy. | Nếu P1 đo được nhu cầu workflow nhiều ngày với nhiều activity phức tạp mà journal tự viết không đáp ứng, mở ADR migration sang Temporal cho workflow cụ thể. |
| ADR-012 (ARCH-02) | PPM assessment proposal 1.1: per-criterion status/reason/evidence, progress numerator/denominator, không chứa acceptance. Reducer deterministic là nơi duy nhất đặt verified. | Giữ ppm-output 1.0: không giải thích được lý do, cho phép `verified` kèm missing evidence và `contradicted`+`accepted`. | Không có; thay đổi là sửa lỗi contract. |
| ADR-013 (ARCH-06/07) | Location registration là record platform-trusted, chứa artifact aliases; repo chỉ đề xuất alias. Mỗi deliverable có một owner project; consumer project dùng `dependency_refs`; portfolio cộng theo deliverable_id duy nhất. | Tự động discovery `blueprint*.md` từ repo: file cài cắm có thể thay mapping. Chia phần trăm deliverable dùng chung: không có căn cứ phân bổ. | Nếu owner cần attribution lợi ích, thêm contribution record có approval, vẫn không cộng trùng. |
| ADR-014 (ARCH-10) | Approval gắn digest SHA-256 trên RFC 8785 JCS của trường proposal cố định, nonce và operation_key; decision single-use, consume cùng transaction với execution intent. | Digest "arguments hash" không chuẩn hóa: hai service có thể băm khác nhau hoặc approval bị dùng cho target khác. | Không. |
| ADR-015 (ARCH-12) | Experiment 1.1 bắt buộc primary metric + non-inferiority margin + guardrail per-stratum, paired design, holdout evaluator-only, proposer ≠ reviewer. | Chỉ so sánh trung bình chất lượng/chi phí: candidate rẻ hơn có thể giảm chất lượng ở stratum nhỏ mà trung bình không đổi. | Nếu dataset đủ lớn để ước lượng effect size theo stratum, có thể đổi margin dựa trên power analysis. |
| ADR-016 (ARCH-05/15) | Binding 1.1 liệt kê fallback/embedding/telemetry profiles; Model Gateway deny-by-default. Vendor server-side tools là `external_trusted`. | Một `model_profile_ref` duy nhất: fallback/embedding/telemetry đi ngoài policy. | Không. |

### Contract 1.1 — compatibility, migration, rollback
- Đổi sang `contract_version: "1.1"`: event, result, task-envelope, action-proposal, binding, experiment, ppm-output (assessment proposal). Mới: approval-decision, location-registration, hud-projection, run-command. Giữ 1.0: agent, task-request, ppm-input, research-input/output.
- Đây là thay đổi breaking (trường required mới, đổi `acceptance`→`acceptance_readiness`, enum mới). Không có consumer đã triển khai (repository không có application code tại revision `0f91f2d`), nên không cần dual-read. Từ P1, mọi đổi contract phải theo expand/contract: thêm trường optional trước, producer ghi cả hai, consumer chuyển, rồi mới bỏ trường cũ; reader từ chối major lạ và chấp nhận minor cao hơn chỉ khi không có required field mới.
- PPM portable contract vẫn là 2.0. Sửa lỗi schema: `updated_at`/`recorded_at` thêm pattern bắt buộc timezone — văn bản contract đã yêu cầu điều này; file hợp lệ trước đây không bị ảnh hưởng, file thiếu timezone trước đây bị chấp nhận nhầm.
- Rollback: revert các file trong `contracts/`, `examples/`, `templates/`, `tools/validate_package.py` về revision `0f91f2d`; không có dữ liệu runtime cần migrate.

### Vòng 2 (06/11/2026, góp ý của owner) — bổ sung vào contract 1.1 trước khi có consumer
| ADR | Quyết định | Phương án bị loại |
|---|---|---|
| ADR-017 (ARCH-20) | Tách ba khóa: `task_intent_key` (Task Service, ổn định qua attempt), dispatch `idempotency_key` (per run), `operation_key` (Action Service, theo business intent). Cùng operation_key khác nội dung → 409 `OPERATION_KEY_CONFLICT`. | Khóa suy ra từ tham số request: không phân biệt được hai ý định giống hệt nhau và không phát hiện được cùng khóa khác target. |
| ADR-018 (ARCH-21) | Evidence chỉ verify criterion khi đúng producer, đúng assessed revision, đúng baseline, report chứa test được map, và không có FAIL cùng revision. Claim tách riêng trong assessment. | Coi mọi evidence_ref resolve được là hợp lệ. |
| ADR-019 (ARCH-22) | Nhiều project cùng repository với metadata_root khác nhau; duplicate = trùng nguồn + metadata_root. | Một repository ↔ một project (chặn monorepo). |

Thay đổi contract (vẫn là 1.1 vì chưa phát hành cho consumer): `ppm-output` thêm `assessed_revision`, `previous_assessment_id` và các trường per-criterion `reported_status`, `reported_claim_refs`, `evidence_revision`, `covered_test_ids`, `previous_status`; `task-envelope` thêm `task_intent_key`; cập nhật mô tả `action-proposal.operation_key` và `location-registration`. Fixture mới: `examples/action-proposal-retry.json`, `examples/locations-monorepo.json`. Rollback: như ghi chú contract 1.1 ở trên.

### Vòng 3 (06/11/2026)
| ADR | Quyết định | Phương án bị loại |
|---|---|---|
| ADR-020 | Offset HUD/event do một outbox relay cấp sau commit (thứ tự commit), không cấp trong transaction ghi. | bigserial trong transaction ghi: lấy offset 10/11 rồi commit ngược thứ tự làm mất event khi replay. |
| ADR-021 (rev 2) | Con trỏ assessment hiện hành chỉ tiến theo `basis_seq` (CAS) được cấp lúc tạo assessment-task cho mọi trigger (`snapshot`, `baseline_changed`, `evidence_ttl_expired`, `model_recovered`, `manual`), khóa input lúc tạo; assessment trễ của `basis_seq` cũ thành lịch sử (C34, C39). Bước model là tùy chọn; reducer vẫn chạy khi model lỗi (`analysis_status`). *(Ghi chú sửa đổi: cập nhật từ CAS snapshot_seq ban đầu sang CAS basis_seq rev 2 để giữ thứ tự deterministic khi đánh giá lại trên cùng snapshot; contracts, examples, và `eap.contracts.rules.apply_assessment` đồng thuận).* | "Ghi đè bản xong sau cùng": lỗi khi scan hoàn tất ngược thứ tự. Chặn toàn bộ assessment khi model lỗi: mất verdict deterministic. CAS chỉ theo `snapshot_seq`: không thể sắp thứ tự đánh giá lại trên cùng snapshot (C39). |
| ADR-022 | Tách `source` vật lý (`source_id`) khỏi project scope; Scan Scheduler và Assessment Orchestrator là module PPM chạy trên Task/Run Service. | Gộp location = project (chặn monorepo); cron hệ điều hành (scheduler thứ hai, không audit). |
Contract (vẫn 1.1, chưa có consumer): `ppm-output` thêm `snapshot_seq`, `analysis_status`; `location-registration` thêm `source_id`. Fixture `examples/ppm-output.json` dùng cùng AC-001 cho note "đã xong" + PASS rev-r1 + FAIL rev-r2.

### Vòng 4 (06/11/2026)
- ADR-017 rev. 2: `action_slot` do workflow/Action Service cấp hoặc xác thực, lưu trong `task_intents` trước lần gọi tool đầu tiên, phục hồi khi retry; slot do agent đề xuất sau restart không tạo khóa mới (C29b). Phương án bị loại: tin slot agent khai (agent đổi `create_pr#1`→`#2` tạo PR thứ hai).
- ADR-021 rev. 2: con trỏ assessment hiện hành theo `basis_seq` (cấp lúc tạo assessment task, cho mọi trigger: snapshot, baseline_changed, evidence_ttl_expired, model_recovered, manual), không chỉ theo `snapshot_seq`. Contract `ppm-output` thêm `basis_seq`, `trigger` (vẫn 1.1, chưa có consumer). Phương án bị loại: chỉ CAS theo `snapshot_seq` (không xử lý được reassessment trên cùng snapshot).

### Từ review PPM v2 (06/11/2026, `ppm-v2/docs/ARCHITECTURE_REVIEW.md`)
- ADR-023: chỉ `verifier_observed` hoặc `ci_attested` verify criterion; report trong repo là `reported_artifact`. Contract `ppm-output` thêm `evidence_trust` (vẫn 1.1, chưa có consumer). Thêm P2a-WP9 Verifier. Phương án bị loại: tin file report trong repo khi khớp revision (producer tự viết được file đó).
- ADR-024: waiver lưu ở PPM DB, `status=waived` + `waiver_ref`, ra khỏi mẫu số. Phương án bị loại: biểu diễn waiver bằng `verified` (che criterion bị bỏ qua) hoặc giữ trong mẫu số (tiến độ không bao giờ đạt dù được duyệt).
- PPM contract 2.1 (tương thích 2.0): `verification_refs`, `criterion_ids`, `test_ids`, `uses_component`; registry YAML là định dạng import sang `location-registration`.

### Đóng P0 — Ký duyệt DG-1 và DG-2 (07/11/2026)
Owner xhuyvn (Huynn), chủ dự án đã phê duyệt toàn bộ quyết định trong `docs/adr/DG-1.md` (DG-1.1 sinh khóa và tính idempotent, DG-1.2 thẩm quyền action slot, DG-1.3 offset outbox relay, DG-1.4 source và project scope) và `docs/adr/DG-2.md` (DG-2.1–DG-2.5 scheduler, thứ tự, chính sách vận hành, trigger, khóa input). Các điều kiện ràng buộc DG1-C1…C5 và DG2-C1 được liệt kê tại §0.1 của mỗi biên bản và là yêu cầu nghiệm thu của các gói công việc nêu tên. Hiệu lực: **P0 closed**; P1a-WP1 có thể bắt đầu.
Tu chính A1 (owner phê duyệt 2026-11-07): toàn bộ khóa ngoại là composite key trong phạm vi tenant (tenant_id, <col>) tham chiếu cha (tenant_id, <pk_col>); phát hiện trong đợt review P1a-WP1.

### D-01 — Quyết định sử dụng LangGraph (07/11/2026)
Owner xhuyvn (Huynn) chọn **phương án (b)** từ `docs/spikes/P0-WP4.md`: LangGraph (ghim cứng `langgraph==1.2.20`, `langgraph-checkpoint==4.3.1`) chỉ có thể điều phối các bước agent **bên trong một lần chạy duy nhất (single run attempt)**, với `thread_id = "<run_id>:<attempt>"`. Tính năng resume/replay của LangGraph bị **vô hiệu hóa đối với các workflow có tác dụng phụ (side effects)**; phục hồi và thử lại do journal của Run Service quản lý (ADR-011), tạo ra attempt mới, và Action Service khử trùng lặp tác dụng phụ qua `action_slot` + `operation_key` (DG-1). Bằng chứng: resume nguyên bản nhân đôi tác dụng phụ trong 20/20 lần thử; bọc qua DG-1 tạo chính xác một tác dụng phụ trong 20/20 lần thử (chủ dự án chạy lại đã xác nhận). Bị loại bỏ: (a) resume LangGraph cho workflow có tác dụng phụ (nhân đôi, hai nơi nắm giữ trạng thái); (c) không dùng LangGraph (hạn chế không cần thiết). Bằng chứng bền vững về exactly-once thuộc về P1a-WP4 (C29, C29b). Xem xét lại nếu LangGraph có được tính nguyên tử giữa checkpoint và tác dụng phụ qua transaction, hoặc nếu độ trễ khởi động lại từ attempt vượt quá 10 phút cho các workflow chỉ đọc (khi đó resume có thể được cho phép chỉ cho các phân đoạn chỉ đọc).

### Chuẩn bị triển khai (06/11/2026, plan rev 1.5)
- ADR-025 Ngôn ngữ repo: tiếng Anh là bản gốc từ P0-WP0; agent đọc và sửa bản tiếng Anh. Bản tiếng Việt lưu ở `docs/vi/<đường dẫn gốc>` với header `Translated from <path> @ <sha>`, chỉ để owner review, agent không đọc (`AGENTS.md`). SC-08 báo bản dịch cũ. Phương án bị loại: hai bản song song cùng là gốc (lệch nhau, tái tạo lỗi "hai blueprint cạnh tranh"); giữ tiếng Việt là gốc và chỉ dịch prompt (agent vẫn đọc review/plan/docs bằng tiếng Việt, tốn token hơn — mức tiết kiệm thật sẽ đo ở P0-WP0).
- ADR-026 Môi trường dev: Python 3.12, PostgreSQL 18.6, Node 26 LTS; Redis 8.10.1 và Qdrant 1.20.0 có trong compose profile `optional`, chỉ giữ dữ liệu dẫn xuất (`docs/DEV_ENVIRONMENT.md`). Không đổi quyết định work queue PostgreSQL `SKIP LOCKED` và FTS; bật Redis/Qdrant thành bắt buộc cần ADR mới kèm số đo. Phương án bị loại: Redis làm queue cho run/dispatch (journal và fencing phải nằm cùng transaction với run state ở PostgreSQL).
