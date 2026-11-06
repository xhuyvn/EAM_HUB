> Translated from ppm-v2/docs/ARCHITECTURE_REVIEW.md @ fb857f4

# Architecture Review — PPM v2 Agent (Project–Program–Portfolio Management)

Ngày review: 06/11/2026. Phạm vi: gói `ppm-v2/` (blueprint, Project Management Contract, schema, templates, prompts, scanner guide, validator) và các điểm nối với Enterprise Agent Platform 1.1. Starting revision: `0f91f2d` (repo `EAM_HUB`), cùng working tree đã sửa ở vòng review platform. Không dùng dữ liệu project thật, không gọi provider tính phí.

Review này không tạo blueprint cạnh tranh. Khi chạy trong platform, `MASTER_BLUEPRINT.md` và `docs/04_PPM_INTEGRATION.md` là nguồn chính thức cho runtime; PPM v2 là nguồn chính thức cho ngữ nghĩa domain (contract `.ppm`, criteria, claim, assessment). Các điểm blueprint PPM bị thay thế được liệt kê ở §19 của `AI_PPM_Agent_Blueprint_v2.0.md`.

## 0. Kết luận và readiness

| Phạm vi | Verdict | Lý do |
|---|---|---|
| Bộ áp dụng standalone (contract 2.1, templates, prompts 01–07, validator) | **Sẵn sàng pilot ngay trên repo của owner — chỉ ở mức claim.** | Contract, templates và validator đã được kiểm lại (20 case pass). Không có verifier nên mọi kết luận là reported/manual review, không phải verified. |
| PPM agent tự động — Local MVP | **Thiết kế đủ để triển khai; chưa có code.** | Sửa xong các lỗ hổng gây false verification (PPM-01..03). Phụ thuộc platform P0–P2a và PPM-M0 (reducer core). Verifier repo thật cần D-07. |
| Shared pilot | **Chưa sẵn sàng.** | Program/portfolio/benefit chưa có contract (PPM-14); baseline/waiver/acceptance API thuộc P2b; tổng portfolio có quyền ẩn chưa test (C43). |
| Production | **Chưa sẵn sàng.** | Không runtime, không eval trên dữ liệu thật, chưa có chuyên gia PPM duyệt domain model. |

Thay đổi quan trọng nhất:
1. **Report test trong repo không còn được coi là bằng chứng** (PPM-01, platform ADR-023). Chỉ verifier chạy lại test được map tại đúng revision, hoặc CI có attestation, mới verify criterion. Lý do: producer viết được cả code, update và file JUnit trong cùng repo.
2. **Contract 2.1 tương thích 2.0**: criteria có `verification_refs` (test/probe được map), update/check có `criterion_ids`/`test_ids`. Lý do: không có liên kết này thì reducer chỉ có thể nhờ LLM đoán claim thuộc criterion nào và test nào bao phủ nó.
3. **Validator bắt thêm lỗi ngữ nghĩa** (criterion trùng, supersession vòng/treo, tham chiếu criterion không tồn tại, timestamp tương lai, trường 2.1 trong file 2.0).
4. **Danh tính project khi copy repo, waiver, baseline từ draft, registry → location-registration** được định nghĩa rõ.

## 1. Những gì tồn tại

| Thành phần | Đường dẫn | Implemented | Tested |
|---|---|---|---|
| Blueprint | `ppm-v2/AI_PPM_Agent_Blueprint_v2.0.md` | Thiết kế | — |
| Contract normative | `ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md` | Thiết kế | — |
| Schema | `ppm-v2/schemas/contract.schema.json` | Có | Templates + 20 case |
| Templates `.ppm/`, registry | `ppm-v2/templates/` | Có | Validator |
| Prompts 01–08 | `ppm-v2/prompts/` | Văn bản | Chưa thử với coding agent |
| Validator, runner | `ppm-v2/tools/validate_contract.py`, `run_negative_cases.py` | Có | Đã chạy (§9) |
| Collector/scanner, reducer, verifier, UI | `guides/SCANNER_IMPLEMENTATION.md`, platform P2a | Không | Không |
| Program/portfolio/benefit | Blueprint §3, §11 | Không có contract | Không |

