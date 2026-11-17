# Feature Specification: Submission Review Fixes

**Feature Branch**: `claude/vibrant-gates-qklwhp` (spec drafted here; implementation branches are chosen at planning, one goal per pull request where practical)

**Created**: 2026-11-17

**Status**: Draft, on hold. It waits for the ArcFace work (branch `claude/arcface-eval-set-2026-11-17`) to land on `main`; then Story 3, FR-015 to FR-022 and the non-goals are revised to build on it, and planning starts (decision of 2026-11-17, below).

**Input**: User description: "Mission Brief — Submission Review Findings" (the strict reviewer's grading of PR #16, 73/100)

## Clarifications

### Session 2026-11-17

- Q: Is the blind audit of the 2026-11-16 label set in scope? → A: Yes. Run it beside the frozen labels and report both models on frozen and audited labels side by side (FR-016, FR-016a).
- Q: With no default page cap, what happens to the 30-minute run-time target? → A: Restate it as a measured time per page and per image from the first default run (FR-030).
- Q: How do the host-side search commands learn the active model profile, so they match the pipeline's? → A: One profile value in the project's environment file, which Compose passes to the pipeline and the host commands read too; a process environment variable overrides it, and a mismatch with the index stops the search with a message (FR-018).
- Q: Which ArcFace variant's ranking feeds the blind audit's page pool? → A: Both: the union of the unlabelled top pages of the reported variant (original input scaling) and of the variant with ArcFace's own scaling, up to the reporting ceiling (FR-016).
- Q: Should the operator see when a search grows its neighbour batch or falls back to the full comparison? → A: Yes: one log line per search (final k, repeats, whether the full comparison ran), the same figures in the detailed output, and repeats per query in the evaluator's report; the normal result output is unchanged (FR-005a).
- Q: How much automated testing does the ArcFace profile get? → A: Fast tests on every pull request (profile selection, unknown-name rejection, threshold equal to the model library's published table), plus one real ArcFace embed-and-search case in the existing main-only real-model job (FR-019a).
- Q: Work on `claude/arcface-eval-set-2026-11-17` (found while planning) labels the 2026-11-17 set, pre-registers ArcFace's confirmation on it, moves the default to ArcFace with a threshold fitted on the label sets, and adds a re-index option. How does this feature relate to it? → A: Build on it. That branch is the base; once it is on `main`, this spec is revised so Story 3 covers only what it leaves open, re-indexing and labelling the second set are dropped from this feature, and the plan is written then.
- Q: The approximate index can miss a face within the limit, and no check on its output can catch that (Sourcery review of PR #18). What does the search promise? → A: Approximate, verified: identical results on the 2026-11-16 snapshot (checked, FR-005), a wide search breadth, and the approximation stated as a known limit in the README (FR-002, FR-005b).
- Q: How are ArcFace's threshold and default decided (Sourcery review of PR #17)? → A: The threshold is fitted on set 2026-11-16 only and read out of sample on set 2026-11-17; the default moves to ArcFace only if the pre-registered confirmation on 2026-11-17 holds. This relaxes, for that one fit and on the record, the rule that nothing is chosen from the 2026-11-16 set.

**Sources**: [assignment.md](../../docs/assignment.md) (authoritative), [submission-review.md](../../docs/briefs/submission-review.md) (mission brief: the review's findings, priorities and recommendations), [002-face-search spec](../002-face-search/spec.md) (the search, its threshold and its accuracy targets), [003-evaluation-set spec](../003-evaluation-set/spec.md) (the label sets and run log), [search-experiments.md](../../docs/briefs/search-experiments.md) (the pre-registered experiment grid). Where they disagree, the assignment wins.

**Assignment requirements served**: "Returns the pages containing images of the same character with high confidence." and "A short README including: … Your approach and architecture decisions."

## User Scenarios & Testing *(mandatory)*

The actors are the **reviewer** (grades the submission from the README and the code), the **operator** (runs the stack and the search), and the **maintainer** (runs experiments, labels pages and keeps the reports honest).

### User Story 1 - The search uses the index the design claims (Priority: P1)

A reviewer reads in the README that face search uses an approximate nearest-neighbour index, and checks the code. Today the search compares the query with every stored face, so the claim is false. After this story, a search reads the closest faces through the index first, then applies the same filters as today, and widens its reach whenever the closest faces may not be all the matches. The operator sees exactly the pages they saw before.

**Why this priority**: a design claim the code does not keep is the most damaging finding: it undermines every other claim in the README. It is also the only story that changes the search's behaviour, so it must land first and be proven not to change results.

**Independent Test**: on the 2026-11-16 snapshot, ask the database how it plans the search (it names the face index), and run the evaluator: every logged page and distance is reproduced exactly.

**Acceptance Scenarios**:

1. **Given** an indexed database, **When** the search's plan is inspected, **Then** it reads faces through the nearest-neighbour index rather than scanning every face.
2. **Given** the 2026-11-16 snapshot and its 10 query photos, **When** the evaluator runs, **Then** every page and every best distance equals the committed distance log; any difference fails the change.
3. **Given** a query whose number of matching faces exceeds the first batch of neighbours, **When** the search runs, **Then** it fetches a larger batch and returns every matching page, and logs the final batch size and the number of repeats; no result is silently cut off.
4. **Given** a query whose matches would need more neighbours than the index can return in one approximate search, **When** the search runs, **Then** it falls back to comparing with every face, logs that it did, and still returns every matching page.
5. **Given** the search's query text and its published contract, **When** either changes, **Then** both change in the same commit, and the existing check that they are equal passes.

---

### User Story 2 - Experiment results are reported honestly (Priority: P2)

A reviewer reads the experiment results and wants to know what the better candidate model (ArcFace) achieves on its own, what the baseline (Facenet512) achieves, and whether either passes the accuracy targets. Today the numbers are given as ranges that run from baseline to candidate, lead with a saturated metric, and the candidate was measured with the wrong input scaling. Two experiment arms also need correcting, and the labels carry a bias towards the baseline model that is not stated.

**Why this priority**: misreported numbers are the second-worst finding: they read as a result the experiment did not show. They cost no behaviour change, so they come straight after the search fix.

**Independent Test**: read the README and the evaluation document: each model has its own line, the line leads with recall at zero false matches, the "no single threshold passes" statement is there, and the added and corrected rows sit beside the unchanged pre-registered rows, each labelled.

**Acceptance Scenarios**:

1. **Given** the README and the evaluation document, **When** a reader looks for the candidate's result, **Then** they find its own numbers (AUC 0.999, recall at zero false matches 0.88 = 14 of 16 true pages, 5 of 5 people separable, 2 of 5 absent people below every true page) and the baseline's numbers on a separate line, with recall at zero false matches given first.
2. **Given** the same documents, **When** a reader asks whether ArcFace passes the accuracy targets, **Then** they read plainly that it does not at any single threshold: 2 true pages and 3 absent people still overlap.
3. **Given** the experiment harness, **When** the maintainer re-embeds with ArcFace's own input scaling, **Then** the result is written to a separate snapshot and reported as a new row marked "added after pre-registration, for information"; the existing snapshot and rows are unchanged and still reproduce.
4. **Given** the query-expansion arm, **When** it picks its neighbours, **Then** it skips neighbours that are renditions of the same photo (the same 0.02 distance rule the CSLS arm uses); the six re-scored rows (three scorings for each of the two models) are reported as corrections beside the original six, which stay as they were.
5. **Given** the "Labelling method and its limits" section, **When** read, **Then** it states that the 2026-11-16 labels were completed by reviewing the baseline model's matches and near misses, that some were edited after the first run, and that pages only the candidate ranks high may therefore be scored as wrong.
6. **Given** the evaluation-set brief, **When** a maintainer prepares the second label set, **Then** the method is already fixed: browse first with no model output in view, then review a pooled list of both models' top pages up to the reporting ceiling, and record each page's origin (browsing, baseline pool, candidate pool) in the methods file.
7. **Given** the frozen 2026-11-16 labels, **When** the maintainer runs the blind audit, **Then** the unlabelled top pages of both ArcFace variants, pooled into one list, are judged in shuffled order with no model name or distance shown, the verdicts go to a separate audit file, the labels file is unchanged, and both models are reported on frozen and audited labels side by side.

---

### User Story 3 - The better model is one setting away (Priority: P3)

A reviewer asks "why did you submit the version you knew was worse?". The answer is a setting: the operator chooses the ArcFace profile with one setting, starts a fresh index, runs the pipeline and searches, and the index and the search always use the same profile. The default stays Facenet512, and the README says why in one or two sentences.

**Why this priority**: it turns the experiment's finding into something the operator can use, without changing the default on a 10-person measurement. It depends on the honest numbers of Story 2 to be explained.

**Independent Test**: with the ArcFace setting in the Compose stack, from an empty database, run the pipeline and a search end to end; both use the ArcFace profile and its threshold. Without the setting, the default run and search are unchanged.

**Acceptance Scenarios**:

1. **Given** the profile setting is set to ArcFace and the database is empty, **When** the operator runs the pipeline and then the search, **Then** faces are stored under the ArcFace profile and the search queries only those faces, with ArcFace's own threshold (0.68).
2. **Given** the setting is not set, **When** the pipeline and the search run, **Then** they use Facenet512 and threshold 0.30, exactly as before.
3. **Given** an index built under one profile, **When** the search runs under the other, **Then** it finds no faces of the current profile and says so; it never compares embeddings from different models.
4. **Given** the README, **When** read, **Then** it shows how to switch profiles (wipe the volumes, set the setting, run), states that ArcFace's threshold comes from the model library's published table and is unvalidated on this site, and explains why the default is still Facenet512.

---

### User Story 4 - A short README a reviewer can navigate (Priority: P4)

A reviewer opens the README expecting the short document the assignment asked for. They find, in under 150 lines, each section the assignment names; a map saying where each deliverable lives and which folders are process records; current, dated example outputs; an honest statement of what a default run covers; and no internal identifiers.

**Why this priority**: the README is graded directly, but its content depends on the stories above (search design, honest numbers, profile switch, default coverage), so it is written after them.

**Independent Test**: count the README's lines, search it for internal identifiers, check each example output has a date and matches the current site set and index, and follow each link in the map.

**Acceptance Scenarios**:

1. **Given** the README, **When** its lines are counted, **Then** there are fewer than 150, and it still has: how to run, how to use the search script, libraries and models, approach and decisions, example usage and outputs.
2. **Given** the README, **When** searched for internal identifiers (success-criterion, requirement and task numbers, research item numbers, section references into spec documents), **Then** none are found; each is replaced by what it means.
3. **Given** the README's example outputs, **When** read, **Then** each is dated, none shows a disabled site, and none predates the current index; outputs that could not be refreshed are removed.
4. **Given** the README, **When** a reader looks for what a default run covers, **Then** one sentence says it indexes every page one link from each enabled site's home page (about 190 on 2026-11-17), not the whole site, how to widen it, and the measured time of the first default run.
5. **Given** the reviewer's map, **When** read, **Then** it names where the pipeline definition, the search script, the Compose file and each README section live, and says the specification, tooling and brief folders are process records, not the first code to read.

---

### User Story 5 - Secrets and versions are locked down (Priority: P5)

An operator inspects the generated database passwords and the database image. The password files are readable only by the accounts that need them, every reader still works, and a rebuild cannot silently change the database extension version the search depends on.

**Why this priority**: low-risk hygiene; it matters because Story 1's behaviour depends on the extension version, and world-readable passwords contradict the project's least-privilege principle.

**Independent Test**: list the secret files' owners and modes in the running stack; start the database, run the pipeline, run the documented host commands that read the reader password, and run the search; check the image reference in Compose and CI names a fixed version.

**Acceptance Scenarios**:

1. **Given** a fresh stack, **When** the secret files are listed, **Then** none is readable by other users, and each has the narrowest owner and mode its readers allow.
2. **Given** the tightened files, **When** the database starts, the pipeline connects as the writer, and the documented host commands read the reader password for the search, **Then** each succeeds.
3. **Given** the Compose file and the CI configuration, **When** read, **Then** both name the same fixed database image version.

---

### User Story 6 - A default run has no page cap (Priority: P6)

The owner decided on 2026-11-17 that a default run should not stop at 50 pages. An operator triggers the pipeline with no parameters, and it indexes every page within the default link depth. The README states the coverage, warns against raising the depth without a cap, and records the measured time and counts of the first such run.

**Why this priority**: an owner decision rather than a review finding; it changes default behaviour deliberately, and its measured run feeds Story 4's coverage sentence.

**Independent Test**: trigger the pipeline with no parameters; the crawl reports no page cap, the run completes, and its time and page, image and face counts are recorded in the README.

**Acceptance Scenarios**:

1. **Given** no run parameters, **When** the pipeline is triggered, **Then** the crawl has no page cap and stops only at the default depth.
2. **Given** a run with an explicit page cap, **When** triggered, **Then** the cap is honoured as before.
3. **Given** a default crawl of about 190 pages, **When** the crawl hands its page list to the next step, **Then** the hand-off stays within the orchestrator's size limit and the run does not fail on it.
4. **Given** the README's parameter table and known limitations, **When** read, **Then** the page cap default reads "unlimited", depth is described as the only default bound, raising depth without a cap is warned against (thousands of pages, days), and the time target is given per page and per image (FR-030).

---

### Edge Cases

- **More matching faces than the first batch**: the batch grows until its farthest face is at or beyond the search's distance limit; the search never returns a partial result without saying so.
- **Batch would exceed what one approximate search can return**: the search switches to comparing every face for that query only; results are complete, at today's cost.
- **The distance limit is a reporting ceiling, not the threshold**: the evaluator logs distances up to a ceiling above the threshold. The completeness rule uses whatever limit the caller passes, so the distance log is reproduced too.
- **Fewer faces than k**: a small index, or the last batch of a big one, returns fewer than k faces; the search stops there (FR-002) instead of asking for a larger batch forever.
- **Two pipeline runs with different profiles**: not supported. The pipeline runs one at a time, and switching profile starts from an empty index (FR-022), so two profiles never index the same image at once.
- **Neighbours removed by later filters**: faces that are too small, on images not yet indexed, or linked only from older crawls of a page are removed after the neighbour search. The completeness rule looks at the farthest neighbour before these filters, so filtering cannot hide a further match.
- **Faces of another model profile in the same database**: they must not take up places in the neighbour batch so that current-profile matches are pushed out; the plan must keep results complete when two profiles coexist (relevant once Story 3 exists, and after a library upgrade).
- **Exact ties at the batch boundary**: faces at equal distance across the batch edge must not be dropped; the grown batch includes them.
- **Approximate search misses a near face**: the index is approximate, so a face within the limit can be missed, and the completeness rule cannot notice it (it only sees what the index returned). Any page difference against the logged run on the snapshot is a bug; the fix is a wider approximate search, never a different threshold. On other indexes the approximation is a stated limit (FR-005b).
- **Index changes during one search**: every repeat of one search reads the same database snapshot (FR-002), so a crawl committing between repeats cannot mix two index states into one result.
- **Empty index or no current-profile faces**: the search reports no faces for the current profile, as today.
- **ArcFace setting with an existing Facenet512 index**: the search finds no ArcFace faces and says so; the README's switch steps start from wiped volumes. Converting an index is out of scope.
- **Shell override disagrees with the environment file**: the override wins for that command only; if the index holds no faces of the resulting profile, the search stops and names the profile, rather than returning an empty result.
- **Unknown profile name in the setting**: the pipeline and the search refuse to start, naming the allowed values; they never fall back silently.
- **Re-embedding with the new scaling**: writes a new snapshot file; it never overwrites the existing snapshot.
- **Default run at greater depth**: thousands of pages and days of work; the README warns, the pipeline does not block it.
- **Secret file reader that runs as a different user than expected**: found by running every listed reader after the change, not by assumption.

## Requirements *(mandatory)*

### Functional Requirements

**Search uses its index (Story 1)**

- **FR-001**: The search MUST first fetch the k nearest faces of the current model profile through the nearest-neighbour index, with the profile rule applied inside the index scan (the index's iterative scan, available from pgvector 0.8 and pinned at 0.8.7 by FR-032), so faces of another profile neither take places in the batch nor push it towards the full comparison. Only then MUST it apply the distance limit, the indexed-image rule, the latest-crawl rule and the minimum face size, and group by page as today. The scan's tuple limit MUST be set so that a batch shorter than k means the profile has no more faces, not that the scan gave up.
- **FR-002**: If the farthest of the k fetched faces is still within the distance limit, the search MUST repeat with a larger k until it is not, so that the index's batch is not cut off while it may still hold matches. It MUST stop as soon as a batch returns fewer than k faces, since the index then holds no more. All repeats of one search MUST run in one read-only transaction with a repeatable snapshot, so they read the same index state. This rule only extends the batch: it cannot detect a face the approximate index skipped (FR-005b).
- **FR-003**: When k would exceed what the approximate search can return in one pass, the search MUST run the exact comparison against every face of the current profile for that query.
- **FR-004**: The approximate search's breadth MUST be set to at least k inside the same explicit transaction as the search query, so it applies to that query and to nothing after it; the connection stays read-only.
- **FR-005**: On the 2026-11-16 snapshot, the search MUST return exactly the pages and best distances in the committed distance log for all 10 query photos; the number of repeats each query needed MUST be reported. A difference is fixed by widening the approximate search, never by changing the threshold.
- **FR-005a**: Every search MUST log one line with the final k, the number of repeats and whether the full comparison ran; the search script's detailed output MUST show the same figures, and the evaluator's report MUST give the repeats per query. The normal result output MUST NOT change.
- **FR-005b**: The README MUST state that the search is approximate: a face within the threshold can, rarely, be missed by the index; the search breadth is set wide (FR-004) and the results were checked against the exact search on the 2026-11-16 snapshot (FR-005).
- **FR-006**: An automated test MUST cover a query with more matching faces than the first k, and assert that every matching page is returned.
- **FR-007**: The search's query text and its published contract MUST change together in the same commit, and remain equal.
- **FR-008**: The search MUST stay read-only and keep connecting as the reader role.

**Honest experiment reporting (Story 2)**

- **FR-009**: The README and the evaluation document MUST give the candidate model's numbers and the baseline's numbers on separate lines, never as a range spanning both, and lead with recall at zero false matches.
- **FR-010**: Both documents MUST state that the candidate does not pass the accuracy targets at any single threshold, with the overlap (2 true pages, 3 absent people).
- **FR-011**: The experiment harness MUST accept an input-scaling choice, defaulting to today's so the existing snapshot still reproduces. The same choice MUST apply to the index faces and to the query photos of a scoring run, so both sides of a comparison use one scaling.
- **FR-012**: The candidate re-embedded with its own input scaling MUST be scored into a separate snapshot and reported as a new row, labelled as added after pre-registration and for information only. Snapshot files, their resumable parts and their lock MUST be named by model and input scaling, so a run with the new scaling can never resume from or overwrite the old one.
- **FR-013**: The query-expansion arm MUST skip neighbours closer than 0.02 to the query (renditions of one photo); its six rows (three scorings for each of the two models) MUST be re-scored and reported as corrections beside the unchanged originals.
- **FR-014**: The labelling-limits section MUST state the 2026-11-16 set's bias towards the baseline model and the post-run edits.
- **FR-015**: The evaluation-set brief MUST fix the second set's labelling method before it is labelled: browse first with no model output visible, then review the pooled top pages of both models up to the reporting ceiling, recording each page's origin (browsing, baseline pool, candidate pool) in a separate, validated provenance file next to the set. The existing methods file keeps its one row per set unchanged.
- **FR-016**: A blind audit of the 2026-11-16 set MUST be run beside the frozen labels: pool the pages that either ArcFace variant (the reported one with the original input scaling, and the one with ArcFace's own scaling from FR-012) ranks up to the reporting ceiling, together with the baseline's own unlabelled pages up to the same ceiling, that the original review never labelled, as one de-duplicated list (so the pool does not favour either model), show them to the labeller in shuffled order with no model name and no distance, and record one verdict per (person label, page URL) pair, since a page can show one pooled person and not another, in a separate audit file next to the set, with its own name and format that the folder validator accepts and checks. The labels file MUST NOT change.
- **FR-016a**: Both models MUST be re-scored on the frozen labels and on the audited labels, reported side by side as a sensitivity analysis. Scoring MUST refuse to run until every pooled (person label, page URL) pair has a verdict, naming the pairs still missing one. Nothing is chosen from the audited numbers.

**Opt-in candidate profile (Story 3)**

- **FR-017**: A second model profile (ArcFace) MUST exist beside the default (Facenet512), each carrying its own model, input scaling and threshold.
- **FR-018**: The pipeline and the search MUST read the active profile from one shared setting, so the index and the query always use the same profile: a single value in the project's environment file, passed by Compose to the pipeline and read by the host-side search commands (the search script and the evaluator). A process environment variable MAY override it. An unknown value MUST stop both with a message naming the allowed values, and a search whose profile has no faces in the index MUST stop with a message naming the profile. The pipeline and the search MUST also refuse to run when the index holds indexed images under another profile (a switch without wiping or re-indexing), naming both profiles, so a search never answers from a partial index.
- **FR-019**: The ArcFace threshold MUST be fitted on the 2026-11-16 set only and read out of sample on the 2026-11-17 set, and the default MUST move to ArcFace only if the pre-registered confirmation on 2026-11-17 holds (decision of 2026-11-17, Clarifications; it replaces the earlier rule of DeepFace's published 0.68, which stays reported for comparison). This is the one recorded exception to the rule that nothing is chosen from the 2026-11-16 set.
- **FR-019a**: Automated tests MUST cover, on every pull request and without downloading a model: profile selection from the setting, rejection of an unknown profile name, and each profile's threshold equal to the model library's published value. The existing real-model job (run on pushes to the main branch) MUST add one real ArcFace embed-and-search case.
- **FR-020**: The ArcFace profile MUST use the model's own documented input scaling; this is fixed from how the model was trained, not from any measured result.
- **FR-021**: With the setting unset, the default pipeline run and search MUST behave exactly as before this feature (apart from FR-028). In particular, the default profile's key MUST stay the same, so faces already indexed under Facenet512 keep matching.
- **FR-022**: The README MUST document switching profiles from a fresh index, and explain in one or two sentences why the default remains Facenet512.

**Short README with a reviewer's map (Story 4)**

- **FR-023**: The README MUST be under 150 lines and keep each section the assignment names; detail MUST move to the documentation folder.
- **FR-024**: The README MUST contain no internal identifiers; each is replaced with its plain meaning. Identifiers stay in the specification folder.
- **FR-025**: Every example output in the README MUST be dated and current (no disabled site, no figures predating the current index); outputs that cannot be refreshed MUST be removed.
- **FR-026**: The README MUST state the default crawl's coverage in one sentence, how to widen it, and the first default run's measured time.
- **FR-027**: A reviewer's map MUST say where each deliverable lives and that the specification, tooling and brief folders are process records.

**Default page cap (Story 6)**

- **FR-028**: A run triggered without parameters MUST have no page cap; the configuration default, the pipeline's parameter default, the README's parameter table and the tests MUST agree.
- **FR-029**: The crawl's page-list hand-off to the next step MUST stay within the orchestrator's size limit for a default run (about 190 pages), verified on a real default run. Every crawl MUST log its hand-off size, and one that exceeds the limit MUST fail the crawl task with a message naming the size, the limit and the remedy (a page cap or a lower depth), never fail later in the next step.
- **FR-030**: The README's 30-minute run-time target MUST be restated as a time per page (and per image), measured on the first default run; the old whole-run target is retired, since a default run with no page cap is expected to take 3 to 4.5 hours.

**Hygiene (Story 5)**

- **FR-031**: Each generated database password file MUST have the narrowest owner and mode that still lets its readers work; none MUST be readable by other users. Every reader MUST be run after the change.
- **FR-031a**: The Airflow containers MUST NOT hold the database superuser's password. Airflow's metadata database MUST get its own role that owns only that database, and the password file mounted into Airflow MUST hold only that role's, the writer's and the reader's credentials; the superuser's password stays with the database server and the host's administrative commands.
- **FR-032**: The database image MUST be pinned to a fixed version in both the Compose file and CI, the same in both.

**Delivery**

- **FR-033**: TODO.md MUST link the brief and close each item as it lands.

### Key Entities

- **Model profile**: a named pairing of face model, input scaling and match threshold. Faces are stored under one profile and searched only within it. Two exist after this feature: Facenet512 (default, 0.30) and ArcFace (opt-in, 0.68, unvalidated).
- **Neighbour batch**: the k closest current-profile faces to a query, read through the index; grown while its farthest face is within the distance limit.
- **Experiment row**: one scored combination of model and method; marked pre-registered, added after pre-registration, or correction of a pre-registered row.
- **Snapshot**: a local, never-committed export of index embeddings; one per model and input scaling.
- **Label audit**: blind verdicts on pages the original review never saw (FR-016), kept in its own file beside the frozen label set.
- **Reviewer's map**: a table from each assignment deliverable to its location.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The search's plan reads faces through the nearest-neighbour index, and on the 2026-11-16 snapshot all 10 query photos return 100% of the logged pages with identical best distances (0 differences).
- **SC-002**: An automated test with more matching faces than the first batch returns 100% of the matching pages.
- **SC-003**: In the README and the evaluation document, 0 numbers are given as ranges spanning both models; each model's line starts with recall at zero false matches; the "no single threshold passes" statement appears in both.
- **SC-004**: The input-scaling row and the 6 corrected query-expansion rows are reported, each labelled as added or corrected after pre-registration, and 0 original rows are changed.
- **SC-005**: The second set's labelling method is written down before labelling starts, and the limits section names the current set's bias. The audit file exists beside the unchanged 2026-11-16 labels, and both models are reported on frozen and audited labels side by side.
- **SC-006**: With the ArcFace setting, an empty stack indexes and searches end to end under the ArcFace profile and its documented threshold; without it, the default run and search give the same results as before, under the same profile key. The real-model job passes its ArcFace case.
- **SC-007**: The README has fewer than 150 lines, 0 internal identifiers, every example output dated and current, the coverage sentence, and a map covering every deliverable.
- **SC-008**: 0 generated secret files are readable by other users, 100% of their readers still work, and the database image version is the same fixed value in Compose and CI.
- **SC-009**: A default trigger crawls with no page cap and completes; the README records its measured time and its page, image and face counts.
- **SC-010**: Every item of the brief has a TODO.md entry linking it, closed when it lands.

## Assumptions

- **Pending revision (2026-11-17)**: Story 3 (FR-017 to FR-022: Facenet512 default, published 0.68 threshold), FR-015 (method fixed before the second set is labelled) and the re-index non-goal were written before the ArcFace branch was known. They stand until that branch lands, then are revised against it; Stories 1, 2, 4, 5 and 6 are expected to carry over, with the README rewrite done on top of that branch's README edits.
- **Starting point**: work starts from `main` after PR #16 (merge of `6922196`): README at 265 lines and the evaluation detail in `docs/search-evaluation.md`. This checkout's branch predates that merge (its README is longer and that document is absent); planning rebases onto `main` first.
- **Recommendations adopted from the brief (k)**: the first batch is k = 100 faces with the approximate breadth at twice k; k doubles while the farthest face is within the limit; once k would pass 500 (the breadth's cap of 1000), the exact comparison runs instead. These are verified on the snapshot (FR-005), not chosen from it: the number of matching faces is a property of the index, not a tuned result.
- **Profile filter and coexisting profiles**: if the database extension supports iterative index scans, the plan considers them for the profile filter; the version is checked when it is pinned (FR-032).
- **Input scaling for ArcFace is decided, not open**: the model's documented scaling is fixed now; the re-run with it (FR-012) is reported for information and does not choose the profile's scaling, since choosing it from the same 10 people would inflate the apparent gain.
- **Label freeze**: the committed 2026-11-16 labels are not edited by this feature; every reported number and the primary numbers going forward use them as they are.
- **No tuning on the 2026-11-16 set**: no threshold, k, face size or model default is chosen from its results.
- **Embeddings are never committed**: snapshots stay in the git-ignored snapshot folder.
- **One goal per pull request where practical**, Story 1 first, with CI green throughout.
- **The first default run** (about 3 to 4.5 hours) is run by the maintainer on the README's reference machine; its numbers fill FR-026 and SC-009.
- **Non-goals** (from the brief): changing the default model or threshold; converting an existing index to the ArcFace profile ([reindex-on-model-upgrade.md](../../docs/briefs/reindex-on-model-upgrade.md)); labelling the second set; deleting the specification, tooling or brief folders; enabling people.com; overlapping download and detection.
- **Technical terms**: this feature's subject is claims about how the system is built (an index, model profiles, input scaling, secret file modes), so the spec names those concepts; implementation choices (query shape, setting names, file modes) are left to planning.
