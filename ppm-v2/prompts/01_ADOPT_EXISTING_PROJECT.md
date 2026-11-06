# Prompt — tiếp nhận dự án đang làm/đã triển khai
Copy đoạn dưới cho coding agent, thay PROJECT_ROOT. Cung cấp kèm standards, schemas và templates từ bộ v2.

Bạn đang tiếp nhận dự án tại PROJECT_ROOT=<đường dẫn thực>. Hãy áp dụng Project Management Contract 2.0 với thay đổi tối thiểu.
1. Đọc hướng dẫn repository đang có. Inventory architecture/blueprint/implementation plan, Git HEAD/dirty state và tài liệu test/deployment hiện hữu. Không thực thi code ở bước này.
2. Nếu đã có .ppm, xác định version và giữ ID; version chưa biết thì tạo gap report, không migrate mù quáng.
3. Tạo/adapt project.yaml và status.yaml, updates/, decisions/, acceptance.yaml theo schema; chỉ map file có thật. Mục tiêu và criteria suy ra là draft; thiếu để null/ghi gap.
4. Với dự án đã triển khai, tách lifecycle_reported và operation_reported; không suy deployed từ build config.
5. Tạo initial update mới với UUID, recorded_at thực, mô tả trạng thái tiếp nhận; không dựng lịch sử cũ. Chưa chạy test thì not_run.
6. Gộp hướng dẫn prompt 03 vào file chỉ dẫn agent phù hợp, giữ các quy tắc cũ; tránh lặp instructions.
7. Đề xuất entry registry riêng cho owner điền path/quyền. Không đặt absolute path hoặc cloud permissions trong .ppm.
8. Validate bằng script được cung cấp nếu dependency có sẵn; nếu chưa có, báo rõ thay vì tự cài dependency ngoài phạm vi.
Không chỉnh code chức năng, không commit/push/deploy trong tác vụ tiếp nhận này.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.0 trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
