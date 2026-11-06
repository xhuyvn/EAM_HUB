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
