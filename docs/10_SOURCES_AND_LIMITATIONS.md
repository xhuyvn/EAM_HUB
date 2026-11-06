# Sources, decisions and verification limits
Web verification date 05/11/2026. Public sources establish capabilities and principles; specific SDK and auth details require compatibility spikes during implementation.

| ID | Authoritative source | Application |
|---|---|---|
| S1 | https://openai.com/index/harness-engineering/ | Repo knowledge, environments and feedback loops |
| S2 | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents | Incremental sessions, initializers and progress artifacts |
| S3 | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents | Eval tasks/trials/graders and lifecycle |
| S4 | https://platform.claude.com/docs/en/managed-agents/session-observability | Session events, usage and observability |
| S5 | https://platform.claude.com/docs/en/managed-agents/permission-policies | Custom-tool enforcement boundary |
| S6 | https://docs.langchain.com/oss/python/langgraph/persistence | Checkpointers and long-term stores |
| S7 | https://www.iso.org/standard/42001 | Organization AI management system |
| S8 | https://airc.nist.gov/airmf-resources/airmf/5-sec-core/ | Govern/Map/Measure/Manage |

Proprietary user source materials were loaded from three ZIP archives in SOURCE_MANIFEST.json. The current PPM v2 blueprint is bundled with an integration notice. Secretary and Enterprise Harness sources were synthesized per the reconciliation table, avoiding legacy roadmaps for phase delivery.

Package validation checks documentation, examples/schemas, selected invariants and ZIP checksums. Model quality, HUD latency, live SDK compatibility, runtime security and deployment resilience remain unmeasured. Targets represent proposals. Scope decisions finalized by the design comprise modular monolith, LangGraph/PostgreSQL, PPM-first pilot, read-only/managed certification and Phase 2 optimization.
