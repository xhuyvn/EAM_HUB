> Translated from docs/02_RUNTIME_AND_SECURITY.md @ a36d44e

# Runtime, persistence và kiểm soát hành động
## Request admission
Authenticate → resolve tenant/principal → authorize task type and resource refs → validate input schema → resolve approved agent release and binding → reserve task-tree budget → create task/run + dispatch intent/outbox transaction → worker claim lease → adapter submit → persist external execution ID → events/results → domain verification.
Public request body không nhận tenant/user/permission claims. Internal envelopes do gateway xây từ credentials. Scope chỉ được thu hẹp khi delegate. Parent cancellation ngừng task con theo policy và đợi confirmation hoặc reconcile.

## State transitions
Bảng này là nguồn chính thức; `tools/validate_package.py` (ALLOWED) kiểm tra fixtures theo cùng bảng. Mọi chuyển trạng thái được ghi bởi Run Service trong cùng transaction với outbox event `run.state_changed`.

| Run state | Event/command | Next | Điều kiện |
|---|---|---|---|
| queued | dispatch claimed | dispatching | lease + fencing token + capability + budget valid |
| queued | cancel | cancelled | chưa có dispatch intent |
| dispatching | external ID confirmed | running | provider acceptance persisted cùng execution_ref |
| dispatching | provider từ chối rõ ràng (validation/auth) trước khi nhận | failed | response xác định, không có execution_ref |
| dispatching | journal chứng minh submit chưa gửi + cancel | cancelled | dispatch intent ở trạng thái `prepared` |
| dispatching | response ambiguous / ack timeout / worker crash sau khi gửi | unknown | journal `sent`, chưa có execution_ref |
| running | action proposal | waiting_approval | immutable action digest + expiry |
| waiting_approval | authorized decision | running | reauthorize và consume approval |
| running | output validated | succeeded | result schema pass; domain verification queued |
| running | provider failure xác nhận | failed | provider status/terminal event |
| running/waiting_approval | cancel requested | cancelling | adapter certified cancel hoặc operator workflow |
| running/waiting_approval | deadline | timed_out | gửi stop request, tiếp tục track remote outcome |
| cancelling | provider stop confirmed | cancelled | confirmation evidence |
| cancelling | provider hoàn tất trước khi dừng | succeeded/failed | ghi kết quả thật, cancel command → completed với note |
| cancelling | cancel ack lost | unknown | không biết provider đã dừng chưa |
| unknown | verified provider status | running/waiting_approval/succeeded/failed/cancelled | quan sát authoritative theo operation_key/execution_ref |

Terminal states (succeeded, failed, cancelled, timed_out) không hồi sinh. Không có chuyển `unknown → queued`: retry một run unknown là tạo attempt mới, chỉ được phép sau khi reconciliation kết luận remote không tồn tại/không thực hiện side effect, hoặc operator chấp nhận rủi ro bằng decision có audit. Deadline khi run đang `unknown`/`cancelling` không chuyển sang timed_out; nó tạo attention item vì platform không thể khẳng định remote đã dừng.

### Dispatch journal và chuỗi lost-ack/restart/cancel
Dispatch journal (bảng `dispatch_intents`) có khóa unique `(binding_id, run_id, operation='submit')` và trạng thái `prepared → sent → acknowledged | ambiguous`. `idempotency_key` gửi cho adapter = sha256(binding_id|run_id|'submit'), không đổi khi worker restart. Lease có `fencing_token` tăng đơn điệu; gateway và result ingestion từ chối token cũ.

1. Worker A claim lease (token 7), ghi intent `prepared`, commit, gọi adapter.submit, ghi `sent` trước khi gửi bytes (hoặc ngay khi gọi CLI).
2. Provider nhận task; acknowledgement bị mất. Worker A crash.
3. Lease hết hạn; worker B claim (token 8). Thấy intent `sent` không có execution_ref → run `unknown`, không gọi submit mới.
4. Worker B gọi `adapter.lookup(operation_key)` hoặc `status` theo idempotency key. Có kết quả → ghi execution_ref, `unknown → running`. Adapter không hỗ trợ lookup → giữ `unknown`, attention item "remote outcome unknown", đề xuất operator kiểm tra thủ công.
5. User bấm cancel trong lúc unknown: command `accepted`, lưu intent; HUD hiển thị "Cancel requested — remote outcome unknown". Sau reconciliation: running → cancelling → cancelled (có evidence), hoặc nếu remote đã xong thì ghi succeeded/failed thật và command completed với note.
6. Kết quả trễ từ worker A (token 7) bị fence từ chối ghi state; payload được lưu làm diagnostic artifact.

Acceptance cho chuỗi này: đúng một external submission ở provider mock (đếm theo operation_key), attempt history giữ nguyên, các event `run.state_changed` theo thứ tự trên, HUD row có `remote_outcome=unknown` trong khoảng 2–4 (conformance C05, C06, C21).

