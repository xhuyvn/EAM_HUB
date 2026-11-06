> Platform integration notice — 05/11/2026. Host permissions, task/run lifecycle, scheduling and executor dispatch follow the platform MASTER_BLUEPRINT and docs/04_PPM_INTEGRATION. PPM contract 2.0 retains domain reporting semantics. Use platform P0–P5 for delivery and map legacy PPM M0–M6 as described in docs/04.

# AI PPM Blueprint v2.0
Ngày: 01/11/2026. Bộ thiết kế + chuẩn + prompt; chưa phải ứng dụng chạy hoàn chỉnh.

## Đọc và áp dụng
1. Đọc `AI_PPM_Agent_Blueprint_v2.0.md` và `standards/PROJECT_MANAGEMENT_CONTRACT.md`.
2. Dự án đang làm: dùng `prompts/01_ADOPT_EXISTING_PROJECT.md`.
3. Dự án mới: dùng `prompts/02_BOOTSTRAP_NEW_PROJECT.md`.
4. Đưa nội dung `prompts/03_REPOSITORY_INSTRUCTIONS.md` vào hướng dẫn agent hiện có theo cách bổ sung, giữ nguyên quy tắc của dự án.
5. Mỗi phiên dùng prompt bắt đầu/kết thúc; định kỳ dùng prompt review.
6. Đăng ký location ở registry của ứng dụng PPM. Chưa có ứng dụng thì lưu registry làm bản dự thảo; chưa có tự động quét chỉ nhờ copy `.ppm/`.

Prompt dùng cho coding agent có quyền được owner cấp. PPM collector chỉ coi repository là data. Prompt không cấp quyền mới, không tự cài hook, lịch hệ điều hành hoặc gửi dữ liệu cloud.

## Nội dung
- Blueprint đầy đủ kế thừa v1, cập nhật contract v2.
- Chuẩn dữ liệu, hướng dẫn tiếp nhận, thiết kế scanner.
- 8 prompt copy/paste, template portable và registry riêng.
- JSON Schema và validator tham chiếu, test fixtures và kết quả kiểm tra đóng gói.

## Kiểm tra mẫu
Yêu cầu Python 3 và `jsonschema`, `PyYAML` (khai báo trong tools/requirements.txt).
Chạy từ thư mục bộ tài liệu:

```bash
python tools/validate_contract.py templates/project/.ppm --schema schemas/contract.schema.json
python tools/validate_contract.py templates/registry.example.yaml --schema schemas/contract.schema.json
```

Validator không chạy test của dự án, không gửi network request và không chứng nhận tiến độ. Mẫu chứa ID dự án minh họa; thay đồng bộ trước khi sử dụng. Snapshot source nghiên cứu là 31/10/2026; v2 tập trung đặc tả contract và adoption.
