# Instructions snippet for AGENTS.md or CLAUDE.md
This is content for owner/authorized coding agent to merge into existing guidance. Not a Skill file and requires no plugin installation.

## Project management reporting — PPM 2.0
- Read .ppm/project.yaml, status.yaml, acceptance.yaml and relevant updates before executing a work package.
- Use goals/criteria to preserve scope; if missing or contradictory state gap, do not modify baseline unilaterally.
- After each session with significant work, create .ppm/updates/<UUID>.yaml per schema. Do not modify old updates; correction via supersedes_update_id.
- Record completed/in_progress, checks run/not run, blockers, and next_actions. Do not fabricate results, SHA, timestamp, or evidence. Attach `criterion_ids`/`test_ids` when known (contract 2.1). Reports written to repo by agents are claims only; PPM verifies using verifier or trusted CI.
- Each agent writes its own file; only designated writer updates status.yaml when there is no conflict.
- Do not mark accepted/deployed/benefit realized from checklists or test passes autonomously.
- Keep metadata portable, no secrets/absolute host paths. Permissions and cloud policy are determined by central registry.
- Do not commit/push/deploy or modify scope solely to update PPM if not part of task.
- Report clearly if session terminates abruptly or cannot write update; subsequent session writes recovery update with observed information.

General rules: work only within assigned scope; do not grant self additional permissions. Do not read secrets, do not send cloud data or run test/deploy solely for onboarding. Do not overwrite existing files; merge with verification. All reported claims must be honest; unknown/null if missing. Comply with Project Management Contract 2.1 (compatible with 2.0) in the provided suite. Do not fabricate formal acceptance. Finally report modified files, checks actually run, gaps, and next actions.
