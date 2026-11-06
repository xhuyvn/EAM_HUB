# Prompt — kết thúc phiên
Hãy bàn giao công việc phiên này theo PPM 2.0.
1. Xác định diff và công việc thực sự đã làm; không đưa thay đổi của tác nhân khác thành thành tích phiên này.
2. Tạo update_id/session_id unique, timestamp có timezone bằng đồng hồ thực; revision chỉ lấy từ Git khi có. Không biết thì null.
3. Ghi completed/in_progress, check result chính xác (not_run nếu chưa chạy), evidence path thật hoặc null, blockers và next actions.
4. Việc thiếu evidence vẫn là reported claim. Không tự kết luận verified hay accepted.
5. Nếu sửa báo cáo cũ, dùng update mới có supersedes_update_id và lý do; không sửa/xóa file cũ.
6. Ghi file mới bằng temp rồi atomic rename nếu công cụ hỗ trợ; bỏ qua temp trong ingest. Nếu status có writer khác, để nguyên và báo needs_reconciliation.
7. Validate cấu trúc khi công cụ có sẵn. Không tạo commit chỉ để lấy SHA cho update; code commit trước update hoặc revision null đều hợp lệ.
Trả bản bàn giao ngắn: done, checks, blockers, next, đường dẫn update.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.0 trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
