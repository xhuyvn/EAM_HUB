# Prompt — end session
Hand over work for this session according to PPM 2.0.
1. Identify diff and work actually completed; do not attribute changes from other actors as accomplishments of this session.
2. Generate unique update_id/session_id, timestamp with timezone using actual clock; revision only taken from Git when available. If unknown, set null.
3. Record completed/in_progress, exact check result (not_run if not run), real evidence path or null, blockers, and next actions. When known, record `criterion_ids` (IDs present in acceptance.yaml) and `test_ids` of check; use `contract_version: '2.1'` when using these fields. Report files you create in repo are only claims with files, not verified evidence.
4. Work lacking evidence remains a reported claim. Do not conclude verified or accepted autonomously.
5. If correcting past report, use new update with supersedes_update_id and reason; do not edit/delete old files.
6. Write new file via temp then atomic rename if tool supports; ignore temp during ingest. If status has another writer, leave untouched and report needs_reconciliation.
7. Validate structure when tools available. Do not create commit solely to get SHA for update; code commit before update or null revision are both valid.
Return short handover: done, checks, blockers, next, update path.
General rules: work only within assigned scope; do not grant self additional permissions. Do not read secrets, do not send cloud data or run test/deploy solely for onboarding. Do not overwrite existing files; merge with verification. All reported claims must be honest; unknown/null if missing. Comply with Project Management Contract 2.1 (compatible with 2.0) in the provided suite. Do not fabricate formal acceptance. Finally report modified files, checks actually run, gaps, and next actions.
