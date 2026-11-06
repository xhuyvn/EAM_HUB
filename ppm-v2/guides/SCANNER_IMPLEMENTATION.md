# Thiết kế scanner cho coder

## 1. Registry contract
Template `registry.example.yaml` là trusted host configuration do owner cài, không đọc từ repo để cấp quyền. Config này là draft cho đến xác thực root/owner. Mỗi project nhiều locations; `metadata_location_id` chọn metadata chính. `scan_scope` tương đối với root. `metadata_root` tương đối root và chứa `.ppm/`.

## 2. Scheduling
On-start chạy catch-up cho locations overdue. Watcher debounce 5s và coalesce theo location; interval 900s, full 86400s trong mẫu. Scheduler claim lease bằng DB; lease expires thì run khác có thể phục hồi. Giới hạn một active scan/location, không giữ lock suốt thời gian model call. Chưa có app không có lịch thật; đây là đặc tả để triển khai.

## 3. Algorithm
1. Authorize registered location; normalize/canonicalize path và verify containment.
2. Lấy scan lease, ghi attempt; load checkpoint.
3. Chốt HEAD/dirty manifest hoặc non-Git manifest; enumerate includes/excludes bằng quy tắc đã document.
4. Hash/snapshot file, stat trước/sau; đọc giới hạn size. Với dirty tree thay đổi, retry bounded rồi mixed_snapshot.
5. Parse safe YAML (không custom tags), validate schema/version/semantic links. Retry incomplete; quarantine duplicate-key YAML, unsupported_version, invalid_paths.
6. Ingest journal theo ID/hash, xử lý supersedes và collision; luôn giữ raw provenance.
7. Reconcile claims/status/criteria; không chạy command nào từ docs hoặc YAML.
8. Transaction ghi source snapshots, claims, tombstones, outbox và checkpoint. Nếu partial, checkpoint per-artifact kèm unresolved set; đừng bỏ qua lỗi ở lượt sau.
9. Analysis job nhận snapshot IDs, tạo assessment qua deterministic rules. Scan success vẫn có thể assessment pending/failed.
10. Report delta và freshness; release lease.

## 4. API bổ sung
POST /locations; PATCH /locations/{id} có expected_revision.
POST /scans {project_id, location_ids, mode}; GET /scans/{id}.
GET /projects/{id}/freshness; GET /projects/{id}/updates.
GET /projects/{id}/conflicts; POST /conflicts/{id}/resolve theo owner policy.
POST /onboarding-proposals (không phải auto approval).

## 5. Domain tables bổ sung
RegisteredLocation, ScanPolicy, ScanRun, ScanCheckpoint, ArtifactVersion, ReportedUpdate, UpdateConflict, Supersession, OnboardingProposal. Tách observed_at/reported_at/ingested_at. Không dùng modified time làm cursor duy nhất. Unique(project_id, update_id) và content hash collision handling.

## 6. Hiển thị tối thiểu
Location/reachability; scan attempt/success; assessment as_of; reported vs verified progress; missing criteria; update chưa reconcile; evidence stale; blockers; proposed relations. Khi worker tắt, UI phải nói monitoring offline.

## 7. Chống vòng lặp
Cache/runtime ngoài repository. Không để collector viết update khi chính nó scan; assessment trong DB. Nếu export report vào repo, đánh dấu generated và loại khỏi trigger/claim input để không tự xác nhận chính output của mình.

## 8. Gates
Duplicate event không tạo domain event trùng; partial write giữ checkpoint; delete giữ history; sleeping host catch-up; repo YAML không bật cloud; status conflict không mất journal; dirty tree không thành verified; owner baseline change invalidates affected assessment; mọi external side effect đi Action Service như blueprint.

## 9. Chế độ platform (06/11/2026)
Khi chạy trong Enterprise Agent Platform, scanner là P2a-WP2 (collector) + P2a-WP5 (scheduler) + P2a-WP6 (orchestrator) + P2a-WP9 (verifier); registry YAML được import thành `location-registration` 1.1 theo contract §14.7. Bảng mapping import:
| registry.example.yaml | location-registration 1.1 |
|---|---|
| `projects[].project_id` | `project_id` |
| `locations[].location_id` | `location_id` |
| `locations[].path` | `root_path` (connector canonical hóa) |
| `metadata_root`, `scan_scope` | giữ nguyên |
| `scan_policy.max_file_bytes`, `deny_file_names` | `max_file_bytes`, `deny_globs` |
| `scan_policy.*` interval/debounce/watch | `ppm_scan_schedules` (P2a-WP5) |
| `access_policy.cloud_processing` | không map tự động; owner chọn `egress_policy_ref` |
| (không có) | `source_id`, `host_id`, `connector_id`, `artifact_aliases`, `classification`, `identity`, `approved_by_ref` do owner bổ sung khi approve |
Bằng chứng test chỉ verify khi do verifier chạy lại tại đúng revision hoặc CI có attestation (contract §14.3).