Ký hiệu bằng chứng: **[V]** chạy lệnh/spike; **[D]** suy ra từ tài liệu; **[A]** giả định cần spike.

## 2. Workflow traces

### A. Tiếp nhận dự án đang làm (prompt 01, standalone)
| Bước | Caller | Authorization | Source of truth | Ghi gì | Lỗi | Owner thấy gì |
|---|---|---|---|---|---|---|
| Inventory | Coding agent của owner | Quyền agent trong repo (ngoài PPM) | Repo | Không ghi | Agent chạy lệnh từ docs → prompt cấm, không có enforcement | Báo cáo của agent |
| Tạo `.ppm/` | Coding agent | — | Repo (portable metadata) | project/status/acceptance(draft)/update đầu | Copy template giữ `proj-example` → ID trùng giữa nhiều repo (PPM-06) | Diff `.ppm/` |
| Registry | Owner | Owner | Host registry (standalone) / `location-registration` (platform) | Path, scan policy | Registry thiếu trường platform cần (PPM-07) | Registry file |
| Validate | Owner/agent | — | Validator | — | Trước review: lỗi ngữ nghĩa lọt (PPM-05) | PASS/FAIL |

### B. Vòng phiên làm việc (prompt 04/05)
Coding agent ghi `updates/<UUID>.yaml` — claim. Sửa sai bằng update mới `supersedes_update_id`. Trước review: không liên kết claim với criterion (PPM-03), supersession có thể vòng/treo (PPM-05, [V]). Sau review: `criterion_ids`, `test_ids`; validator chặn vòng/treo/ID lạ.

### C. Claim → assessment (platform mode)
Collector snapshot → ingest claim (update, note, status) → verifier chạy test được map tại `assessed_revision` → reducer deterministic → assessment hiện hành theo `basis_seq` → reviewer accept. Bảng quyết định từng criterion:

| Điều kiện | Kết quả |
|---|---|
| Verifier/CI PASS các test được map ở đúng revision, không FAIL | `verified` |
| FAIL (verifier, CI hoặc report trong repo) ở đúng revision | `contradicted` |
| Chỉ có claim (update/note) | `unverified / claim_only` |
| Chỉ có file report trong repo (dù PASS) | `unverified / reported_artifact_only` |
| Evidence ở revision cũ / hết TTL | `stale` |
| Test PASS nhưng không thuộc mapping | `unverified / coverage_not_mapped` |
| Waiver đã duyệt còn hạn | `waived` (ra khỏi mẫu số) |
| Chưa có baseline | provisional, `progress.basis=none` |

Người chấp nhận: reviewer có role (IdP), qua acceptance API, trên assessment `current` đúng baseline. Path không khả dụng → scan failed/partial, assessment cũ stale theo TTL. Note trùng → dedup hash. Baseline đổi → reassessment.

### D. Thay đổi scope (prompt 07)
Agent tạo decision note `proposed` → owner duyệt baseline revision mới trong DB → trigger `baseline_changed` → assessment cũ thành lịch sử, acceptance cũ `superseded`. `acceptance.yaml` khác baseline → `baseline_drift` (contract §14.5).

### E. Program/portfolio rollup
Deliverable có một owner project; consumer dùng `uses_component`/`dependency_refs`; rollup theo deliverable distinct. Người xem không có quyền một project → tổng chỉ gồm project được phép, nhãn "partial", không lộ số lượng/tên/trọng số bị ẩn (PPM-10, C43). Chưa có contract program/portfolio/benefit (PPM-14).

### F. Review thủ công standalone (prompt 06)
Coding agent đọc repo và trả bảng reported/verification. Không có verifier nên cột verification chỉ được ghi "manual — unverified"; agent không được tự chạy test để "xác minh" (prompt cấm). Rủi ro còn lại: agent có quyền shell đọc note chứa prompt injection; khuyến nghị chạy prompt 06 với chế độ read-only/không shell.

