# Đoạn hướng dẫn đưa vào AGENTS.md hoặc CLAUDE.md
Đây là nội dung để owner/coding agent được ủy quyền merge vào hướng dẫn hiện có. Không phải file Skill và không yêu cầu cài plugin.

## Project management reporting — PPM 2.0
- Đọc .ppm/project.yaml, status.yaml, acceptance.yaml và các update liên quan trước khi thực hiện work package.
- Dùng mục tiêu/tiêu chí để giữ scope; thiếu hoặc mâu thuẫn thì nêu gap, không tự đổi baseline.
- Sau mỗi phiên có công việc đáng kể, tạo .ppm/updates/<UUID>.yaml theo schema. Không sửa update cũ; correction qua supersedes_update_id.
- Ghi completed/in_progress, checks đã chạy/chưa chạy, blockers và next_actions. Không bịa kết quả, SHA, thời gian hoặc evidence.
- Mỗi agent ghi file riêng; chỉ designated writer cập nhật status.yaml khi không có conflict.
- Không tự đánh dấu accepted/deployed/benefit realized từ checklist hoặc test pass.
- Giữ metadata portable, không secrets/absolute host paths. Quyền và cloud policy do registry trung tâm quyết định.
- Không commit/push/deploy hoặc sửa scope chỉ để cập nhật PPM nếu không nằm trong nhiệm vụ.
- Báo rõ nếu phiên kết thúc đột ngột hoặc không thể ghi update; phiên sau ghi recovery update với thông tin quan sát được.

Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.0 trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
