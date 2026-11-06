# PPM v2 như một managed domain agent
## Các capability
ppm.inventory.scan đọc location registry đã duyệt. ppm.project.assess đối chiếu baseline/criteria/evidence. ppm.program.impact duyệt dependency graph. ppm.portfolio.scenarios tính constraints và ưu tiên. ppm.acceptance.propose tạo review packet. Acceptance commit là domain action của authenticated reviewer.

Collector deterministic là worker của PPM domain. Analysis agent nhận scoped snapshot refs, có thể dùng model local/cloud qua Model Gateway. Scan làm việc với `.ppm` v2 và file mapping architecture/blueprint/implementation_plan. Task scan, task assessment và project acceptance có lifecycle riêng.

## Ownership
| Entity | Authority |
|---|---|
| Agent/release/binding, task/run, platform policy, budget | Platform |
| Host location registration, connector root grants | Platform admin, referenced by PPM |
| Project/program/portfolio, criteria, baseline, assessment | PPM domain service |
| Portable `.ppm` updates và notes | Repo producers, reported claims |
| Evidence snapshot and ACL/provenance | Evidence service |

V2 local registry được import sang Platform Location Service. ID project giữ nguyên, thêm tenant_id từ context; project IDs chỉ unique trong tenant. `.ppm` không chứa platform credentials hoặc auth grants. Cloud/access policy trong registry cũ map vào trusted binding/admin policy sau review.

## End-to-end
1. Owner đăng ký project location, root và metadata mapping.
2. Scheduler tạo scan task theo PPM scan policy, dedup schedule occurrence ID.
3. Connector scan files/hash/Git snapshot, emit progress x/y và evidence refs.
4. PPM ingest journal, giữ invalid/partial/mixed snapshot status.
5. Assessment task đối chiếu claims/criteria/tests; raw report trong repo vẫn là reported evidence cho đến kiểm tra nguồn và scope.
6. PPM lưu assessment + domain event/outbox trong transaction.
7. HUD nhận run completion và project assessment update riêng.
8. Work package cần coding được submit sang Engineering adapter, completion trả artifact vào PPM verification.

## Tiến độ và chất lượng
Verified progress tính theo baseline weighted criteria đủ evidence. Reported progress giữ riêng và null khi không biết. Project delivered/closed không quyết định operation running/retired. Program benefits đo bằng outcome metric, portfolio constraints gồm reviewer capacity, GPU time và budget riêng.

## Mapping PPM milestones
PPM M0 → Platform P0; M1 → P2; M2 → P2/P3; M3/M4 → P4; M5 executor → shared adapter P4 và các capability nâng cao có gate riêng; M6 → P5. Các prefix tránh nhầm với phase 2 H0–H3.

## Migration acceptance
Onboard 5 project đại diện gồm đang làm, lịch sử hoàn thành, không Git, monorepo và shared component. Giữ identity, counts và provenance. Test stale evidence, duplicate update ID/different content, traversal/symlink, watcher loss, offline catch-up, multiwriter status và scope changes. Chỉ formal acceptance khi criteria + approval đúng baseline.

## Revision 1.1 — location, claim→assessment, shared component
### Location registration và aliases
`POST /v1/ppm/locations` tạo `location-registration` 1.1 ở state `proposed`; owner/admin approve chuyển `active`. Record lưu root_path tuyệt đối, metadata_root, scan_scope, `artifact_aliases` cho architecture/blueprint/implementation_plan (ví dụ `implement_plan.md`, `docs/IMPLEMENTATION_PLAN.md`), notes_globs, deny_globs, classification và egress_policy_ref. Thứ tự resolve: (1) `.ppm/project.yaml` artifact_mapping nếu path tồn tại trong scope; (2) alias đầu tiên tồn tại trong registration; (3) không tìm thấy → mapping null + coverage gap. Discovery heuristics (`blueprint*.md`, ARCHITECTURE.md…) chỉ tạo onboarding proposal, không tự đổi mapping. Quan hệ location–project: một repository (source) có thể được nhiều project đăng ký (monorepo), mỗi project một `metadata_root` riêng, aliases/notes/criteria riêng. Chỉ từ chối (409) khi trùng cả nguồn (`canonical_root_fingerprint` hoặc `repo_identity`) và `metadata_root`, hoặc khi một `metadata_root` bị hai project nhận. `scan_scope` được phép chồng nhau để đọc (ví dụ `libs/shared`), nhưng việc tính điểm do owner của deliverable quyết định. Collector chụp một snapshot cho mỗi source mỗi lần scan và chia view theo scan_scope của từng project; file chung được hash một lần (ví dụ `examples/locations-monorepo.json`). Clone/worktree là location khác của cùng project và không được cộng tiến độ riêng.

