# Prompt — adopt existing/deployed project
Copy snippet below for coding agent, replace PROJECT_ROOT. Provide along with standards, schemas, and templates from v2 suite.

You are adopting project at PROJECT_ROOT=<actual path>. Apply Project Management Contract 2.1 (compatible with 2.0) with minimal changes.
1. Read existing repository instructions. Inventory architecture/blueprint/implementation plan, Git HEAD/dirty state, and existing test/deployment documentation. Do not execute code in this step.
2. If .ppm exists, identify version and keep IDs; for unknown version create gap report, do not migrate blindly.
3. Create/adapt project.yaml and status.yaml, updates/, decisions/, acceptance.yaml per schema; map only existing files. Inferred goals and criteria are draft; missing left as null/record gap.
4. For deployed projects, separate lifecycle_reported and operation_reported; do not infer deployed from build config.
5. Create new initial update with UUID, actual recorded_at, describing adoption status; do not fabricate past history. If tests not run, mark not_run.
6. Merge guidance from prompt 03 into appropriate agent instruction file, preserving existing rules; avoid duplicate instructions.
7. Propose separate registry entry for owner to fill path/permissions. Do not put absolute paths or cloud permissions in .ppm.
8. Validate using provided script if dependencies available; if not available, report clearly instead of installing out-of-scope dependencies.
Do not modify functional code, do not commit/push/deploy during this adoption task.
General rules: work only within assigned scope; do not grant self additional permissions. Do not read secrets, do not send cloud data or run test/deploy solely for onboarding. Do not overwrite existing files; merge with verification. All reported claims must be honest; unknown/null if missing. Comply with Project Management Contract 2.1 (compatible with 2.0) in the provided suite. Do not fabricate formal acceptance. Finally report modified files, checks actually run, gaps, and next actions.