## 3. Threat model riêng PPM
| Threat | Enforcement point | Test | Expected | Residual |
|---|---|---|---|---|
| Report test giả do producer tự ghi | Reducer: chỉ `verifier_observed`/`ci_attested` verify | C40; negative "repo-written test report used to verify" | Không verified | CI attestation cấu hình sai |
| Test code độc hại khi verifier chạy lại | Verifier sandbox: argv allowlist, không network, không secrets, timeout, workspace tạm | Fixture test cố gắng mở socket/đọc env | Bị chặn, kết quả inconclusive | Escape sandbox — D-07 chọn cơ chế |
| Prompt injection trong note tới analysis model | Reducer deterministic; model chỉ trích claim | C22 | Không đổi verdict | Claim sai lệch vẫn hiển thị (cột reported) |
| Prompt injection tới coding agent standalone (prompt 06) | Chỉ hướng dẫn trong prompt | — | — | **Cao**: phụ thuộc quyền của agent; dùng chế độ read-only |
| Copy repo giữ project_id | Ingest: identity_conflict theo repo_identity | C42 | Quarantine | Owner xác nhận sai |
| Repo tự cấp quyền/cloud | Schema (`access_policy` const), registry ngoài repo | Negative "repo grants cloud", "registry enables command execution" | Từ chối | — |
| Tổng portfolio lộ project bị ẩn | Rollup lọc theo quyền trước khi tính | C43 | Partial, không lộ | Suy luận từ thay đổi tổng theo thời gian |
| Secrets trong `.ppm` commit | deny_globs, secret scan trước ingest | SC-04 | Không ingest | Detector không đầy đủ |

## 4. Findings
| ID | Sev | Loại | Tóm tắt | Trạng thái |
|---|---|---|---|---|
| PPM-01 | Critical | [D] | Report test trong repo (`evidence_path`) được coi là bằng chứng observed; blueprint §5 ví dụ evidence `source_kind: test_report`, `path: reports/integration.json`, `trust_class: observed`; platform không có verifier WP | Fixed design: contract §14.3, platform ADR-023, `evidence_trust`, P2a-WP9; validator |
| PPM-02 | High | [V] | Criteria không có mapping test/probe → không xác định được phạm vi test | Fixed: `verification_refs` (2.1) |
| PPM-03 | High | [V] | Update/check không liên kết criterion → phải để LLM đoán | Fixed: `criterion_ids`, `test_ids` (2.1) |
| PPM-04 | Medium | [D] | Waiver có trong blueprint §6 nhưng assessment không biểu diễn được | Fixed: ADR-024, `waived` + `waiver_ref` |
| PPM-05 | High | [V] | Validator PASS với criterion trùng, supersession vòng/treo, timestamp năm 2099 | Fixed: validator + 9 case mới |
| PPM-06 | High | [V] | Repo copy giữ nguyên `project_id` → hai sản phẩm bị gộp | Fixed design: identity_conflict (contract §14.4), C42 |
| PPM-07 | Medium | [V] | Registry YAML thiếu 17 trường so với `location-registration` 1.1; `cloud_processing` không có tương đương | Fixed: registry là định dạng import, bảng mapping (contract §14.7, scanner guide §9) |
| PPM-08 | Medium | [D] | Luồng tạo baseline từ `acceptance.yaml` draft chưa định nghĩa; D1 phụ thuộc vào nó | Fixed design: contract §14.5, C44 |
| PPM-09 | Medium | [D] | Blueprint PPM §9 chọn LangGraph làm orchestration chính, §10 executor riêng, §18 MVP đến M4 — mâu thuẫn platform ADR-011/017 và phạm vi D1 | Fixed: blueprint §19 bảng thay thế |
| PPM-10 | Medium | [D] | Tình huống eval #19 (tổng portfolio khi thiếu quyền) không có quy tắc | Fixed design: C43 |
| PPM-11 | Medium | [D] | 20 tình huống eval bắt buộc (blueprint §13) chưa map sang case chạy được; #3, #6, #9, #10, #16 chưa có case | Fixed plan: `ppm-v2/evals/SCENARIO_MATRIX.md`, PPM-M0-WP1 |
| PPM-12 | Low | [D] | `status.yaml` "designated writer" không xác định được trong file | Accepted: status là summary dẫn xuất, không dùng cho verdict |
| PPM-13 | Low | [D] | Prompts không hướng dẫn liên kết claim với criterion, không nói report tự ghi chỉ là claim | Fixed: prompt 03, 05 |
| PPM-14 | Medium | [D] | Program/portfolio/benefit/capacity chưa có contract; chỉ có cấp project | Open: PPM-C1 trước P4-WP1 |
| PPM-15 | Low | [D] | Notes không có cấu trúc (loại/thời điểm/nguồn) | Accepted cho 2.1; xem lại ở 2.2 sau khi có dữ liệu thật |

