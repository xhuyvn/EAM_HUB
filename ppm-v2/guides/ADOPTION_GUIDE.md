# Adoption Guide

## A. Active projects
1. Open correct repository with coding agent; use prompt 01 specifying PROJECT_ROOT.
2. Agent inventories docs/Git read-only first, preserving existing source and instructions.
3. Copy/adapt `templates/project/.ppm/`; replace project ID consistently, map real documents, missing left null.
4. Create initial update: describe current observed status; do not reconstruct past sessions.
5. Merge instructions snippet prompt 03 into active AGENTS.md/CLAUDE.md. If both exist, place contract in `.ppm/README.md`, add short guidance avoiding conflicting duplicate rules. Do not use symlinks on Windows merely to share instructions.
6. Owner registers location/scan policy in registry; do not let coding agent infer cloud permissions.
7. Run validator; owner reviews goals/criteria to establish official baseline later.

## B. Future projects
Use prompt 02 with idea/scope. Create draft goals and criteria before implementation. Missing metrics recorded as null/question. Do not create accepted baseline when only an idea exists. After each work package use prompt 04/05.

## C. Deployed projects
Record releases, deployment docs, and operation_reported. Missing deployment evidence results in reported running, not verified deployment. Onboarding does not rebuild/redeploy, does not run DB migrations. Create maintenance backlog and verification gaps. May close administratively per blueprint but never fabricate technical acceptance.

## D. Without Git
base_revision/result_revision=null; working_tree_state=not_git. Collector uses snapshot manifest/hash, does not fabricate SHA. Do not force Git init to achieve onboarding.

## E. Monorepo and multi-repo
Monorepo has separate subproject `.ppm/` dirs; location registry scoped to subdirectory. Multi-repo uses explicit location_id/ref, no relative traversal out of root. Program/portfolio relationships remain proposed until owner confirms.

## F. Day-to-day
Start: prompt 04. End: prompt 05. Review: prompt 06. Scope change: prompt 07. PPM agent implementation: prompt 08.
No PPM app required to start recording notes; before app deployment, scan via manual review prompt. Watcher and scan schedules operate only after collector is built, configured, and running.

## G. Migration
Preserve backups outside ingestion path or label with non-ingested suffix. Map v1 artifacts to artifact_mapping, add location_ref. Move classification/cloud_processing out of repo config to owner-controlled registry. Preserve project ID; record migration update and old/new contract version. If `.ppm/` exists, merge field by field, do not copy-overwrite. Unknown schema major must stop migration, still permitting read-only gap report.

## H. Adoption checklist
[ ] Stable and uniform ID across files.
[ ] Accurate mapping, paths do not escape root.
[ ] Incomplete goals/criteria labeled draft.
[ ] Initial update present without fabricated history.
[ ] Unrun checks recorded as not_run.
[ ] Location/permissions in separate registry.
[ ] No secrets or private paths in repo.
[ ] Merged instructions preserve old rules.
[ ] Validator passes; gaps visible to owner.
