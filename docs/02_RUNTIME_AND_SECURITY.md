# Runtime, persistence và kiểm soát hành động
## Request admission
Authenticate → resolve tenant/principal → authorize task type and resource refs → validate input schema → resolve approved agent release and binding → reserve task-tree budget → create task/run + dispatch intent/outbox transaction → worker claim lease → adapter submit → persist external execution ID → events/results → domain verification.
Public request body không nhận tenant/user/permission claims. Internal envelopes do gateway xây từ credentials. Scope chỉ được thu hẹp khi delegate. Parent cancellation ngừng task con theo policy và đợi confirmation hoặc reconcile.

## State transitions
| Run state | Event/command | Next | Điều kiện |
|---|---|---|---|
| queued | dispatch claimed | dispatching | lease + capability + budget valid |
| dispatching | external ID confirmed | running | provider acceptance persisted |
| dispatching | response ambiguous | unknown | reconcile by idempotency key/process identity |
| running | action proposal | waiting_approval | immutable action digest + expiry |
| waiting_approval | authorized decision | running | reauthorize and consume approval |
| running | output validated | succeeded | result schema pass; domain verification queued |
| running/waiting_approval | cancel requested | cancelling | adapter supports cancel or operator workflow |
| cancelling | provider stop confirmed | cancelled | confirmation evidence |
| active state | deadline | timed_out | request stop and retain remote outcome tracking |
| unknown | verified provider status | reconciled state | authoritative provider observation |

Run failed không tự retry các side effect chưa rõ. Terminal state không hồi sinh vì late event. Late usage/artifact có thể được append vào result history; conflict result tạo reconciliation record. Pause/resume dùng checkpoint chỉ khi capability đã certify. `waiting_approval` không phải pause mọi process bên provider, adapter phải mô tả boundary đó.

## At-least-once và consistency
Unique task submission theo tenant+client idempotency key. Unique adapter submission theo binding+run ID+operation key. Event key theo tenant+producer+event_id. Cùng ID/different hash thành quarantine/conflict. Outbox transaction cùng mutation. Projection có revision/CAS và versioned reducer.
External idempotency không có thì store intent, query status trước retry. Process identity gồm PID và creation time trên host. Lease có fencing token cho gateway writes, old shell có filesystem access cần stop/quarantine workspace trước re-dispatch. Không hứa exactly-once remote side effects.

## Event ordering
event_id unique, run_id, producer_id, producer_seq, occurred_at, received_at. Ingestion tự gán monotonic offset theo tenant stream. producer_seq chỉ có thứ tự trong producer/run, không phải global clock. Dedupe, gap detection, bounded buffer và reconciliation khi seq gap; replay dùng ingestion cursor và reducer rules. Heartbeat không thay task progress. SSE Last-Event-ID trỏ cursor opaque user-scoped, không dùng wall-clock làm cursor.

## Authorization
Quyền effective là giao user grant, agent deployment grant, task scope và resource policy. Tool gateway kiểm tra tại thời điểm execute, bao gồm after-approval và resumed runs. RBAC role + ABAC tenant/project/classification/purpose. Approval payload có arguments hash, target revision, scope, expiration, nonce và approver. Agent text không được approve action của chính nó.

Custom tools dùng cùng Action Service dù vendor có permission policy. Credentials qua secret store, resource handles chỉ resolve server-side. SSRF checks cho URL tools phải kiểm tra protocol/resolved IP/redirects. File root check xử lý symlink/reparse + TOCTOU, giới hạn parser/file sizes. Commands allowlisted argv trong isolated worker.

## Budget
Reserve trước dispatch theo task tree, có max tokens, wall time, tool calls, child tasks và cost estimate. Model calls ghi provider usage IDs để dedup cumulative snapshots. Usage unknown hiển thị unknown. Reservation settlement cần reconcile late usage và release unused reservation. Circuit breaker chặn new calls khi chạm limit, cancellation có độ trễ phải được phản ánh trong HUD.

## Network và storage
OIDC service tokens/mTLS hoặc scoped connector token rotation cho host connector. Cloud policy theo provider/classification kể cả traces/embeddings. Events audit mặc định metadata redacted, raw tool output là artifact cần quyền. PostgreSQL role/RLS nếu chọn phải test connection pool tenant reset. Evidence object URI không trực tiếp cấp quyền, download qua authenticated API hoặc signed URL ngắn hạn được kiểm tra lại.
