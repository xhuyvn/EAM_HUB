# Domain expansion and standards mapping
## Heritage scope
| Domain | Primary data/workflow | Integration requirement |
|---|---|---|
| Knowledge | Document/version/effective date/ACL, clause-aware ingestion, hybrid retrieval, citations | Evidence refs version-specific, pre/post retrieval authorization |
| Governance/Risk | Requirement-control map, gaps, drafts, review | Requirement source traceable, reviewer authority |
| Research | Source articles, dates, duplicate events, briefing subscriptions | Provenance + scheduled delivery dedup |
| Productivity | Personal tasks/reminders, timezones, assignments | Project IDs owned by PPM, verified recipient binding |
| Editorial | Draft/revision/site profile/fact check/publish | Approval binds exact revision and destination |
| Media | Script/storyboard/TTS/render/QC/assets | Isolated resource quota, rights/provenance |
| Engineering | Work package/code/test/patch | Workspace isolation, verifier, integration approval |
| Operations | Monitoring/incident/runbook action | Environment scope and scoped change actions |

Each domain is developed as an agent package + domain service. Packages can utilize deterministic workflows, reserving LLMs strictly for analytical components. Domain owners finalize evaluations and resource budgets prior to activation.

## Proposed governance mapping
ISO/IEC 42001 maps to responsibility, lifecycle, and performance reviews. NIST AI RMF Govern/Map/Measure/Manage maps to registry/policy, threat/domain context, eval/metrics and incidents/releases. ISO/TC 258 PPM guidance maps to PPM baselines, benefits, portfolio and governance. Mappings operate at architectural levels; clause-by-clause assessment requires standard documentation and expert review.

OpenAI harness engineering and Anthropic long-running harness/evals provide principles for context, artifacts, feedback and evaluation. Specific package designs, contract versions and milestone gates represent proposals of this document, not vendor internal stack assertions.