### Hai loại khóa: dispatch key và business-intent key
Theo nguyên tắc idempotent API có client request token (AWS Builders' Library, "Making retries safe with idempotent APIs", truy cập 06/11/2026), mỗi ý định nghiệp vụ cần một định danh do bên gọi cấp và ổn định qua mọi lần retry.

| Khóa | Ai cấp | Công thức | Phạm vi ổn định | Dùng cho |
|---|---|---|---|---|
| `task_intent_key` | Task Service lúc admission | sha256(tenant_id\|task_id) | Mọi attempt của task | Gửi cho provider có client token (ví dụ tên branch/PR marker), để attempt mới không tạo resource thứ hai |
| `idempotency_key` (dispatch) | Run Service | sha256(binding_id\|run_id\|'submit') | Một run; giữ nguyên qua worker restart | Submit/lookup ở adapter |
| `operation_key` (action) | Action Service khi action được đề xuất lần đầu cho task | sha256(tenant_id\|task_id\|intent_key) | Mọi attempt của task | Execution journal; approval consumption |

**`action_slot` do platform giữ, không do agent tự đặt.** Workflow/Action Service cấp slot (hoặc xác thực slot agent đề xuất theo danh sách bước của task type) và **lưu bền vào `task_intents` trước lần gọi tool đầu tiên**, cùng transaction với execution intent. Khi retry/resume, Action Service phục hồi slot từ `task_intents` theo (task_id, tool_id, bước workflow), không dùng slot mới do agent đề xuất. Agent đề xuất slot mới (`create_pr#2`) cho một bước đã có slot → bị ánh xạ về slot đã lưu nếu cùng bước, hoặc bị từ chối `ACTION_SLOT_UNDECLARED` nếu task type không cho phép bước thứ hai; tạo PR thứ hai hợp lệ chỉ qua intent mới do người dùng xác nhận (audit). Biến thể C29b: sau restart agent đề xuất `create_pr#2` → số PR thực tạo vẫn là 1.

Quy tắc:
- Retry tạo `run_id` mới nhưng là **cùng ý định nghiệp vụ** khi cùng task và cùng action (tool_id, arguments_sha256, resource_ref). Khi đó action mới dùng lại `operation_key`; Action Service trả kết quả đã journal thay vì gọi tool lần hai.
- Cùng `operation_key` nhưng khác tool_id, arguments_sha256 hoặc resource_ref → `409 OPERATION_KEY_CONFLICT`, không thực thi (validator: "same operation_key with different target repository").
- Một ý định mới (người dùng muốn tạo PR thứ hai) phải là task mới hoặc intent_key mới do người dùng xác nhận rõ ràng, có audit.
- `retry_new_attempt` bị từ chối với `REMOTE_OUTCOME_UNKNOWN` khi run hiện tại `unknown`. Với agent `external_trusted` tự thực hiện side effect, platform chỉ có `task_intent_key` và reconciliation; không có dedupe phía Action Service, nên run phải ở `unknown` cho tới khi có bằng chứng đối soát.

Acceptance (C29): provider mock tạo PR rồi mất response; worker restart; người dùng bấm retry. Kết quả bắt buộc: retry bị từ chối khi còn unknown; sau reconcile tìm thấy PR theo `task_intent_key`/operation_key; đúng 1 PR ở mock; attempt history đầy đủ; một retry với cùng key nhưng repository đích khác bị 409.

Run failed không tự retry các side effect chưa rõ. Terminal state không hồi sinh vì late event. Late usage/artifact có thể được append vào result history; conflict result tạo reconciliation record. Pause/resume dùng checkpoint chỉ khi capability đã certify. `waiting_approval` không phải pause mọi process bên provider, adapter phải mô tả boundary đó.

## At-least-once và consistency
Unique task submission theo tenant+client idempotency key. Unique adapter submission theo binding+run ID+operation key. Event key theo tenant+producer+event_id. Cùng ID/different hash thành quarantine/conflict. Outbox transaction cùng mutation. Projection có revision/CAS và versioned reducer.
External idempotency không có thì store intent, query status trước retry. Process identity gồm PID và creation time trên host. Lease có fencing token cho gateway writes, old shell có filesystem access cần stop/quarantine workspace trước re-dispatch. Không hứa exactly-once remote side effects.

## Event ordering
event_id unique, run_id, producer_id, producer_seq, occurred_at, received_at. Ingestion tự gán monotonic offset theo tenant stream. producer_seq chỉ có thứ tự trong producer/run, không phải global clock. Dedupe, gap detection, bounded buffer và reconciliation khi seq gap; replay dùng ingestion cursor và reducer rules. Heartbeat không thay task progress. SSE Last-Event-ID trỏ cursor opaque user-scoped, không dùng wall-clock làm cursor.

### Offset theo thứ tự commit (ADR-020)
Không dùng sequence/bigserial cấp trong transaction ghi event làm cursor: hai transaction có thể lấy offset 10 rồi 11 nhưng commit 11 trước 10. Nếu SSE đã gửi 11, client với cursor 11 sẽ không bao giờ nhận 10. Thiết kế: transaction nghiệp vụ chỉ ghi outbox (không có offset). Một relay đơn (một worker giữ advisory lock theo tenant stream) đọc các outbox row **đã commit**, cấp `ingest_offset` tăng dần trong transaction riêng, rồi publish. Vì vậy thứ tự offset chính là thứ tự relay nhìn thấy commit. Cursor HUD chỉ trỏ tới offset do relay cấp. Snapshot HUD ghi `projection_revision` = offset lớn nhất đã áp vào projection, trong cùng transaction đọc. Throughput relay đơn cần đo ở P1a-WP7; nếu không đủ mới cân nhắc phân vùng theo tenant hoặc watermark theo `pg_snapshot_xmin`.
Test C33: hai transaction lấy thứ tự cấp 10, 11; ép 11 commit trước 10. Sau replay từ cursor bất kỳ, projection HUD phải bằng snapshot mới tạo; không event nào bị mất hay lặp.

## Authorization
Quyền effective là giao user grant, agent deployment grant, task scope và resource policy. Tool gateway kiểm tra tại thời điểm execute, bao gồm after-approval và resumed runs. RBAC role + ABAC tenant/project/classification/purpose. Approval payload có arguments hash, target revision, scope, expiration, nonce và approver. `action_digest` = SHA-256 của RFC 8785 JCS trên đúng các trường binding_id, run_id, task_id, tool_id, arguments_sha256, resource_ref, expected_resource_revision, policy_version, operation_key, nonce, expires_at (`sha256-jcs-v1`). Approval decision (`contracts/approval-decision.schema.json`) single-use: Action Service trong một transaction kiểm tra digest/nonce/valid_until/role, chuyển consumption → consumed và ghi execution intent theo operation_key; execution trùng operation_key trả kết quả journal cũ thay vì gọi tool lần hai. Agent text không được approve action của chính nó.

Custom tools dùng cùng Action Service dù vendor có permission policy. Credentials qua secret store, resource handles chỉ resolve server-side. SSRF checks cho URL tools phải kiểm tra protocol/resolved IP/redirects. File root check xử lý symlink/reparse + TOCTOU, giới hạn parser/file sizes. Trên Windows connector phải: mở file bằng handle với `FILE_FLAG_OPEN_REPARSE_POINT` và từ chối reparse point/junction; lấy final path từ handle (`GetFinalPathNameByHandle`) rồi so containment theo dạng chuẩn hóa, không phân biệt hoa/thường; từ chối `\\?\`, UNC, alternate data streams (`:` trong tên), 8.3 short-name mismatch và file có link count > 1 (hardlink không bị realpath phát hiện); kiểm tra lại size/hash sau khi đọc. Trên Linux dùng `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` khi có, hoặc `O_NOFOLLOW` từng thành phần. Đây là giả định thiết kế cần spike P0-WP3 trên Windows thật. Commands allowlisted argv trong isolated worker.

### Untrusted project content và prompt injection
Nội dung repo (`architecture.md`, blueprint, implement plan, `.ppm/notes`, `.ppm/updates`) chỉ đi vào model như data có delimiter và provenance. Enforcement không dựa vào prompt: (1) analysis agent chỉ có tools đọc snapshot đã cấp và `ppm.propose_assessment`; (2) mọi evidence_ref trong proposal phải resolve tới evidence do collector thu, nếu không reducer loại bỏ; (3) reducer deterministic đặt criterion `verified` chỉ từ evidence observed khớp revision/criterion — text của model hoặc note không thể đặt verified; (4) Model Gateway áp egress policy theo classification trước request, nên lời nhắc "gửi .env lên cloud" không có đường thực thi; (5) deny_globs loại `.env`, khóa, credentials trước snapshot. Test: fixture note chứa lệnh giả mạo (C22).

### Tenant và producer identity
Ingestion gắn tenant từ binding/credential của producer; event có `tenant_id` khác binding bị quarantine và tạo security event (C23). Cursor HUD, idempotency key và project ID đều scoped theo tenant.

## Budget
Reserve trước dispatch theo task tree, có max tokens, wall time, tool calls, child tasks và cost estimate. Model calls ghi provider usage IDs để dedup cumulative snapshots. Usage unknown hiển thị unknown. Reservation settlement cần reconcile late usage và release unused reservation. Circuit breaker chặn new calls khi chạm limit, cancellation có độ trễ phải được phản ánh trong HUD.

## Network và storage
OIDC service tokens/mTLS hoặc scoped connector token rotation cho host connector. Cloud policy theo provider/classification kể cả traces/embeddings. Events audit mặc định metadata redacted, raw tool output là artifact cần quyền. PostgreSQL role/RLS nếu chọn phải test connection pool tenant reset. Evidence object URI không trực tiếp cấp quyền, download qua authenticated API hoặc signed URL ngắn hạn được kiểm tra lại.
