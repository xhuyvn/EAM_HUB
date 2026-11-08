> Translated from docs/09_DOMAINS_AND_STANDARDS.md @ a36d44e

# Domain expansion và standards mapping
## Phạm vi kế thừa
| Domain | Dữ liệu/workflow chính | Integration requirement |
|---|---|---|
| Knowledge | Document/version/effective date/ACL, clause-aware ingestion, hybrid retrieval, citations | Evidence refs version-specific, pre/post retrieval authorization |
| Governance/Risk | Requirement-control map, gaps, drafts, review | Requirement source traceable, reviewer authority |
| Research | Source articles, dates, duplicate events, briefing subscriptions | Provenance + scheduled delivery dedup |
| Productivity | Personal tasks/reminders, timezones, assignments | Project IDs do PPM sở hữu, verified recipient binding |
| Editorial | Draft/revision/site profile/fact check/publish | Approval gắn exact revision và destination |
| Media | Script/storyboard/TTS/render/QC/assets | Isolated resource quota, rights/provenance |
| Engineering | Work package/code/test/patch | Workspace isolation, verifier, integration approval |
| Operations | Monitoring/incident/runbook action | Environment scope và scoped change actions |

Mỗi domain được phát triển như agent package + domain service. Một package có thể dùng deterministic workflow và LLM chỉ cho phần cần phân tích. Domain owner chốt eval và resource budget trước active.

## Mapping quản trị đề xuất
ISO/IEC 42001 cho responsibility/lifecycle/performance review. NIST AI RMF Govern/Map/Measure/Manage map sang registry/policy, threat/domain context, eval/metrics và incidents/releases. ISO/TC 258 PPM guidance map vào PPM baseline/benefits/portfolio/governance. Mapping ở cấp kiến trúc, việc đánh giá từng điều khoản cần tài liệu tiêu chuẩn đầy đủ và review chuyên gia.

OpenAI harness engineering và Anthropic long-running harness/evals cung cấp nguyên lý context/artifacts/feedback/evaluation. Thiết kế package cụ thể, contract versions và milestone gates là đề xuất của tài liệu này, không phải tuyên bố stack nội bộ của vendor.
