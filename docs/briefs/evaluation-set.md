# Mission Brief — Search Evaluation Set

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [spec 002](../../specs/002-face-search/spec.md) SC-001 to SC-003 and [quickstart §7](../../specs/002-face-search/quickstart.md#7-accuracy-evaluation-sc-001-to-sc-003). Lands after feature 002.

## Mission

> "Returns the pages containing images of the same character with high confidence."

`scripts/evaluate_search.py` can measure the search, but there is nothing to measure it against: the hand-labelled set it expects does not exist yet. This mission builds that set as CSV tables that pandas reads, one label set per labelling crawl, commits it with openly licensed photos so anyone who clones the repo can run it, and adds a GitHub Actions job that checks the set and the evaluator on every pull request.

## Goals

1. **Label people on the site**: in each label set, at least 5 people, each with a query photo not taken from the indexed site and every page URL of that crawl they appear on.
2. **Label absent people**: in each label set, at least 5 distinct people who are not in that crawl, each with a neutral label (`absent-a`) and one query photo.
3. **Include hard cases**: in each label set, an absent look-alike of a labelled person and a page where a person's face is small; also an on-site look-alike pair (two labelled people who resemble each other) whenever the crawl has one, so SC-002 is actually stressed.
4. **Store labels as CSV, one label set per crawl**: one row per (person, page) and one row per absent label, read with pandas into the evaluator's existing Pydantic models. Each set is tagged with its crawl date; new sets are added and earlier sets are kept unchanged. A run evaluates one set, the latest by default. The TOML format is replaced, not kept alongside.
5. **Check it in CI**: a GitHub Actions job validates the table and runs the evaluator end to end against a mock, on every pull request that touches either.
6. **Record the results**: run the evaluation on a live index and put the numbers, the labelling method and its bias into the README.
7. **Log every real-model run**: append each run to a committed run log, with the conditions that make runs comparable, so results can be followed across code changes and crawls.

## Constraints

- **pandas as the reader**: declared as a direct dependency in `pyproject.toml`, bounded to the Airflow constraint pin (`pandas==3.1.0`). Today it only arrives through deepface, which must not be relied on; the lock gains no new package. Read with explicit `dtype`s; no type inference.
- **Validate once at the boundary**: rows become Pydantic models when read; a bad row fails the whole load with its line number. No scattered try/except.
- **Openly licensed photos only**: a query photo is committed only under a licence that allows it (public domain, CC0, CC BY, CC BY-SA). Each one has a row in an attribution file keyed by its photo path: path, source URL, author, licence. The attribution paths must equal the committed photos exactly. Anything else stays out of the repo.
- **Neutral labels**: committed labels use `person-a` and `absent-a`, never real names. One query photo per label, shared by every label set: a label means the same person in every set and never changes role (present or absent). An absent person who later appears on the site is left out of that later set. Minimum counts are per set, over distinct labels. Photo paths are relative to the label sets' folder.
- **Canonical page URLs**: labelled pages must be in the crawler's canonical form, as `normalize_page_url` (`src/face_index/urls.py`) returns it, so they compare equal to indexed URLs. A label that changes under it is rejected.
- **Independent query photos**: a person's query photo must not be one of the indexed images or a rendition of one.
- **Labelling method is written down**: how pages were found (browse, search, both), by whom, and on which crawl date, since recall is only as good as the labels.
- **Results are tied to the crawl date**: the site changes hourly, so a later index lacks some labelled pages and has new ones. A labelled page missing from the index stays a label error that fails the run; nothing is rescored. The labeller keeps a snapshot of the labelling-date index locally, never committed, so the recorded numbers can be re-run. The README states the crawl date and how to re-label after drift.
- **Run log**: one entry per real-model run, written on request by the evaluator and committed by a person: run date, label set (crawl date), labels version, model profile, threshold, index size (faces, images, pages), per-label recall, extra pages, absent matches and label errors, and the verdict. A FAIL is logged like a PASS. Mock runs are never logged. Entries hold neutral labels and counts only (no page URLs, names or face data) and are never rewritten.
- **No silent skips**: a photo where no face is detected, or a labelled page that was never indexed (a label error), is reported and fails the run (constitution XII). A page that is indexed but not returned is a search miss: it is listed and counts against recall, which may drop to 0.80 per SC-001.
- **CI with a mock, not the model**: the job checks every label set and the run log for schema and consistency (columns, types, URL format, no duplicate rows, minimum counts per set, no page shared by a person and an absent row, no label changing role between sets, no log entry missing a field). It then runs the evaluator against a seeded pgvector service with `FakeEmbedder` (`tests/fakes.py`), as the integration tests already do. The fixture must include a planted miss that drops a person's recall below 0.80 (for example 1 of 2 pages) and a planted look-alike, so the job proves the evaluator can FAIL, not only PASS (constitution IV).
- **Existing CI style**: pinned action SHAs, `uv sync --locked`, `permissions: contents: read`, `concurrency` group, a job timeout.
- **Same search code**: the evaluator keeps calling `search_faces` at the default threshold; the set does not get its own threshold.

## Non-Goals

- Running DeepFace in GitHub Actions; the mock covers the evaluator's logic, and the real-model run is local.
- Changing the threshold or adding a face-size filter. The evaluation may recommend a threshold in its report; acting on it belongs to [noisy-index-faces.md](noisy-index-faces.md).
- Multi-face query photos; each photo has one clear face (see [multi-face-queries.md](multi-face-queries.md)).
- A labelling UI or annotation tool; a spreadsheet exported to CSV is enough.
- Covering every person on the site; each label set is a sample.
- Committing or publishing an index snapshot; it stays local with the labeller.

## Success Criteria

1. Every committed label set has at least 5 distinct present labels, 5 distinct absent labels, an absent look-alike and a small-face page (plus an on-site look-alike pair, or a README note that its crawl had none), and loads through pandas into the evaluator without error.
2. A malformed row (missing column, bad or non-canonical URL, duplicate, wrong type) fails CI with a message naming the row.
3. In CI, the mock run reports PASS on the clean fixture and FAIL on the planted miss and look-alike; it needs no secrets and finishes in under 5 minutes.
4. After cloning, `docker compose up` and one indexing run, a contributor runs `scripts/evaluate_search.py` with one documented command and gets the per-person table and PASS or FAIL against SC-001 to SC-003. On an index crawled after the labelling date, drifted pages are listed as label errors and the run fails; a PASS is only promised against the labelling-date crawl.
5. Every committed photo has exactly one attribution row, matched by path, with a licence that allows redistribution and a page-ID source URL; CI rejects an unattributed photo, an attribution without a photo, and a source URL not in page-ID form.
6. The README records the latest results, the labelling method, its known bias, the crawl date and the re-labelling steps, and quickstart §7 points to the new format.
7. The run log has one entry for every real-model run the README reports, each with every field, and none for mock runs.

## Decisions

- **Format**: CSV, so label changes are reviewable in a diff.
- **Where photos live** (recommended): committed under `queries/eval/`, openly licensed with attribution. A contributor gets the full set by cloning, and CI needs no secrets. An encrypted artifact was rejected: it needs a secret that fork pull requests cannot read, and it adds key management for no gain over licensed photos.
- **Hard cases**: included in every set. An absent look-alike is always achievable (the photo is chosen); an on-site pair depends on the crawl, so it is required only when one exists.
- **Label sets accumulate** (decided 2026-11-16): a set per labelling crawl, earlier sets kept, so people and hard cases build up over time. A single set replaced on each re-labelling was the smaller option; accumulating was chosen for a more diverse set.
- **Run log** (decided 2026-11-16): committed, so results stay comparable across code, threshold and crawl changes, and serve as evidence for [noisy-index-faces.md](noisy-index-faces.md).
- **Index drift** (decided 2026-11-16): strict, with a local snapshot. Scoring only the pages still indexed was rejected: new pages showing a labelled person would still count as extra pages, so it would not fix drift. A committed database snapshot was rejected: it would publish face embeddings of people who gave no licence, derived from copyrighted images, would skip the pipeline, and goes stale on a model upgrade. A crawl mode for exactly the labelled pages was rejected as extra scope.
- **Names in attribution**: source links use the host's numeric page-ID form (Wikimedia Commons `https://commons.wikimedia.org/w/index.php?curid=<page_id>`; Flickr and Unsplash photo IDs), never a URL or file name that names the person. Committed photo files are renamed to the neutral label. The author named in the attribution is the photographer, as the licence requires. URL shorteners were rejected: they hide nothing, add a token and a third-party dependency, and make the licence link unverifiable in review.
