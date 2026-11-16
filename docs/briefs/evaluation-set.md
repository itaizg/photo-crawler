# Mission Brief — Search Evaluation Set

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [spec 002](../../specs/002-face-search/spec.md) SC-001 to SC-003 and [quickstart §7](../../specs/002-face-search/quickstart.md#7-accuracy-evaluation-sc-001-to-sc-003). Lands after feature 002.

## Mission

> "Returns the pages containing images of the same character with high confidence."

`scripts/evaluate_search.py` can measure the search, but there is nothing to measure it against: the hand-labelled set it expects does not exist yet. This mission builds that set as a CSV table that pandas reads, commits it with openly licensed photos so anyone who clones the repo can run it, and adds a GitHub Actions job that checks the set and the evaluator on every pull request.

## Goals

1. **Label people on the site**: at least 5 people, each with a query photo not taken from the indexed site and every page URL they appear on.
2. **Label absent people**: at least 5 people who are not on the site, each with a query photo.
3. **Include hard cases**: look-alikes of labelled people and pages where the person's face is small, so SC-002 is actually stressed.
4. **Store labels as CSV**: one row per (person, page) and one row per absent photo, read with pandas into the evaluator's existing Pydantic models. The TOML format is replaced, not kept alongside.
5. **Check it in CI**: a GitHub Actions job validates the table and runs the evaluator end to end against a mock, on every pull request that touches either.
6. **Record the results**: run the evaluation on a live index and put the numbers, the labelling method and its bias into the README.

## Constraints

- **pandas as the reader**: already in `uv.lock` (Airflow constraint pin `pandas==3.1.0`), so no new dependency. Read with explicit `dtype`s; no type inference.
- **Validate once at the boundary**: rows become Pydantic models when read; a bad row fails the whole load with its line number. No scattered try/except.
- **Openly licensed photos only**: a query photo is committed only under a licence that allows it (public domain, CC0, CC BY, CC BY-SA). Each one has a row in an attribution file: source URL, author, licence. Anything else stays out of the repo.
- **Neutral labels**: committed labels use `person-a`, never real names. Photo paths are relative to the labels file.
- **Independent query photos**: a person's query photo must not be one of the indexed images or a rendition of one.
- **Labelling method is written down**: how pages were found (browse, search, both), by whom, and on which crawl date, since recall is only as good as the labels.
- **No silent skips**: a photo where no face is detected, or a labelled page missing from the index, is reported and fails the run (constitution XII).
- **CI with a mock, not the model**: the job checks the schema and consistency (columns, types, URL format, no duplicate rows, minimum counts, no page shared by a person and an absent row). It then runs the evaluator against a seeded pgvector service with `FakeEmbedder` (`tests/fakes.py`), as the integration tests already do. The fixture must include a planted miss and a planted look-alike, so the job proves the evaluator can FAIL, not only PASS (constitution IV).
- **Existing CI style**: pinned action SHAs, `uv sync --locked`, `permissions: contents: read`, `concurrency` group, a job timeout.
- **Same search code**: the evaluator keeps calling `search_faces` at the default threshold; the set does not get its own threshold.

## Non-Goals

- Running DeepFace in GitHub Actions; the mock covers the evaluator's logic, and the real-model run is local.
- Changing the threshold or adding a face-size filter. The evaluation may recommend a threshold in its report; acting on it belongs to [noisy-index-faces.md](noisy-index-faces.md).
- Multi-face query photos; each photo has one clear face (see [multi-face-queries.md](multi-face-queries.md)).
- A labelling UI or annotation tool; a spreadsheet exported to CSV is enough.
- Covering every person on the site; the set is a sample.

## Success Criteria

1. The labels CSV has at least 5 present people, 5 absent people and at least 2 hard cases, and loads through pandas into the evaluator without error.
2. A malformed row (missing column, bad URL, duplicate, wrong type) fails CI with a message naming the row.
3. In CI, the mock run reports PASS on the clean fixture and FAIL on the planted miss and look-alike; it needs no secrets and finishes in under 5 minutes.
4. After cloning, `docker compose up` and one indexing run, a contributor runs `scripts/evaluate_search.py` with one documented command and gets the per-person table and PASS or FAIL against SC-001 to SC-003.
5. Every committed photo has an attribution row with a licence that allows redistribution and a page-ID source URL; CI rejects a source URL that is not in page-ID form.
6. The README records the results, the labelling method and its known bias, and quickstart §7 points to the new format.

## Decisions

- **Format**: CSV, so label changes are reviewable in a diff.
- **Where photos live** (recommended): committed under `queries/eval/`, openly licensed with attribution. A contributor gets the full set by cloning, and CI needs no secrets. An encrypted artifact was rejected: it needs a secret that fork pull requests cannot read, and it adds key management for no gain over licensed photos.
- **Hard cases**: included in the set.
- **Names in attribution**: source links use the host's numeric page-ID form (Wikimedia Commons `https://commons.wikimedia.org/w/index.php?curid=<page_id>`; Flickr and Unsplash photo IDs), never a URL or file name that names the person. Committed photo files are renamed to the neutral label. The author named in the attribution is the photographer, as the licence requires. URL shorteners were rejected: they hide nothing, add a token and a third-party dependency, and make the licence link unverifiable in review.
