# Team Context: Submission Review Fixes

**Discovered**: 2026-11-17 (before_specify, `team-discover`)
**Knowledge base**: `/Users/itaizemah-goren/team-ai-directives` (CDR index, 34 entries)

## Discovered Team Context

| ID | Module | Type | Descriptor | Relevance |
|----|--------|------|------------|-----------|
| constitution | context_modules/constitution.md | Constitution | Core principles governing all AI agent behavior and team interactions | High |
| CDR-2026-007 | context_modules/personas/senior_python_developer.md | Persona | Python, data/ML, testing specialist | High |
| CDR-2026-021 | context_modules/rules/security/sql_injection_prevention.md | Rule | Preventing SQL injection across languages | High (goal 1 rewrites the search SQL; keep it parameterised) |
| CDR-2026-027 | context_modules/rules/testing/python/pytest_patterns.md | Rule | Writing and organising Python tests with pytest | High (larger-k repeat test, page_cap tests) |
| rule-rules-orchestration-airflow_dag_patterns | context_modules/rules/orchestration/airflow_dag_patterns.md | Rule | Standard Airflow DAG patterns | Medium (goal 11: DAG param default, XCom size) |
| CDR-2026-020 | context_modules/rules/security/pre_commit_checklist.md | Rule | Pre-commit security checklist | Medium (goal 9: secret file permissions) |
| CDR-2026-011 | context_modules/rules/devops/github_actions.md | Rule | GitHub Actions workflow standards | Medium (goal 10: pin the pgvector image in CI) |
| CDR-2026-022 | context_modules/rules/style-guides/file_organization.md | Rule | File organisation and sizing | Low (goal 7: move README detail to docs/) |
| rule-rules-devops-secrets_management | context_modules/rules/devops/secrets_management.md | Rule | Kubernetes External Secrets patterns | Low (Compose, not Kubernetes) |

Skills: none of the registry's default or external skills match (they cover Helm, Crossplane, dbt, GKE, React). `evaluation` / `advanced-evaluation` (external) are LLM-evaluation skills, not face-search evaluation.

_Searched 34 CDR entries and the skills registry; 9 modules matched (4 High)._

## Key directives for this feature

- **Constitution 3 / project XII**: surface every failure; the larger-k fallback must never silently truncate results.
- **Constitution 4, 9 / project XIII–XIV**: least privilege for the secret files (goal 9); the search keeps the read-only reader role.
- **Constitution 5 / project IV**: a test must cover the larger-k repeat; the search's equality with the logged run is the regression check.
- **Constitution 11–12 / project V, XVIII**: surgical changes, one goal per pull request.
- **SQL injection rule**: the rewritten query keeps named parameters; `k` and `ef_search` are passed as parameters or validated integers, never string-formatted from input.

### Changes from Previous Discovery

- 2026-11-17 (before_plan): no changes. Same 34-entry index, same 9 matches and relevance.
- 2026-11-17 (before_specify): first discovery for this feature (feature 003 noted the knowledge base was unavailable).
