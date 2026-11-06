> Implementation workflow (06/11/2026): coding agents start with `AGENTS.md` and `STATUS.md`; reviews use `prompts/REVIEW_SESSION.md` and `docs/REVIEW_LOG.md`. Dev environment: `docs/DEV_ENVIRONMENT.md`.

# Enterprise Agent Platform — Integrated Blueprint 1.1
Phát hành 05/11/2026; revision 1.1 ngày 06/11/2026 sau architecture review (`docs/ARCHITECTURE_REVIEW.md`). Tài liệu thiết kế tiếng Việt, contracts và prompt cho coder bằng tiếng Anh. Package bao gồm thiết kế platform, HUD, PPM v2, phase 2 harness optimization và agent onboarding kit.

## Bắt đầu
1. Đọc `MASTER_BLUEPRINT.md` để nắm kiến trúc và phạm vi phase 1/2.
2. Đọc `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` để hiểu những lựa chọn đã hợp nhất.
3. Đọc `docs/02_RUNTIME_AND_SECURITY.md`, `docs/03_HUD_SPEC.md`, `docs/04_PPM_INTEGRATION.md`.
4. Dùng `guides/NEW_AGENT.md` hoặc `guides/EXISTING_AGENT.md` để tiếp nhận agent.
5. Đọc `docs/ARCHITECTURE_REVIEW.md` (findings, readiness) và `docs/IMPLEMENTATION_PLAN.md` (work packages). Giao coder bắt đầu bằng `prompts/START_NEXT_MILESTONE.md` (P0); `prompts/01_START_PLATFORM_P0.md` là bản tóm tắt cũ cùng phạm vi.
6. Phase 2 có đặc tả riêng tại `docs/05_PHASE2_HARNESS_OPTIMIZATION.md`.

## Trạng thái bàn giao
Đây là specification và integration kit. Validation report ghi các kiểm tra đã chạy trên package. Runtime, adapter live, HUD realtime, security/load test và benchmark model sẽ được thực hiện qua các milestone.
`visuals/HUD_MOCKUP.svg` và PNG là mockup có dữ liệu minh họa, dùng để review bố cục.

## Quy tắc nguồn chính thức
Tài liệu gốc của package là MASTER_BLUEPRINT, docs, contracts và guides. `ppm-v2/` chứa contract, templates và hướng dẫn domain PPM. Platform quyết định auth, run lifecycle, scheduling và integration. PPM quyết định project assessment, criteria và acceptance. Provenance của các tài liệu đã hợp nhất nằm trong SOURCE_MANIFEST.json.

## Dùng template
Các JSON trong templates dùng giá trị ví dụ. Tạo ID thực, owner thật và binding credentials ngoài repository trước onboarding. Agent manifest là proposal, deployment binding phải được trusted admin xác nhận. Trường tenant/principal trong event nội bộ do gateway xác lập.

## Kiểm tra cấu trúc
Python 3.10+ với jsonschema và PyYAML (`pip install -r tools/requirements-validated.txt`). Chạy từ package root.
```bash
python tools/validate_package.py
sha256sum -c SHA256SUMS.txt
```
`validate_package.py` kiểm tra 16 schema, fixtures, transition table, approval binding, negative cases và gọi `ppm-v2/tools/run_negative_cases.py`; nó ghi lại `VALIDATION_REPORT.md` với thời điểm chạy và git revision.
Phiên bản dependency đã dùng trong `tools/requirements-validated.txt`. Script kiểm tra schema/examples và một số semantic invariants. Security/recovery test plan được ghi riêng, chưa có runtime để thực thi các gate đó.
