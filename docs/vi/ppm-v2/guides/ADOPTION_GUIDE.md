> Translated from ppm-v2/guides/ADOPTION_GUIDE.md @ a36d44e

# Hướng dẫn áp dụng

## A. Dự án đang làm
1. Mở đúng repository bằng coding agent; dùng prompt 01 và chỉ rõ PROJECT_ROOT.
2. Agent inventory docs/Git chỉ đọc trước, giữ nguyên source và file hướng dẫn có sẵn.
3. Copy/adapt `templates/project/.ppm/`; thay project ID đồng bộ, map tài liệu có thật, thiếu để null.
4. Tạo initial update: mô tả trạng thái quan sát hiện tại; không phục dựng phiên cũ.
5. Gộp đoạn instructions prompt 03 vào AGENTS.md/CLAUDE.md đang dùng. Nếu hai file đều có, đặt contract ở `.ppm/README.md`, thêm chỉ dẫn ngắn tránh duplicate rules trái nhau. Không dùng symlink trên Windows chỉ để chia sẻ instructions.
6. Owner đăng ký location/scan policy trong registry; không để coding agent suy ra quyền cloud.
7. Chạy validator; owner review mục tiêu/criteria để tạo baseline chính thức sau này.

## B. Dự án tương lai
Dùng prompt 02 cùng ý tưởng/scope. Tạo mục tiêu và criteria draft trước implementation. Thiếu metric ghi null/question. Không tạo baseline accepted khi chỉ có ý tưởng. Sau mỗi work package dùng prompt 04/05.

## C. Dự án đã triển khai
Ghi release, deployment docs và operation_reported. Thiếu deployment evidence thì reported running, chưa verified deployment. Onboarding không rebuild/redeploy, không chạy migration DB. Tạo maintenance backlog và verification gaps. Có thể đóng hành chính theo blueprint nhưng không tạo technical acceptance giả.

## D. Không dùng Git
base_revision/result_revision=null; working_tree_state=not_git. Collector dùng snapshot manifest/hash, không bịa SHA. Không bắt init Git để được onboarding.

## E. Monorepo và nhiều repo
Monorepo có các subproject `.ppm/` riêng; location registry scope theo subdirectory. Multi-repo dùng location_id/ref rõ ràng, không relative traversal ra root. Quan hệ program/portfolio chỉ proposed cho đến owner xác nhận.

## F. Hằng ngày
Bắt đầu: prompt 04. Kết thúc: prompt 05. Review: prompt 06. Đổi scope: prompt 07. Triển khai chính agent PPM: prompt 08.
Không cần app PPM để bắt đầu ghi notes; chưa triển khai app thì quét bằng prompt review thủ công. Watcher và lịch quét chỉ hoạt động sau khi collector được xây, cấu hình và chạy.

## G. Migration
Giữ backup ngoài đường dẫn ingest hoặc ghi hậu tố không ingest. Map v1 artifacts sang artifact_mapping, bổ sung location_ref. Di chuyển classification/cloud_processing khỏi repo config sang registry owner-controlled. Giữ project ID; ghi migration update và old/new contract version. Đã có `.ppm/` thì merge từng field, không copy đè. Schema major không biết phải dừng migration, vẫn cho read-only gap report.

## H. Checklist tiếp nhận
[ ] ID ổn định và đồng nhất các file.
[ ] Mapping đúng, path không thoát root.
[ ] Mục tiêu/criteria còn draft được gắn nhãn.
[ ] Có initial update và không có lịch sử giả.
[ ] Chưa chạy check ghi not_run.
[ ] Location/permissions trong registry riêng.
[ ] Không chứa secret hoặc path riêng tư trong repo.
[ ] Instructions merge không xóa quy tắc cũ.
[ ] Validator pass; gaps được owner nhìn thấy.