### PPM-01 — Report trong repo được coi là bằng chứng (Critical)
- Evidence: contract §6 "collector thu riêng hash/report metadata rồi verifier xác nhận" (không nói verifier làm gì); blueprint §5 ví dụ evidence lấy `reports/integration.json` trong repo với `trust_class: observed`; platform docs/04 vòng 2 viết "evidence do collector/verifier thu (`trust_class=observed`)"; platform plan không có WP verifier.
- Failure: coding agent (hoặc người) ghi `reports/ac003-junit.xml` PASS và update trỏ tới nó; collector "quan sát" file khớp revision → criterion verified → reviewer thấy "verified" và nghiệm thu một tính năng chưa chạy.
- Correction: bốn trust class; chỉ verifier/CI-attested verify; file report trong repo = claim kèm file (FAIL vẫn là tín hiệu phản bác). P2a-WP9 Verifier sandbox; contract `ppm-output` `evidence_trust`; fixture AC-003 thể hiện đường giả mạo.
- Verification: negative "repo-written test report used to verify (forged-report path)"; runtime C40.

### PPM-05 — Validator lọt lỗi ngữ nghĩa (High, [V])
- Evidence: spike trên bản sao templates (thêm criterion trùng, `upd-b`↔`upd-c` supersede lẫn nhau, `upd-d` supersede `upd-missing`, `recorded_at: 2099-01-01`) → `validate_contract.py`: `PASS 8 records; structural checks only`.
- Failure: chuỗi correction vòng làm reducer lặp vô hạn hoặc chọn sai bản hiện hành; criterion trùng làm sai mẫu số; timestamp tương lai làm update "mới nhất" mãi mãi.
- Correction: `semantic_doc_errors`, `semantic_set_errors`; runner chạy case cấp thư mục.
- Verification: `run_negative_cases.py` → `PASS 5 templates, 20 cases`.

### PPM-06 — Copy repo giữ project_id (High, [V])
- Evidence: copy template sang thư mục khác, đổi tên sản phẩm; `project_id` vẫn `proj-example`, validator từng thư mục đều PASS; contract §4 chỉ khuyến cáo "phải cấp project ID mới".
- Failure: hai sản phẩm khác nhau gộp updates và tiến độ dưới một project.
- Correction: identity_conflict khi cùng project_id ở source khác `repo_identity`; quarantine; owner quyết định.
- Verification: C42 (P2a-WP2).

### PPM-07 — Registry khác contract platform (Medium, [V])
- Evidence: so trường `templates/registry.example.yaml` với `contracts/location-registration.schema.json`: thiếu `source_id, host_id, connector_id, artifact_aliases, identity, classification, egress_policy_ref, follow_symlinks, allow_hardlinked_files, approved_by_ref, …` (17 trường).
- Failure: hai nơi cấu hình location, owner sửa một nơi, nơi kia lệch; `cloud_processing: approved_only` bị hiểu thành quyền egress.
- Correction: registry là định dạng standalone/import; bảng mapping; egress không map tự động.

