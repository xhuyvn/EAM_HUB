> Translated from docs/10_SOURCES_AND_LIMITATIONS.md @ a36d44e

# Nguồn, quyết định và giới hạn kiểm chứng
Ngày kiểm tra web 05/11/2026. Nguồn công khai được dùng cho capability/nguyên lý; SDK và auth cụ thể cần compatibility spike khi implementation.

| ID | Nguồn chính thức | Áp dụng |
|---|---|---|
| S1 | https://openai.com/index/harness-engineering/ | Repo knowledge, environment và feedback loops |
| S2 | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents | Incremental sessions, initializer và progress artifacts |
| S3 | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents | Eval tasks/trials/graders và lifecycle |
| S4 | https://platform.claude.com/docs/en/managed-agents/session-observability | Session events, usage và observability |
| S5 | https://platform.claude.com/docs/en/managed-agents/permission-policies | Custom-tool enforcement boundary |
| S6 | https://docs.langchain.com/oss/python/langgraph/persistence | Checkpointer và long-term stores |
| S7 | https://www.iso.org/standard/42001 | Organization AI management system |
| S8 | https://airc.nist.gov/airmf-resources/airmf/5-sec-core/ | Govern/Map/Measure/Manage |

Các tài liệu nguồn riêng của người dùng được đọc từ ba ZIP trong SOURCE_MANIFEST.json. Blueprint PPM v2 hiện hành được đưa kèm với integration notice. Secretary/Enterprise Harness sources được tổng hợp theo bảng reconciliation, không dùng roadmap cũ làm phase delivery mới.

Package validation chỉ kiểm tra tài liệu, examples/schema, một số invariant và ZIP checksum. Chưa đo chất lượng model, độ trễ HUD, compatibility live SDK, bảo mật runtime hoặc khả năng phục hồi triển khai. Các target được ghi là đề xuất. Các quyết định scope do bản thiết kế chốt gồm modular monolith, LangGraph/PostgreSQL, PPM-first pilot, read-only/managed certification và optimization phase 2.
