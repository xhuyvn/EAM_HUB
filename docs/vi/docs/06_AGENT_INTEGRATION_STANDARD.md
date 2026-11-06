> Translated from docs/06_AGENT_INTEGRATION_STANDARD.md @ fb857f4

# Enterprise Agent Integration Contract 1.0
## Các mức tích hợp
| Level | Năng lực | Điều kiện |
|---|---|---|
| L0 Observe | Inventory, status/events/results có thể đọc | Data permissions + identity mapping |
| L1 Managed | Submit/status/results, cancel theo capability | Idempotency/recovery certification |
| L2 Governed actions | Tool writes qua gateway, exact approvals | Enforcement path và sandbox/network boundary được test |
| L3 Optimizable | Versioned harness + eval candidate pipeline | Phase 2 release controls |

Adapter chỉ quảng bá capability đã có evidence. Existing agent có tool access trực tiếp riêng phải khai báo execution_boundary=external_trusted hoặc sandboxed; platform chỉ bảo đảm các hành động thực sự đi qua gateway. Agent có secret rộng phải được thu hẹp quyền hoặc giới hạn L0 trước managed-write onboarding.

## Package
agent.json chứa identity, owner refs, task types, schema refs, runtime kind, desired capabilities, desired tools, eval profile và harness version. Prompt và task schema nằm cùng package có digest. trusted binding lưu endpoint_ref, secret_ref, host/root scope, approved capability set, timeout/cost profile và grant IDs ngoài repo.

Owner_ref trong package là metadata đề xuất. Registry đối chiếu owner với IdP và approver trước active release. Version immutable, artifact hash kiểm tra trước dispatch. Disable/uninstall có dependency report, revoke credentials/grants và reconcile active runs trước retire.

## Adapter interface
capabilities(binding) trả verified flags và evidence reference.
submit(task, execution_context, idempotency_key) trả execution_ref hoặc ambiguous outcome. idempotency_key là operation key ổn định của run (docs/02), không phải key mới mỗi lần thử.
lookup(idempotency_key) trả found(execution_ref)/not_found/unsupported; `not_found` chỉ có giá trị khi provider cam kết lookup authoritative. Adapter không có lookup thì không được tự retry submit.
status(execution_ref) trả normalized state + provider raw code + observed_at.
events(execution_ref, cursor) trả events nếu supported, polling adapter có snapshot reconciliation.
collect_result(execution_ref) trả typed result + artifact refs.
cancel/resume/approve_tool chỉ expose khi capability=true và current run state cho phép.

Unsupported capability trả typed error `CAPABILITY_UNSUPPORTED`. CLI exit 0 chỉ là transport/process success, result schema và domain verifier quyết định mức hoàn thành. stdout/stderr không chứa credentials; stderr redacted thành restricted diagnostic artifact.

## Adapter types
Native Python domain có typed function/tool boundary. HTTP service dùng service identity, endpoint allowlist, timeouts, webhook signature + replay protection. CLI process dùng executable path đã pin, argv, explicit cwd, env allowlist và process tree ownership. Vendor SDK dùng supported public API và version pin. Session mở sẵn cần explicit registration/handover và exclusive controller. GUI automation cần user-session bridge, focus/input arbitration và capability review riêng.

Codex/Claude/OpenCode/Antigravity names trong manifest là adapter target. P0 phải xác minh version/auth/OS/tool approval/cancel/resume thật bằng fixture. Package không mặc định vendor nào có full attach/resume hoặc cost accounting.

Vendor runtime tự thực thi tool phía server cần phân loại riêng. Ví dụ đã kiểm chứng (Claude Platform docs, Managed Agents permission policies, truy cập 06/11/2026): permission policies điều khiển tool chạy phía server (agent toolset, MCP toolset); custom tools do ứng dụng thực thi và không chịu permission policies. Hệ quả: custom tools có thể đi qua Action Service (đạt L2); built-in toolset (bash/file/web) chạy trong sandbox của vendor nằm ngoài platform enforcement và là đường egress cloud. Binding chỉ được `governed` khi built-in toolset bị tắt hoặc mọi tool server-side đặt `always_ask` và confirmation được bridge sang platform approval có digest; nếu không, `execution_boundary=external_trusted` và tối đa L1. Adapter certification phải có test chứng minh cấu hình này (C25).

## Conformance
Schema + IDs + output refs; unauthorized submit/action; scope narrowing delegation; duplicate submission; crash before/after external acceptance; out-of-order stream; cancellation late acknowledgement; missing heartbeat; schema drift; redaction; cost unknown; provider failover egress policy; task result vs acceptance separation. `evals/CONFORMANCE_CASES.md` có expected outcomes.