## 5. Lựa chọn thiết kế
| Quyết định | Giữ | Phương án khác | Trade-off | Đảo ngược | Bằng chứng làm đổi |
|---|---|---|---|---|---|
| Contract 2.1 additive | Thêm trường optional, đọc cả 2.0 | Nhảy 3.0 bắt buộc migration | Không phá repo đã áp dụng; validator cần kiểm theo version | Dễ | Nếu cần đổi nghĩa trường hiện có |
| Verifier chạy lại test | Sandbox local | Tin CI attestation duy nhất | Local có chi phí chạy test; CI cần pipeline tin cậy | Hai cách cùng tồn tại | Repo không chạy được test local |
| Mapping test do owner duyệt | `verification_refs` trong baseline | LLM tự map test↔criterion | Tốn công owner; không đoán | Có thể để LLM đề xuất, owner duyệt | Eval cho thấy đề xuất LLM đủ chính xác để chỉ cần duyệt |
| Reducer deterministic | Rules thuần Python (PPM-M0) | LLM judge | Ít linh hoạt; kiểm chứng được | — | Không |

## 6. Claims bên ngoài
Không thêm claim vendor mới; dùng lại bảng §8 của review platform. ISO/TC 258 chỉ dùng làm khung khái niệm (blueprint §2); không tuyên bố tuân thủ.

## 7. Thay đổi đã áp dụng
- `ppm-v2/schemas/contract.schema.json` → 2.1 (đọc 2.0): `verification_refs`, `criterion_ids`, `test_ids`, `uses_component`.
- `ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md` §14; blueprint §19; `guides/SCANNER_IMPLEMENTATION.md` §9; prompts 01–08 (tham chiếu 2.1, liên kết criterion, report tự ghi là claim; prompt 08 trỏ prompt mới).
- `ppm-v2/tools/validate_contract.py` (semantic checks), `ppm-v2/tools/run_negative_cases.py` (case cấp thư mục), `ppm-v2/evals/negative_cases.json` (+9), templates → 2.1, `ppm-v2/evals/SCENARIO_MATRIX.md` (mới).
- Platform: `contracts/ppm-output.schema.json` (`evidence_trust`, `waived`, `waiver_ref`, reason codes, `previous_status` nhận `waived`), `examples/ppm-output.json`, `tools/validate_package.py` (+3 negative, 1 positive waiver), `docs/01` (ADR-023/024), `docs/04`, `evals/CONFORMANCE_CASES.md` (C40–C44), `docs/IMPLEMENTATION_PLAN.md` (plan 1.4: P2a-WP9, effort), `docs/ARCHITECTURE_REVIEW.md` (ARCH-34/35, D-07).

## 8. Validation
| Lệnh | Kết quả thực tế |
|---|---|
| Spike: `validate_contract.py` trên metadata root có criterion trùng, vòng supersede, supersede treo, `recorded_at` 2099 (trước sửa) | `PASS 8 records; structural checks only` (lỗi lọt) |
| Spike: copy template giữ `project_id` | Cả hai thư mục PASS, cùng `proj-example` |
| Spike: so registry với `location-registration` | Thiếu 17 trường |
| `python tools/run_negative_cases.py` (sau sửa) | `PASS 5 templates, 20 cases` |
| `python tools/validate_contract.py templates/project/.ppm --schema schemas/contract.schema.json` | `PASS 4 records; structural and contract-semantic checks only` |
| `python tools/validate_contract.py <acceptance.yaml 2.0 gốc tại 0f91f2d>` | `PASS 1 records` (tương thích 2.0) |
| `python tools/validate_package.py` (repo root) | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` |
Chưa chạy: prompts với coding agent thật, collector/verifier/reducer (chưa có code), eval 20 tình huống, mọi C40–C44 runtime.

## 9. Quyết định chưa chốt (owner)
- D-07 (chung với platform): cơ chế sandbox của verifier trên repo thật.
- PPM-D1: chính sách CI attestation nào được coi là `ci_attested` (pipeline, chữ ký, ai cấu hình).
- PPM-D2: ai được duyệt waiver và thời hạn tối đa.
- PPM-D3: TTL evidence theo loại (trùng D-03 của platform).
- PPM-D4: phạm vi program/portfolio cần cho shared pilot (benefit register, capacity, scenarios) trước khi viết PPM-C1.
