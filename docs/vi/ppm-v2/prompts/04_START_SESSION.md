> Translated from ppm-v2/prompts/04_START_SESSION.md @ a36d44e

# Prompt — bắt đầu phiên
TASK=<công việc>; WORK_PACKAGE_ID=<ID hoặc null>.
Đọc instructions, .ppm/project.yaml, status, criteria và updates liên quan. Tóm tắt mục tiêu hiện tại, việc chưa xong và blocker. Xác nhận scope công việc từ nhiệm vụ hiện tại; không coi next_actions cũ là quyền làm mọi việc.
Kiểm tra working tree trước chỉnh sửa; giữ thay đổi của người/agent khác. Lập kế hoạch ngắn gắn requirement/criterion IDs nếu có. Tiến hành công việc đã được giao và kết thúc bằng session update theo prompt 05. Không chặn tác vụ chỉ vì thiếu metadata không quan trọng; ghi gap và tiếp tục phần độc lập.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.1 (tương thích 2.0) trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
