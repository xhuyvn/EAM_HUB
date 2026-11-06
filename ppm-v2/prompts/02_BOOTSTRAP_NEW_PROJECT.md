# Prompt — dự án mới
PROJECT_ROOT=<đường dẫn>; IDEA=<ý tưởng>; OWNER=<owner ref>.
Hãy khởi tạo hồ sơ PPM 2.0 cho dự án mới trước khi implementation.
- Đọc yêu cầu, ghi mục tiêu, scope và out-of-scope; phần chưa quyết định thành open questions.
- Tạo architecture/blueprint/implementation plan draft nếu chưa có, chỉ ở mức thông tin đủ căn cứ. Không trình bày quyết định chưa duyệt như baseline.
- Tạo .ppm theo templates; project ID ổn định, artifact mapping thật, lifecycle_reported=proposed, operation_reported=not_applicable, progress=null.
- Viết acceptance criteria draft có ID, mô tả đo được, weight và verification_method. Đánh dấu draft cho đến owner phê duyệt trong PPM.
- Tạo initial planning update; checks=not_run khi chưa chạy.
- Đề xuất program/portfolio membership nếu có căn cứ, giữ trạng thái proposed.
- Gộp instructions prompt 03; báo các quyết định cần owner xác nhận.
Chỉ khởi tạo tài liệu/metadata trong tác vụ này, chưa tự triển khai toàn bộ ý tưởng.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.1 (tương thích 2.0) trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