### Từ claim tới verified assessment
1. Collector snapshot: file hash, HEAD/dirty manifest, `.ppm/updates` → ReportedUpdate (claim), notes → Note (observation, dedup theo content hash; cùng note copy ở hai project là hai claim độc lập, không tăng evidence).
2. Evidence chỉ đến từ collector/verifier: test report có revision, runtime probe, artifact inspection. `checks[].result=passed` trong update là claim; trở thành evidence khi collector thu được report tại `evidence_path` khớp `tested_revision` với revision được đánh giá.
3. Analysis run (local model qua Model Gateway) tạo `ppm-output` 1.1 proposal: map claim/evidence → criterion, giải thích ngắn.
4. PPM reducer deterministic kiểm tra lại từng criterion: evidence tồn tại, thuộc project, đúng baseline_revision và revision/environment; `mixed_snapshot` hoặc dirty tree không được verified; refuting evidence → contradicted. Reducer có thể hạ cấp nhưng không bao giờ nâng cấp so với rules, bất kể nội dung proposal.
5. Assessment lưu trong PPM DB với baseline_revision, validity và outbox `ppm.assessment.updated`. `acceptance_readiness=ready_for_review` chỉ khi verified.
6. Formal acceptance: reviewer có role theo binding/IdP gọi `POST /v1/ppm/projects/{id}/acceptance` với assessment_id + baseline_revision + expected_revision; server kiểm tra assessment còn current. Run success, update "accepted" hoặc chữ ký trong repo không có hiệu lực.

### Quy tắc bằng chứng của reducer
Một `evidence_ref` mở được vẫn chưa đủ để xác minh criterion. Criterion chỉ `verified` khi đồng thời:
1. Nguồn tạo: chỉ `verifier_observed` (verifier sandbox chạy lại test/probe được map tại đúng revision, P2a-WP9) hoặc `ci_attested` (CI có provenance do owner tin cậy) mới verify. Collector *thấy* một file report trong repo không có nghĩa là *quan sát* được kết quả test: file đó là `reported_artifact`, chỉ là claim kèm file (PASS không verify; FAIL vẫn được dùng như tín hiệu phản bác). Note, `.ppm/updates`, status và text của model là claim; reducer ghi chúng vào `reported_status`/`reported_claim_refs` và không bao giờ đưa vào `supporting_evidence_refs` (`evidence_wrong_producer` nếu có).
2. Revision: `evidence_revision` = `assessed_revision` của snapshot đang đánh giá (non-Git: tree hash). PASS ở commit cũ không verify commit hiện tại (`evidence_stale_revision`).
3. Baseline: criterion thuộc baseline_revision đang hiệu lực; baseline đổi → stale.
4. Phạm vi kiểm thử: baseline lưu test mapping cho từng criterion (test ids do owner duyệt khi chốt baseline). Report phải chứa các test đó với kết quả passed; PASS của test khác → `coverage_not_mapped`.
5. Không có phản bác: FAIL của test được map ở cùng revision → `contradicted`, vượt lên mọi PASS cũ và mọi claim "đã xong".
`previous_status` + `previous_assessment_id` cho HUD trả lời "từ lần trước đến nay criterion nào đổi, bằng chứng nào". Ví dụ đầy đủ: `examples/ppm-output.json` (AC-001: note "đã xong", PASS ở rev-r1, FAIL do collector thu ở rev-r2 trên cùng test được map → contradicted; vì AC-001 là required nên acceptance bị chặn).

### Waiver và baseline từ draft
Waiver được duyệt trong PPM DB (approver, lý do, scope, expiry) → criterion `status=waived` + `waiver_ref`, ra khỏi mẫu số tiến độ, được tính là thỏa cho acceptance readiness đến khi hết hạn (hết hạn tạo trigger reassessment). Trước khi owner tạo baseline từ `acceptance.yaml` (draft), assessment chỉ provisional với `progress.basis=none` (PPM contract §14.5).

### Quét lại tăng dần
Mỗi scan so hash file theo scan_scope + metadata_root của từng project với snapshot trước. Project không có file thay đổi trong phạm vi của mình không tạo assessment mới (giữ assessment cũ, cập nhật `last_successful_scan_at`). File chung đổi (ví dụ `libs/shared`) tạo revalidation cho owner project của deliverable và cho consumers có `dependency_refs`.

### Thay đổi và lỗi nguồn
- Path không khả dụng/ổ ngắt/máy ngủ: scan `failed` hoặc `partial`, giữ assessment cũ với validity `stale` sau TTL, `connector.status` stale/offline; không tạo progress 0 hay no_change.
- Evidence cũ: test report có tested_revision ≠ revision hiện tại của module liên quan → criterion `stale`; khi chưa có mapping tác động, quy tắc bảo thủ coi mọi commit mới là stale.
- Baseline đổi (revision mới được approve): mọi assessment của baseline cũ → `stale`, acceptance gắn baseline cũ → `superseded`, tạo revalidation scan task.
- Update trùng ID khác nội dung: conflict, không last-write-wins; supersedes giữ chuỗi revision.

