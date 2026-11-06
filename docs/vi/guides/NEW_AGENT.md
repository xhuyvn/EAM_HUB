> Translated from guides/NEW_AGENT.md @ fb857f4

# Hướng dẫn tích hợp agent mới
1. Chọn business owner, task types, input/output, acceptance criteria và mức L0/L1/L2 cần thiết.
2. Copy `templates/new-agent/` thành agent package riêng. Thay ID/name/owner/schema/tool refs, giữ contract_version=1.0.
3. Viết tools có typed args, authorization tại service và idempotency cho mutations. Model input chỉ gồm context đã authorize.
4. Cài adapter theo protocol và đăng ký verified capability matrix. Native runtime dùng workflow entrypoint của platform, không tự tạo scheduler thứ hai.
5. Bổ sung domain fixtures và eval rubric; chạy conformance cases trước staging.
6. Build immutable release bundle, hash prompt/schema/tools/config. Đăng ký registry ở draft/testing bằng API.
7. Admin tạo deployment binding từ template, resolve secrets/profile/root grants ở environment. Chạy permission và budget smoke test.
8. Shadow/read-only pilot, kiểm tra HUD trace/results/cost/freshness. L1/L2 cần gate tương ứng.
9. Reviewer duyệt active release, publish version record. Pilot results phải gắn exact bundle hash.

First task nên là bounded read-only fixture. Đầu ra bàn giao gồm manifest, adapter capability evidence, schema, prompt, eval report, owner decision và rollback plan. Dùng `prompts/02_ONBOARD_NEW_AGENT.md`.
