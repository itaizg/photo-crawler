# Feature Specification: Search Evaluation Set

**Feature Branch**: `claude/vibrant-gates-qklwhp` (spec drafted here; the implementation branch is chosen at planning)

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "The mission brief regarding the evaluation set"

**Sources**: [assignment.md](../../docs/assignment.md) (authoritative), [evaluation-set.md](../../docs/briefs/evaluation-set.md) (mission brief: interpretation and recorded decisions), [002-face-search spec](../002-face-search/spec.md) SC-001 to SC-003 (the targets this set measures) and its [quickstart §7](../002-face-search/quickstart.md#7-accuracy-evaluation-sc-001-to-sc-003). Where they disagree, the assignment wins.

**Assignment requirement served**: "Returns the pages containing images of the same character with high confidence." Feature 002 states this as SC-001 (recall ≥ 0.80 per person), SC-002 (no look-alikes returned) and SC-003 (absent people return nothing). Those criteria cannot be checked until a hand-labelled set exists; this feature creates it.

## Clarifications

### Session 2026-11-16

- Q: The site changes hourly, so a contributor's later index will lack some labelled pages and contain new ones. How should the evaluation treat that drift? → A: Stay strict and keep a local snapshot. Labelled pages missing from the index are label errors and fail the run; the verdict is only valid against the labelling-date crawl. The labeller keeps a snapshot of that index locally, never committed (it holds face embeddings of real, unlicensed people), so the README numbers can be re-run. The README states the crawl date and how to re-label after drift.
- Q: Should the look-alike hard case be a person not on the site, or two people who are both on the site? → A: Both. Every label set has at least one absent look-alike; it also marks an on-site look-alike pair whenever its crawl contains one, and the README records when no pair was available.
- Q: How should results and labels build up over time? → A: Both ways. (i) A committed run log records every real-model run with the conditions that make it comparable. (ii) Labels accumulate as label sets, one per labelling crawl, and earlier sets are kept; each run evaluates one set. This extends the brief (goal 6 and the set's scope) by the user's decision of 2026-11-16.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Measure the search against a real labelled set (Priority: P1)

A contributor clones the repository, starts the project, runs one indexing pass, and runs the evaluation with one documented command. They get a per-person table (expected pages, returned pages, recall, extra pages, missed pages), the result for each absent person, and an overall PASS or FAIL against SC-001 to SC-003. Everything they need (labels, query photos, attribution) arrives with the clone; no secret, download, or private file is needed.

**Why this priority**: this is the mission. Without the set, the search's accuracy claims in feature 002 are unverified, and the README cannot report numbers.

**Independent Test**: on a fresh clone with an indexed site, run the documented evaluation command; it loads the committed set without error and prints the table and a PASS or FAIL verdict.

**Acceptance Scenarios**:

1. **Given** a fresh clone and a completed indexing run, **When** the contributor runs the documented evaluation command, **Then** the chosen label set (the latest by default) loads and the output shows one row per present person, one result per absent person, and PASS or FAIL.
2. **Given** a person whose labelled pages are indexed, **When** the search returns fewer than 80% of them, **Then** the output lists each missed page and the verdict is FAIL.
3. **Given** a look-alike in the absent rows, **When** the search returns any page for that photo, **Then** the verdict is FAIL.
4. **Given** a labelled page that is not in the index, **When** the evaluation runs, **Then** it reports that page as a label error and fails; it is not counted as a search miss and is not skipped.
5. **Given** a query photo in which no face is detected, **When** the evaluation runs, **Then** it reports the photo by path and fails.

---

### User Story 2 - Catch bad labels and a broken evaluator on every pull request (Priority: P2)

A contributor edits the labels, the attribution, the photos, or the evaluator, and opens a pull request. An automated check validates every label set and runs the evaluator end to end against a mock index with a deterministic fake face model. A malformed row fails the check with a message naming the row; the mock run must show PASS on a clean fixture and FAIL on fixtures with a planted miss and a planted look-alike, so the check proves the evaluator can fail.

**Why this priority**: labels rot quietly. Without an automatic gate, a typo in a URL or a lost photo turns into a wrong recall number nobody notices. It comes after P1 because it guards the set rather than delivering it.

**Independent Test**: open a pull request that adds a row with a non-canonical URL; the check fails and names that row. Open one with no label changes; the check passes in under 5 minutes without secrets.

**Acceptance Scenarios**:

1. **Given** a pull request that touches the labels, attribution, photos, or evaluator, **When** the check runs, **Then** it validates every label set and runs the mock evaluation, with no secrets and within 5 minutes.
2. **Given** a row with a missing column, a wrong type, a bad or non-canonical page URL, or a duplicate, **When** the label set is loaded, **Then** the whole load fails with a message naming the row's line number.
3. **Given** the clean mock fixture, **When** the evaluator runs, **Then** it reports PASS; **Given** the fixture with a planted miss (a person found on 1 of 2 pages) or a planted look-alike, **Then** it reports FAIL.
4. **Given** a page labelled for both a present person and an absent row in the same label set, **When** it is validated, **Then** validation fails.
5. **Given** fewer than 5 distinct present labels, fewer than 5 distinct absent labels, or a missing required hard case in a label set, **When** it is validated, **Then** validation fails and names the set and the minimum not met.

---

### User Story 3 - Commit photos lawfully and without naming people (Priority: P2)

A maintainer adds a query photo only if its licence allows redistribution, and records who took it and where it came from. The committed files and labels never name the person shown. The automated check rejects a photo with no attribution, an attribution with no photo, and a source link that could reveal the person's name.

**Why this priority**: committing photos is what makes P1 reproducible for everyone, and it is only acceptable if licensing and privacy hold. It ranks with P2 because a single bad photo blocks publishing the set.

**Independent Test**: add a photo without an attribution row, or an attribution row whose source link names a file rather than a numeric page ID; the check fails and names the path.

**Acceptance Scenarios**:

1. **Given** the committed photo folder and the attribution file, **When** validation runs, **Then** the set of attributed paths equals the set of committed photos exactly, and each attribution has a source link, the photographer, and an allowed licence.
2. **Given** an attribution whose licence is not one of public domain, CC0, CC BY, or CC BY-SA, **When** validation runs, **Then** it fails naming the row.
3. **Given** an attribution whose source link is not in the host's numeric page-ID form, **When** validation runs, **Then** it fails naming the row.
4. **Given** any committed label or photo file name, **When** reviewed, **Then** it uses a neutral label (`person-a`, `absent-a`) and never a real name.

---

### User Story 4 - Report the measured accuracy honestly (Priority: P3)

A reader of the README sees the evaluation results from a real-model run on a live index: per-person recall, extra pages, absent results, the verdict, how the pages were labelled (browse, search, or both; by whom; on which crawl date), and the known bias of that method. The quickstart's evaluation section points to the new label format.

**Why this priority**: the assignment's README must explain the approach; reporting measured accuracy with its limits completes the story, but depends on P1.

**Independent Test**: read the README section; it has the numbers, the labelling method, the crawl date, and a bias statement, and quickstart §7 references the new format.

**Acceptance Scenarios**:

1. **Given** a completed real-model evaluation run, **When** the README is updated, **Then** it contains the per-person table, the absent results, the overall verdict, the labelling method, and its known bias.
2. **Given** a real-model run made with the logging option, **When** it finishes, **Then** the run log gains one entry with the run's conditions and results; **Given** a mock run, **Then** nothing is logged.
3. **Given** the evaluation recommends a different threshold, **When** results are recorded, **Then** the recommendation is written down only; the default threshold is not changed by this feature.

---

### Edge Cases

- **Labelled page never indexed**: a label error. It is reported by URL and the run fails; it is not counted as a miss and not skipped (constitution XII).
- **Labelled page indexed but not returned**: a search miss. It is listed and counts against recall, which may legitimately fall to 0.80 and still pass.
- **No face in a query photo**: reported by path; the run fails.
- **Several faces in a query photo**: out of scope (each committed photo has one clear face). The evaluator searches the largest face, as today.
- **Page URL that is not canonical**: rejected at load, naming the row, because it would never compare equal to an indexed URL.
- **Same (person, page) row twice, or two people sharing one label**: rejected as a duplicate.
- **Same page labelled for a present person and an absent row**: rejected.
- **A present person with zero labelled pages**: rejected; every present label needs at least one page for recall to mean anything.
- **Old TOML labels file passed in**: rejected with a message saying the format is now CSV; the TOML format is not supported alongside.
- **Query photo taken from the indexed site, or a resized copy of one**: not allowed. Attribution must point to an outside host by numeric page ID, which excludes the indexed site as a source.
- **Index crawled on a later date than the labels**: pages have drifted. Missing labelled pages are label errors and the run fails, listing them; new pages showing a labelled person count as extra pages. The verdict is not comparable with the README's; the contributor re-labels (documented) or the labeller re-runs against the local snapshot.
- **An absent person appears on the site in a later crawl**: that absent label is left out of the later label set (a label's role never changes); the earlier set is unchanged.
- **No on-site look-alike pair in a crawl**: the set is still valid if its other hard cases are present; the README records that no pair was available.
- **A label set evaluated against an index from another crawl**: treated as drift (above); the run log records the set's crawl date, so such runs are not compared with matching ones.
- **Index built under a different face model profile**: the evaluator reports it and fails, as it does today.

## Requirements *(mandatory)*

### Functional Requirements

**The labelled set**

- **FR-001**: Labels MUST be organised as label sets, one per labelling crawl, each tagged with its crawl date. Adding a set MUST NOT change or remove earlier sets. Query photos and attribution are shared: a neutral label means the same person, with the same single photo, in every set, and its role (present or absent) never changes.
- **FR-001a**: Each label set MUST contain at least 5 distinct present labels, each with every indexed page URL of that crawl on which the person appears, one row per (person, page).
- **FR-002**: Each label set MUST contain at least 5 distinct absent labels, each a person who does not appear in that crawl, one row per absent label.
- **FR-003**: Each label set MUST include hard cases, each marked in its row: at least one absent look-alike of a present person, and at least one page where a present person's face is small. When the crawl contains two present people who resemble each other, the set MUST also mark them as an on-site look-alike pair (the direct SC-002 stress of feature 002).
- **FR-004**: Labels MUST be neutral (`person-a`, `absent-a`); real names MUST NOT appear in any committed label, file name, or source link.
- **FR-005**: Photo paths in a label set MUST be relative to the label sets' folder, so sets share photos.
- **FR-006**: Every labelled page URL MUST already be in the crawler's canonical form; a URL that canonicalization would change MUST be rejected.
- **FR-007**: A present person's query photo MUST NOT be one of the indexed images or a rendition of one.
- **FR-007a**: Each evaluation run MUST evaluate exactly one label set, chosen by the contributor; the latest set is the default.
- **FR-008**: The label sets MUST replace the existing TOML labels format; the evaluator MUST NOT accept both.

**Loading and validation**

- **FR-009**: Each label set MUST be read with every column's type stated explicitly (no type inference) and converted into the evaluator's existing validated record types in one place, at load time.
- **FR-010**: Any invalid row (missing column, wrong type, bad or non-canonical URL, duplicate row, page shared between a present and an absent label, present label without pages, label used as present in one set and absent in another) MUST fail the whole load with a message naming the row's line number. No partial set is evaluated.
- **FR-011**: Validation MUST check the minimum counts (FR-001a to FR-003) per label set, over distinct labels, and name the set and each minimum that is not met.

**Licensing and attribution**

- **FR-012**: A query photo MUST be committed only under public domain, CC0, CC BY, or CC BY-SA.
- **FR-013**: The repository MUST contain an attribution table with one row per committed photo, keyed by photo path, giving the source link, the author (the photographer), and the licence.
- **FR-014**: The set of attributed paths MUST equal the set of committed photos exactly; an unattributed photo, an attribution without a photo, or a photo no label set uses MUST fail validation, naming the path.
- **FR-015**: Source links MUST use the host's numeric page-ID form (Wikimedia Commons `curid`, Flickr or Unsplash photo ID), never a URL or file name that names the person; any other form MUST fail validation.

**Evaluation behaviour**

- **FR-016**: The evaluator MUST keep using the search's own code at the default threshold; no label set carries its own threshold.
- **FR-017**: The evaluator MUST report, per present person: expected pages, returned pages, recall, extra pages, and every missed page; per absent label: the number of pages returned; and an overall PASS or FAIL against SC-001 to SC-003 of feature 002.
- **FR-018**: A labelled page that is not in the index MUST be reported as a label error and MUST fail the run; it MUST NOT be counted as a search miss or skipped.
- **FR-019**: A query photo with no detected face MUST be reported by path and MUST fail the run.
- **FR-020**: The evaluation MUST be runnable after clone, project start and one indexing run with one documented command.

**Automated check**

- **FR-021**: An automated check MUST run on every pull request that touches the labels, attribution, query photos, or evaluator, and MUST validate every label set and the run log (FR-009 to FR-015, FR-027).
- **FR-022**: The check MUST then run the evaluator end to end against a seeded mock index with a deterministic fake face model, never the real model, and need no secrets.
- **FR-023**: The mock fixtures MUST include a clean case that PASSes, a planted miss that drops a person's recall below 0.80 (for example 1 of 2 pages), and a planted look-alike; the check MUST require FAIL for the latter two.
- **FR-024**: The check MUST follow the project's existing automation conventions (pinned action versions, locked dependency install, read-only permissions, concurrency group, job timeout) and finish within 5 minutes.

**Documentation**

- **FR-025**: The labelling method MUST be written down: how pages were found (browse, search, or both), by whom, and the crawl date the labels refer to.
- **FR-025a**: The labeller MUST keep a snapshot of the labelling-date index locally so the recorded results can be re-run. The snapshot MUST NOT be committed or published, since it holds face embeddings of people who gave no licence; the repository records only that it exists, its crawl date, and who holds it.
- **FR-026**: The README MUST summarise the latest real-model evaluation results (pointing to the run log for history), the labelling method, its known bias, the crawl date the results are tied to, and how to re-label after the site drifts. Quickstart §7 and the evaluator's usage text MUST describe the new format.

**Run log**

- **FR-027**: On request, a real-model evaluation run MUST append one entry to a committed run log with: run date, label set (crawl date), labels version, model profile, threshold, index size (faces, images, pages), per-label recall, extra pages, absent matches and label errors, and the verdict. A FAIL is logged like a PASS.
- **FR-028**: Mock runs MUST NOT be logged. Log entries MUST use neutral labels and counts only, adding no page URLs, names or face data. Existing entries MUST NOT be rewritten.

### Key Entities

- **Label set**: the labels for one labelling crawl. Attributes: crawl date, label rows. Sets accumulate; earlier sets are kept unchanged.
- **Present label**: a neutral label (`person-a`) for a person who appears on the indexed site. Attributes: one query photo path (shared across sets), labelled pages per set, hard-case mark per row.
- **Absent label**: a neutral label (`absent-a`) for a person not on the site. Attributes: one query photo path (shared across sets), hard-case mark (set for look-alikes).
- **Look-alike pair**: two present labels in one set marked as resembling each other; returning one's page for the other is an extra page.
- **Label row**: one (present label, page) pair or one absent label; the unit validated and reported by line number.
- **Query photo**: an openly licensed photo with one clear face, committed under the neutral label's name, not taken from the indexed site.
- **Attribution row**: photo path (key), source link in page-ID form, author, licence.
- **Labelling method record**: how pages were found, by whom, and the crawl date.
- **Evaluation report**: per-person rows, per-absent results, label errors, and the overall verdict (defined by feature 002; extended here with label errors).
- **Run log entry**: one real-model run: its conditions (run date, label set, labels version, model profile, threshold, index size) and results (per-label counts, label errors, verdict).
- **Mock fixture**: a seeded index and fake-model photos used by the automated check, in clean, planted-miss, and planted-look-alike variants.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every committed label set has at least 5 distinct present labels, at least 5 distinct absent labels, an absent look-alike and a small-face page (plus an on-site look-alike pair, or a README note that its crawl had none), and loads into the evaluator with zero errors.
- **SC-002**: Each of these malformed rows fails the automated check with a message that names the row: missing column, wrong type, bad URL, non-canonical URL, duplicate row. 5 of 5 are caught.
- **SC-003**: In the automated check, the mock run reports PASS on the clean fixture and FAIL on both the planted miss and the planted look-alike; the check needs no secrets and finishes in under 5 minutes.
- **SC-004**: After cloning, starting the project, and one indexing run, a contributor runs the evaluation with one documented command and gets the per-person table and a PASS or FAIL against feature 002's SC-001 to SC-003, with no additional files or secrets. On an index crawled after the labelling date, drifted pages are listed as label errors and the run fails; a PASS is only promised against the labelling-date crawl (re-run from the labeller's local snapshot).
- **SC-005**: 100% of committed photos have exactly one attribution row matched by path, with an allowed licence and a page-ID source link; the check rejects an unattributed photo, an attribution without a photo, and a source link not in page-ID form (3 of 3).
- **SC-006**: The README contains the measured results, the labelling method (including crawl date), its known bias, and the re-labelling steps, and quickstart §7 points to the new format.
- **SC-007**: The run log has one entry for every real-model run recorded in the README, each with all FR-027 fields, and none for mock runs; the automated check rejects an entry with a missing field.

## Assumptions

- **Recorded decisions (brief)**: the labels and attribution are CSV tables, so changes are reviewable in a diff; they are read with pandas declared as a direct dependency pinned to the Airflow constraint (`pandas==3.1.0`), adding no new package to the lock. Rows become the evaluator's existing Pydantic models at load. The automated check is a GitHub Actions job using `FakeEmbedder` and a seeded pgvector service, as the integration tests do. Photos are committed under `queries/eval/`.
- **Git-ignore exception**: `queries/` is git-ignored except its README today; this feature adds an exception for `queries/eval/` (labels, attribution, photos) and keeps every other query photo ignored.
- **Hard cases**: an absent look-alike and a small-face page are always achievable; an on-site look-alike pair depends on what the crawl contains, so it is required only when one exists. All are marked in a hard-case column so they can be counted.
- **Scope extension (user decision, 2026-11-16)**: accumulated label sets and the run log go beyond the brief. They serve its goal 6 ("Record the results") and the assignment's "high confidence" requirement by making results comparable over time; no service, UI or threshold change is added.
- **A FAIL does not block delivery**: a real-model FAIL is recorded in the log and README like a PASS; acting on it (threshold, face-size filter) belongs to [noisy-index-faces.md](../../docs/briefs/noisy-index-faces.md).
- **One face per photo**: each committed photo shows one clear face; multi-face queries stay with [multi-face-queries.md](../../docs/briefs/multi-face-queries.md).
- **Real-model run is local**: the README numbers come from a contributor's local run on a live index; the automated check never downloads the model.
- **Reproducibility is tied to the crawl date**: the site changes hourly, so the committed set is valid for one crawl. A committed database snapshot was rejected (biometric data of unlicensed people, data derived from copyrighted images, skips the pipeline, stale on model upgrade); the snapshot stays local with the labeller.
- **Labelling bias is accepted and stated**: recall is only as good as the labels; pages a labeller did not find are invisible to the measurement, and the README says so.
- **Out of scope** (per the brief): changing the threshold or adding a face-size filter (a threshold may only be recommended, see [noisy-index-faces.md](../../docs/briefs/noisy-index-faces.md)), a labelling UI, covering every person on the site, and running the real face model in automation.
- **Dependency**: feature 002 (face search and `scripts/evaluate_search.py`) is merged, and the crawler's canonical URL function is the reference for page URLs.
- **Not verified in this environment**: the team-ai-directives knowledge base path in `.specify/init-options.json` is a local path not present where this spec was drafted, so team discovery did not run; the project constitution (which inherits it) was applied.
