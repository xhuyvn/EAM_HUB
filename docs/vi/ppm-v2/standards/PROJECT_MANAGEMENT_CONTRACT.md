> Translated from ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md @ fb857f4

# Project Management Contract 2.1 (tương thích 2.0)

## 1. Tính chất
MUST = yêu cầu bắt buộc của contract này; SHOULD = khuyến nghị có thể ngoại lệ nếu ghi lý do. Đây là tiêu chuẩn nội bộ được đề xuất, không phải tiêu chuẩn ISO mới.
Mọi tài liệu portable dùng `contract_version: '2.0'` và `kind`. JSON Schema Draft 2020-12 kiểm tra cấu trúc; rule engine kiểm tra ngữ nghĩa, quyền và provenance.

## 2. Authority matrix
| Dữ liệu | Nguồn chính thức | Ai được ghi |
|---|---|---|
| Location tuyệt đối, scan schedule, cloud/access | Registry trên host PPM | Owner/trusted admin |
| Danh tính/mục tiêu đề xuất/mapping | `.ppm/project.yaml` | Owner hoặc coding agent được giao |
| Nhật ký công việc | `.ppm/updates/*.yaml` | Người/agent thực hiện trong scope được cấp |
| Reported summary | `.ppm/status.yaml` | Một designated writer; có thể tái dựng từ updates |
| Tiêu chí đề xuất | `.ppm/acceptance.yaml` | Owner/coding agent; phê duyệt trong domain DB |
| Quyết định đề xuất | `.ppm/decisions/*.md` | Agent/người soạn |
| Baseline, assessment, acceptance | Domain DB + audit | Domain service theo quyền |

MUST không coi `owner_ref`, actor hoặc chữ accepted trong file repo là chứng thực danh tính hay approval. Không nhập approved baseline từ repo khi chưa đối chiếu approval event tin cậy.

## 3. Cấu trúc tối thiểu
`.ppm/project.yaml`, `.ppm/status.yaml`, `.ppm/updates/` là profile adoption tối thiểu; thư mục updates có thể rỗng lúc mới tạo. `.ppm/acceptance.yaml` bắt buộc trước verified-delivery assessment; nếu chưa có phải báo coverage gap. `.ppm/decisions/` và notes là bổ sung. `README.md` và `.gitignore` mẫu giúp triển khai.

Paths trong portable mapping MUST là relative POSIX path, không `..`, không absolute hoặc URL. Registry được phép absolute host path; collector vẫn kiểm tra root và realpath. Mọi path đọc thực tế MUST resolve trong trusted root, bao gồm symlink/reparse checks. JSON Schema chỉ chặn một số dạng path, không thay containment check.

## 4. Identity và phiên bản
Project ID MUST ổn định khi đổi tên/path. Khởi tạo khuyến nghị `proj-<UUID>`; mẫu human-readable được phép nhưng inventory phải kiểm tra unique trong namespace. Di chuyển không tạo project mới; clone/worktree là location mới gắn cùng project/repo identity. Copy để tạo sản phẩm mới phải cấp project ID mới.
Monorepo: mỗi project có `.ppm/` dưới subdirectory riêng; registry chỉ định metadata_root và scan_scope. Một project nhiều repo: metadata root chính, location_ref trong artifact mapping giải quyết repo phụ. Artifact path không được chui ra root bằng `../`.
Contract major lạ: quarantine unsupported_version. Migration MUST giữ file gốc/backup và không đổi ID đã đăng ký. Chuyển v1 `schema_version: 1` phải qua explicit migration, không tự đoán.

## 5. Project/status
Project khai báo objectives dạng draft: id, statement, metric nullable; artifact mapping bắt buộc có architecture/blueprint/implementation_plan với path hoặc null. Thiếu tài liệu phải giữ null và ghi gap, không tạo nội dung giả.
Status chỉ ghi lifecycle_reported, current_milestone, reported_progress_percent nullable, blockers và next_actions. `reporting_revision` là version của summary, không phải baseline revision. Status cũ được giữ theo snapshot; không xóa lịch sử update.
Reported progress thiếu baseline MUST để null. 0 chỉ dùng khi người báo cáo thực sự muốn báo 0, không đại diện unknown. Project delivery và operational status tách biệt.

