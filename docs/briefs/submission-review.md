# Mission Brief — Submission Review Findings

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: a reviewer's findings on PR #16 ("Prepare for submission", head `6922196`) against the assignment, 2026-11-17. Lands after PR #16.

**Status (2026-11-19):** partly done. Landed: goals 2, 4 and 8 (PR #17) and goal 10 (PR #19). Partly done: goal 5 (the label bias is documented, PR #17; the second set's labelling method and the blind audit are not done), goal 7 (plain words and dated example outputs, PR #17; the README is still 212 lines, and the coverage statement waits on goal 11) and goal 9 (password files owner-only, PR #19; the separate Airflow database role from spec FR-031a is not done). Not started: goals 1, 3, 6 and 11. Spec 004 is no longer on hold: PR #17 has landed, and revising the spec against it is the next step. The submission meets every deliverable on paper. It falls short on the search's "high confidence", on a design claim the code does not keep (the HNSW index), on how the experiments are run and reported, and on the README's length. This brief covers every finding; each goal below is one finding.

## Mission

> "Returns the pages containing images of the same character with high confidence."
> "A short README including: … Your approach and architecture decisions."

Fix what the review found, in priority order: claims the code does not keep come first, then experiment and reporting errors, then the opt-in better model, then the README, then hygiene. Nothing here changes the default model or the threshold on the strength of the 2026-11-16 label set.

## Goals

### P1 — Claims the code does not keep

1. **Make the search use its HNSW index.** [`search_query.sql`](../../src/face_index/search_query.sql) filters with `WHERE f.embedding <=> q < max_dist`. pgvector uses an HNSW index only for `ORDER BY embedding <=> q LIMIT k`, so today every search scans the whole `face` table. The README lists the HNSW index as part of the design.
   - Rewrite the query as nearest-neighbours first: the k closest faces of the current profile, ordered by distance with a limit. Apply the threshold, `status = 'indexed'`, the latest-crawl rule and `min_face` afterwards, then group by page as today.
   - **Completeness:** a person can have more than k matching faces (one image is on 47 pages, and renditions multiply faces). If the k-th neighbour is still below the threshold, repeat with a larger k (for example, double it) until it is not. Without this, results are silently cut off.
   - Check the server's pgvector version (the Compose image tag `pgvector/pgvector:pg17` floats). Set `hnsw.ef_search` at least as high as k for the query. If the server is 0.8 or later, consider `hnsw.iterative_scan` for the profile filter.
   - Update [search-query.md](../../specs/002-face-search/contracts/search-query.md) together with the SQL: `test_search_store.py` asserts the two are equal.

### P2 — Experiment and reporting errors

2. **Fix the misleading ArcFace numbers.** The README's "AUC 0.994 to 0.999, and true pages ranked above every wrong page 10 to 14 of 16" and the improvement log's candidate row give ranges that run from the baseline to the candidate. They read as ArcFace's own result.
   - Give the candidate its own numbers: AUC 0.999, recall at zero false matches 0.88 (14 of 16), 5/5 separable, 2/5 absent people below every true page. Give the baseline's numbers on their own line.
   - Lead with recall at zero false matches, not AUC. AUC is saturated by thousands of easy wrong pairs: the baseline already scores 0.994.
   - State plainly in the README and in [search-evaluation.md](../search-evaluation.md) that ArcFace does not pass SC-001 to SC-003 at any single threshold either: 2 true pages and 3 absent people still overlap.
3. **Re-run ArcFace with its own input normalization.** The experiment reused `model_profile.NORMALIZATION = "base"` for ArcFace. In DeepFace 0.0.104, `base` feeds the model pixels in [0, 1]. ArcFace's own `normalization="ArcFace"` gives (x − 127.5)/128, that is [−1, 1]. The candidate was measured off its training distribution.
   - Give `snapshot.ModelEmbedder` and `reembed.py` a normalization parameter (default unchanged, so the existing snapshot stays reproducible).
   - Re-embed to a separate snapshot file and score it as a new row, labelled as added after the pre-registered grid, not as a replacement. It is reported for information; it does not decide the profile's normalization (see Open Questions).
4. **Skip renditions in query expansion.** `scoring.expand` averages the query with its top 2 index faces. The CSLS arm skips neighbours closer than 0.02 (renditions of one photo), but query expansion does not, so its two "neighbours" are often one photo at two sizes.
   - Apply the same 0.02 skip in `expand` and re-score the four query-expansion rows.
   - Report them as a correction to the pre-registered grid, next to the original rows. Do not overwrite the original rows.
5. **Document and reduce the label bias.** The 2026-11-16 labels were completed by reviewing Facenet512's matches and near misses (`face-matcher --details`), and some labels were edited after the first run. Pages only ArcFace ranks high could be scored as wrong pages. The pre-registration covered the grid, not the labels.
   - Say this in "Labelling method and its limits".
   - For the second label set (already a TODO item), set the method in [evaluation-set.md](evaluation-set.md) before labelling: browse first, with no model's output in view. Then review a pooled candidate list: the union of both models' top pages up to the reporting ceiling. In `methods.csv`, record which pages came from browsing and which from each model's pool, so the bias can be measured.

### P3 — The better model, opt-in

6. **Make ArcFace a selectable profile; the default stays Facenet512.** The review's main question will be "why did you submit the version you knew was worse?" The answer should be a flag, not a TODO.
   - Define a second profile in `model_profile.py`. Both the pipeline and `face_matcher` read one setting (an environment variable in Compose), so query and index always share a profile.
   - **Threshold:** each profile carries its own. ArcFace's is DeepFace's `find_threshold("ArcFace", "cosine")` = 0.68, documented as unvalidated. Facenet512's 0.30 came from the same table, so this keeps the same rule. Do not derive it from the 2026-11-16 set.
   - **Populating it:** a fresh index under the ArcFace profile (`docker compose down -v`, then a run), documented in the README. Converting an existing index belongs to [reindex-on-model-upgrade.md](reindex-on-model-upgrade.md), not here.
   - In the README, explain in one or two sentences why the default is still Facenet512: the gain is measured on 10 people, and the held-out set decides.

### P4 — The README the assignment asked for

7. **Make the README short.** It has 265 lines, plus linked documents.
   - Aim for under 150 lines. Keep each assignment section (how to run, how to use the script, libraries/models, approach and decisions, example usage and outputs) and move detail to `docs/`.
   - **No internal identifiers** in the README: SC-xxx, FR-xxx, Txxx, "quickstart §3", research Rn. Say what they mean in plain words. The identifiers stay in `specs/`.
   - **Refresh stale example outputs.** The smoke output still shows people.com's 402 from before the site was disabled, and the summary tables predate the current index. Re-run them or remove them, and date what remains.
   - **State coverage honestly.** With the default `page_cap` now unlimited (goal 11), the default run indexes every page one link from the home page, about 190 on 2026-11-17, not the whole site. Say so in one sentence, with how to widen it (`depth`), and give the measured time of the first default run once it exists.
8. **Add a reviewer's map.** About 12,000 lines and a full spec process are a lot for a reviewer to walk into. Add a short table, at the top of the README or in `docs/`, saying where each deliverable lives: the DAG, the matcher, `docker-compose.yml` and the README sections. Say that `specs/`, `.specify/` and the briefs are process records, not code to read first.

### P5 — Hygiene

9. **Tighten the secret files' permissions.** The `db-secrets` service writes `postgres_password`, `writer_password` and `reader_password` with mode 644. Give each the narrowest owner and mode that still works for its readers: the Postgres server (it reads the file as the `postgres` user), the host commands in the README (`docker compose exec -T postgres cat …`, run as root) and `pgpass` (already 600). Check each reader after the change.
10. **Pin the pgvector image.** `pgvector/pgvector:pg17` floats, so a rebuild can change the server's pgvector version under goal 1. Pin a version tag in Compose and in CI.

### P6 — Owner decision

11. **Make the default `page_cap` unlimited** (decided 2026-11-17). Change `config.DEFAULT_PAGE_CAP` to `None`, along with the DAG param default, the README's parameter table, and the tests that assume 50.
    - `depth` becomes the only bound on a default run. At depth 1 that is about 190 pages, and the expected run is 3 to 4.5 hours on the machine in the README. At depth 2 it would be thousands of pages and days, so the README must say not to raise `depth` without setting a cap.
    - Check that the crawl task's XCom payload stays under Airflow's size limit at about 190 pages. At thousands of pages, the payload's existing compact list form may not be enough.
    - Download and detection run one after the other, so detection waits for every download, about 50 minutes at this size. Overlapping them is a possible later speed-up, not part of this goal.

## Revisions after review (2026-11-17)

The Sourcery reviews of PR #18 found places where this brief and spec 004 now disagree. Where they do, spec 004 holds the later decision:

- **Goal 1:** the nearest-neighbour batch is fetched with the profile rule inside the index scan (pgvector's iterative scan), not filtered afterwards, so other profiles' faces cannot shorten it (spec FR-001).
- **Goal 3:** the re-embedding path is `face_index.faces.DeepFaceEmbedder`, used by `reembed.py`; `snapshot.ModelEmbedder` no longer exists.
- **Goal 4:** six query-expansion rows are re-scored, not four (three scorings for each of the two models). Done on 2026-11-17: [search-evaluation.md](../search-evaluation.md).
- **Goal 5:** page origins go in a separate, validated provenance file next to the set; `methods.csv` keeps one row per set (spec FR-015).
- **Goal 6:** the ArcFace threshold is fitted on 2026-11-16 and read out of sample on 2026-11-17; the default moves only if the confirmation holds (spec FR-019).
- **Goal 9:** Airflow's password file also holds the superuser's password, for Airflow's own metadata connection; a separate Airflow role removes it (spec FR-031a).

## Constraints

- **No tuning on the 2026-11-16 set.** No threshold, k, face size or model default is chosen from its results. New experiment rows are reported as additions or corrections, beside the pre-registered ones.
- **Default behaviour is unchanged** except for the default `page_cap` (goal 11) and goal 1. Goal 1 must return exactly the same pages as today. The evaluator's logged run must reproduce, and any difference is a bug.
- **Search stays read-only** (`default_transaction_read_only = on`, the reader role).
- **Embeddings are never committed.** Snapshots stay under `eval-snapshots/` (gitignored), as in [search-experiments.md](search-experiments.md).
- **SQL and contract stay in step:** any change to `search_query.sql` updates the contract file in the same commit.
- **One goal per pull request** where practical, P1 first. CI stays green throughout.

## Non-Goals

- Changing the default model or threshold. That waits for the held-out label set.
- Converting an existing index to the ArcFace profile ([reindex-on-model-upgrade.md](reindex-on-model-upgrade.md)).
- Labelling the second set itself. This brief only fixes its method (goal 5).
- Deleting `specs/`, `.specify/` or the briefs. Goal 8 points reviewers past them.
- Enabling people.com.

## Success Criteria

1. `EXPLAIN` of the search query shows an index scan on `face_embedding_hnsw`. The evaluator, run against the 2026-11-16 snapshot, reproduces the logged distances in `queries/eval/distances.csv` exactly. A test covers the larger-k repeat when more than k faces match.
2. The README and `search-evaluation.md` give ArcFace's and the baseline's numbers separately, lead with recall at zero false matches, and say no single threshold passes with ArcFace either.
3. The ArcFace-normalization row and the corrected query-expansion rows are reported, labelled as added or corrected after pre-registration. The original rows are unchanged.
4. `evaluation-set.md` fixes the second set's labelling method (browse first, then a pooled review of both models), and the limits section names the current set's bias.
5. `FACE_MODEL_PROFILE=arcface` (or the chosen name) indexes and searches end to end in the Compose stack, with its own documented threshold. The default run and default search are unchanged.
6. The README is under 150 lines, contains no internal identifiers, has dated and current example outputs, states the crawl coverage, and maps each deliverable to its location.
7. The secret files are no longer world-readable, every reader still works, and the pgvector image is pinned.
8. A default trigger with no `--conf` crawls with no page cap, and the README records the first such run's measured time and page, image and face counts.
9. TODO.md links this brief and closes each item as it lands.

## Open Questions

- **k for goal 1: recommendation.**
  - **Start at k = 100, with `SET LOCAL hnsw.ef_search = 2·k`.** k counts faces, not pages: the join to `page_image` still returns every page a matching image is on, however many. So 100 faces is roughly 30 to 50 distinct photos at 2 to 3 renditions each, which covers almost anyone on 50 crawled pages. An `ef_search` of twice k keeps the approximate search's recall near 1 at this index size.
  - **Double while the k-th distance is below the threshold.** pgvector caps `hnsw.ef_search` at 1000, so once k would pass 500, run today's exact query instead. That guarantees completeness for heavily covered people, such as a head of state on a news site, at the old cost for those rare queries only.
  - **Verify, don't choose, on the snapshot.** Run the 10 query photos and report how many repeats each needed, and whether any page differs from the logged run. If a page differs, raise the `ef_search` multiplier, never the threshold. The count of matching faces is a property of the index, not a tuned result, so measuring it is allowed under the constraints.
- **Normalization for the ArcFace profile: decided before measuring, not open.** The profile uses `normalization="ArcFace"`, the model's documented preprocessing. That choice comes from how the model was trained, not from any result, so it is fixed now. Choosing between `base` and `ArcFace` from the goal 3 row would be one more comparison on the same 10 people that already picked ArcFace from 12 combinations: a data-dependent choice that inflates the apparent gain and contradicts the constraint above. The goal 3 row is reported for information only. If both variants are wanted, carry both to the held-out set and fix the decision rule before it is labelled.
- **Re-reviewing the 2026-11-16 set with ArcFace's pool: recommendation — audit it, don't edit it.**
  - **The committed labels stay frozen.** Every number already reported, and the primary numbers going forward, use them as they are. Editing them now, after ArcFace's results are known, repeats the label-after-results problem goal 5 describes.
  - **Run a blind audit beside them.** Pool the unlabelled pages that ArcFace ranks up to the reporting ceiling and that the Facenet512 review never saw. Show them to the labeller in shuffled order, with no model name and no distance, and record the verdicts in a separate audit file next to the set. The labels file is not changed.
  - **Report it as a sensitivity analysis.** Re-score both models, not only ArcFace, on the frozen labels and on the audited labels, side by side. If the ranking of the two models holds under both, the bias did not decide it. Nothing is chosen from the audited numbers.
  - **The real fix is the second set.** Its pooled method from goal 5 makes the labels symmetric by design, so the audit only answers how much the bias mattered here.
- **The 30-minute target:** with an unlimited default (goal 11), the target in the README's known limitations cannot be met at depth 1. Drop it, or restate it as time per page?
