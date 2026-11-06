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