## 6. Session update
Mỗi update MUST có ID unique và filename tương ứng, project_id, recorded_at có timezone, actor string tự khai, session_id, summary, base_revision/result_revision nullable, working_tree_state, completed/in_progress/checks/blockers/next_actions.
Work item gồm item_ref nullable và description. Check gồm name, result (`passed`, `failed`, `not_run`, `inconclusive`), evidence_path nullable, tested_revision nullable, environment nullable. Dữ liệu này vẫn là claim của producer; collector thu riêng hash/report metadata rồi verifier xác nhận.
Check `passed` SHOULD có artifact report; nếu không có thì dùng evidence_path=null và đánh dấu unsupported claim trong assessment. Không ghi passed khi chưa chạy. Evidence không khớp code/criteria không đủ cho verified.

Per-session update là append-only về ngữ nghĩa. Sửa lỗi bằng file mới có `supersedes_update_id`, lý do trong summary; giữ file cũ. Cùng ID/cùng bytes => duplicate no-op; cùng ID/khác bytes => conflict, không last-write-wins. Update đến muộn vẫn xử lý; timestamp không phải ingestion cursor.

Producer SHOULD tạo ID UUID để tránh collision, ghi ra file tạm rồi atomic rename trong cùng filesystem. Temp suffix `.tmp` không ingest; file chưa parse được retry có giới hạn. Không commit/push chỉ để đáp ứng contract nếu tác vụ không yêu cầu.

## 7. Notes và quyết định
Ghi chú tự do trong `.ppm/notes/` hoặc mapping `notes` có thể là Markdown. Nó giúp giữ ngữ cảnh nhưng không tự đổi trạng thái, scope hoặc acceptance. Phân biệt loại note: idea, blocker, question, observation. Muốn báo tiến độ nên tạo structured update.
Decision note phải có scope, alternatives, rationale, impact, evidence_refs và state proposed. Chỉ quyết định được đối chiếu authority/approval event mới có hiệu lực trong DB.

## 8. Source revision và self-reference
Không bắt update chứa hash commit bao gồm chính update đó: tạo commit hash tự tham chiếu là bất khả thi. `result_revision` có thể trỏ code commit trước documentation commit; hoặc null nếu chưa commit. Collector tự thu HEAD, tree/diff manifest và update hash độc lập. Không sửa code chỉ để khớp revision trong nhật ký.

## 9. Scan contract
MUST hỗ trợ initial/manual/interval/full_reconciliation; watcher là optimization. Checkpoint chỉ tiến sau transaction ingest thành công. Định danh ingestion theo project+location+artifact path+content hash; domain update dedup thêm project+update_id.
Scan status: queued/running/succeeded/partial/failed/cancelled. Assessment validity: current/stale/unknown/conflicted. File parse fail có thể scan partial nhưng không được đánh dấu toàn bộ project fresh.
Đường dẫn mất, quyền bị thu hồi, máy ngủ và cloud lỗi không tạo tiến độ 0 hoặc kết luận no_change. `last_successful_scan_at`, `last_scan_attempt_at`, `last_assessment_at`, source_as_of MUST phân biệt.
Full reconciliation phát hiện delete/rename và watcher event bị mất. Journal không xóa bản ghi lịch sử khi file bị xóa; tạo tombstone source và đánh giá tác động.

## 10. Nhiều writer
Mỗi agent ghi update riêng. Status chỉ designated writer cập nhật với expected content hash hoặc CAS. Không có CAS thì để PPM tái dựng và báo status stale thay vì overwrite mù quáng. Khi coding agents có isolated worktree, registry phải chọn integration branch/snapshot để aggregate; không cộng mọi worktree thành tiến độ accepted.

## 11. Git và dữ liệu nhạy cảm
Metadata/updates/criteria SHOULD commit để truy vết, sau kiểm tra dữ liệu nhạy cảm. Local cache, host paths, tokens và raw output chứa secret MUST không commit. `.gitignore` không phải security control; kiểm tra cả tracked files. Project repo không tự bật cloud, cron, MCP hoặc arbitrary shell.

## 12. Definition of session done
- Ghi rõ done/in-progress, checks và blockers thực tế.
- Tạo một update mới; update summary nếu là designated writer.
- Không tuyên bố accepted/deployed/benefit realized từ code/test pass.
- Không che failure hoặc dựng lịch sử.
- Bàn giao next actions và unresolved decisions.

## 13. Ví dụ diễn giải
Update báo M2 done, test pass nhưng evidence_path null: reported done, verification gap.
Test report fail ở current implementation: contradicted, cần xử lý.
Owner nói “đã dùng ổn”: ghi observation/acceptance proposal, xác định scope trước formal acceptance.
Project đóng, sản phẩm chạy: lifecycle closed và operation running cùng tồn tại.

## 14. Contract 2.1 (06/11/2026) — bổ sung tương thích ngược
Bổ sung sau review `ppm-v2/docs/ARCHITECTURE_REVIEW.md`. Tài liệu 2.0 vẫn hợp lệ, không cần migration.