### Shared component và portfolio
Một deliverable có đúng một owner project (ví dụ gateway dùng chung là project/component riêng). Consumer project tham chiếu qua `dependency_refs` và không đưa criteria đó vào mẫu số của mình. Program/portfolio cộng verified weight theo tập deliverable_id duy nhất của owner project; evidence dùng chung được lưu một lần (content-addressed) và tham chiếu nhiều lần. Rollup hiển thị contribution, không lấy trung bình phần trăm các project. Test: fixture hai project dùng chung gateway, tổng portfolio = tổng weight distinct (C24).

### Thuật ngữ
PPM blueprint dùng "autonomy L0–L4"; platform dùng "integration level L0–L3" (docs/06). Trong tài liệu platform, autonomy PPM được gọi A0–A4 để tránh nhầm: A0 đọc/phân tích, A1 draft/proposal (MVP), A2 cập nhật hành chính, A3 dispatch có budget, A4 merge/deploy/ưu tiên chiến lược.

## Revision 1.1 vòng 3 — điều phối scan → assessment
### Source vật lý và phạm vi project (ADR-019, bổ sung)
Location Service tách hai thực thể. `source` là thư mục/repository vật lý: host_id, canonical root, repo_identity; mỗi thư mục chỉ có một `source_id`. `project scope` (`location-registration` có `source_id`) gồm metadata_root, scan_scope, aliases, notes_globs. Nhiều scope có thể dùng chung một source. `(source_id, metadata_root)` là duy nhất và thuộc đúng một project.

### Ai điều phối
PPM Scan Scheduler sở hữu lịch quét. PPM Assessment Orchestrator sở hữu chuyển tiếp snapshot → assessment. Cả hai là module trong PPM domain, chạy qua Task/Run Service như mọi task khác; không có scheduler thứ hai.
1. Scheduler (bảng `ppm_scan_schedules`, lease theo location) tạo scan task khi đến interval, khi watcher debounce báo thay đổi, khi khởi động có location quá hạn, hoặc khi gọi thủ công. Mỗi lần kích hoạt có `occurrence_id` duy nhất; trùng occurrence là no-op. Mỗi source chỉ có một scan đang chạy.
2. Scan task commit snapshot và cấp `snapshot_seq` tăng dần theo (source_id, project) trong cùng transaction. Outbox `ppm.snapshot.captured`.
3. Orchestrator nhận event và tạo assessment task, dedup theo `snapshot_id`. Nếu snapshot giống snapshot trước theo hash từng project scope, nó không tạo assessment mới: chỉ cập nhật `last_successful_scan_at` và không nhân đôi note hay tiến độ.
4. Assessment task chạy bước model (tùy chọn, trích claim từ text tự do) rồi reducer deterministic. Model lỗi hoặc timeout → reducer vẫn chạy trên evidence và claim có cấu trúc, `analysis_status=model_failed`; HUD hiển thị "claims từ note chưa được xử lý". Reducer lỗi → task failed, assessment hiện hành giữ nguyên, HUD báo "snapshot mới chưa được đánh giá".
5. Ghi assessment dùng compare-and-set con trỏ `project_current_assessment`, chỉ tiến khi `basis_seq` lớn hơn (xem "Reassessment" bên dưới; `basis_seq` tăng theo thứ tự snapshot nên quy tắc snapshot vẫn đúng). Ví dụ: scan A bắt đầu trước, scan B đọc revision mới hơn và xong trước; khi A xong sau, A được lưu vào lịch sử, B vẫn là assessment hiện hành (C34).

### Reassessment độc lập với quét file (ADR-021 rev. 2)
Nội dung file không đổi vẫn có thể cần tính lại assessment. Trigger reassessment (mỗi trigger tạo một assessment task, dedup theo `(project_id, trigger, trigger_ref)`):
- `snapshot`: snapshot mới có hash scope khác snapshot trước.
- `baseline_changed`: owner approve baseline revision mới (trigger_ref = baseline_revision).
- `evidence_ttl_expired`: một evidence đang dùng trong verdict hết TTL theo D-03 (trigger_ref = evidence_id + expiry).
- `model_recovered`: assessment hiện hành có `analysis_status=model_failed` và model profile hoạt động lại (trigger_ref = assessment_id bị lỗi).
- `manual`: reviewer yêu cầu.
Mỗi assessment task nhận `basis_seq` tăng dần theo project **tại thời điểm tạo task**, và chốt input tại thời điểm đó: snapshot_id mới nhất, baseline_revision hiện hành, policy/TTL hiện hành. Con trỏ `project_current_assessment` chỉ tiến khi `basis_seq` lớn hơn (CAS). Hệ quả: cùng `snapshot_seq` nhưng baseline đổi khi assessment cũ đang chạy → assessment cũ (basis_seq nhỏ hơn, baseline cũ) hoàn tất sau vẫn chỉ vào lịch sử; HUD hiển thị kết quả theo baseline mới và trạng thái "đang đánh giá lại theo baseline r2" cho đến khi task mới xong. "Bỏ qua scope không đổi" chỉ áp cho trigger `snapshot`. Test C39.
