> Translated from ppm-v2/prompts/07_CHANGE_PROPOSAL.md @ fb857f4

# Prompt — thay đổi mục tiêu/phạm vi
CHANGE=<thay đổi mong muốn>.
Đọc baseline/criteria hiện có và trạng thái approval. Lập change proposal: lý do, phương án, affected requirements/deliverables, architecture impact, dependencies, nguồn lực, risks, criteria cần đổi và revalidation cần chạy.
Giữ baseline cũ. Tạo decision note state=proposed, evidence refs và câu hỏi cần owner quyết định. Không tự thay approved scope hoặc gán accepted. Có thể tiếp tục phần công việc độc lập đã được giao; nêu phần bị chặn bởi quyết định.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.1 (tương thích 2.0) trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