### 14.1 Version
`contract_version` nhận `'2.0'` hoặc `'2.1'`. Trường 2.1 chỉ được dùng khi file khai `'2.1'`; file khai 2.0 mà dùng trường 2.1 bị từ chối (validator). Reader MUST đọc được cả 2.0 và 2.1. Minor cao hơn (2.2+) hoặc major khác → quarantine `unsupported_version` và gap report read-only.

### 14.2 Trường mới (đều optional)
- `acceptance.yaml` → `criteria[].verification_refs[]`: `{kind: test_id | probe_id | artifact_path | manual_review, ref}`. Đây là mapping đề xuất; chỉ có hiệu lực khi owner duyệt thành baseline trong PPM DB. Reducer dùng mapping baseline để biết test nào bao phủ criterion; thiếu mapping → `coverage_not_mapped`, không đoán.
- `updates/*.yaml` → `completed[]/in_progress[]/checks[].criterion_ids[]`, `checks[].test_ids[]`: liên kết claim với criterion để không phải dùng LLM đoán. ID phải tồn tại trong `acceptance.yaml` cùng metadata root.
- `project.yaml` → relation `uses_component`: project này dùng deliverable của project khác (shared component). Không đưa criteria của component vào `acceptance.yaml` của consumer.

### 14.3 Lớp tin cậy của bằng chứng (MUST)
| Trust class | Nguồn | Có thể verify criterion? |
|---|---|---|
| `verifier_observed` | Verifier sandbox của PPM/platform chạy lại test/probe được map tại đúng revision | Có |
| `ci_attested` | Kết quả CI có provenance kiểm được (định danh pipeline, revision, chữ ký/attestation) do owner cấu hình là tin cậy | Có |
| `reported_artifact` | File report nằm trong repository (JUnit/JSON/log) do coding agent hoặc người tạo, kể cả khi update trỏ `evidence_path` tới nó | **Không**; chỉ là claim có kèm file |
| `none` | Không có | Không |
Lý do: producer viết cả code, update lẫn file report trong cùng repo, nên file report trong repo không độc lập với claim. FAIL trong `reported_artifact` vẫn được dùng như tín hiệu phản bác (thận trọng), PASS thì không.

### 14.4 Danh tính project khi copy/clone
Cùng `project_id` xuất hiện ở hai source khác `repo_identity` (copy repo để làm sản phẩm mới mà quên đổi ID) → `identity_conflict`, quarantine location mới, không gộp updates; owner chọn: cấp ID mới hoặc xác nhận là worktree/clone của cùng project. Clone/worktree đã xác nhận vẫn không cộng tiến độ riêng (§10).

### 14.5 Baseline từ draft
`acceptance.yaml` luôn là `draft`. Owner tạo baseline qua PPM API từ snapshot hash của `acceptance.yaml` (+ chỉnh sửa nếu cần); baseline lưu revision, approver, thời điểm và mapping verification. Trước khi có baseline: assessment hiển thị trạng thái từng criterion là *provisional*, `progress.basis=none`, không có acceptance readiness. Sau đó, `acceptance.yaml` khác baseline → tín hiệu `baseline_drift` và gợi ý change proposal, không tự đổi baseline.

### 14.6 Waiver
Waiver chỉ tồn tại trong PPM DB (approver, lý do, scope, expiry), không nằm trong repo. Criterion được waive hiển thị riêng, ra khỏi mẫu số tiến độ và được tính là thỏa cho acceptance readiness đến khi waiver hết hạn.

### 14.7 Registry và chế độ platform
`templates/registry.example.yaml` là cấu hình cho chế độ standalone và là **định dạng import một lần** khi chuyển sang platform. Ở chế độ platform, nguồn sự thật là `location-registration` 1.1 (`contracts/location-registration.schema.json`): import tạo record `proposed`, owner bổ sung `source_id`, `host_id`, `artifact_aliases`, `classification`, `egress_policy_ref` và approve. `access_policy.cloud_processing: approved_only` không tự chuyển thành quyền egress; owner phải chọn `egress_policy_ref`.

### 14.8 Kiểm tra bổ sung của validator
Ngoài schema: criterion ID trùng; supersession trỏ tới update không tồn tại hoặc tạo vòng; update tham chiếu criterion không tồn tại; nhiều `project_id` trong một metadata root; `recorded_at/updated_at` lệch tương lai > 24 giờ; trường 2.1 trong file 2.0. Chạy `python tools/run_negative_cases.py` để kiểm các case trong `evals/negative_cases.json`.
