# Feature Specification: Face Search Script

**Feature Branch**: `feat/face-search` (to be created; the spec was drafted on `feat/indexer-4-spec-status`)

**Created**: 2026-11-12

**Status**: Draft

**Input**: User description: "The task described in docs/search-script-brief.md"

**Sources**: [assignment.md](../../docs/assignment.md) (authoritative), [search-script-brief.md](../../docs/search-script-brief.md) (interpretation and recorded decisions), [001-image-indexer spec](../001-image-indexer/spec.md) and its [index read contract](../001-image-indexer/contracts/index-read.md) (the data this script reads and what the pipeline guarantees about it). Where they disagree, the assignment wins.

## Clarifications

### Session 2026-11-12

- Q: How many of a person's pages must a search find for SC-001 to pass? → A: At least 80% of each person's labelled pages; missed pages are listed in the test report. Precision (SC-002) stays at 100%.
- Q: When a re-crawled page no longer shows an image, should search still return that page for the person in it? → A: No. Search ignores a page–image link that the page's most recent successful crawl did not see. The pipeline additionally reports these outdated links in its Airflow task logs and run summary (FR-018, built in this feature).
- Q: When the person matches a page's lead image, should results mark that page? → A: Yes, but only in the detailed output (with scores). The default output stays page addresses only, and ordering stays by match closeness.
- Q: Where should the face-listing option save the copy with numbered faces? → A: Next to the query photo, as `<name>.faces.jpg`, overwriting an earlier copy. The copy must persist on the host after the script exits, and its path must be printed in a form the searcher can open from the host.

## User Scenarios & Testing *(mandatory)*

The primary user is the **searcher**: the developer or an evaluator who has a photo of a person and wants to know which crawled pages show that person. They run the script from the command line inside the project's container setup, after the indexing pipeline has completed at least one run.

### User Story 1 - Find every page that shows a person (Priority: P1)

The searcher gives the script a photo containing one face. The script finds that face, compares it with every face the pipeline indexed, and prints the address of every crawled page that contains an image of the same person. Each page is listed once, however many matching images it contains.

**Why this priority**: This is the assignment's search requirement in full: "return a list of all pages on the site that contain an image of that same character". Without it there is no search deliverable.

**Independent Test**: Index a small fixture site where one person appears in images on three pages (one page with two images of them) and another person appears on two other pages. Search with a different photo of the first person. Exactly the three pages are returned, each once.

**Acceptance Scenarios**:

1. **Given** an index in which a person appears on several pages, **When** the searcher runs the script with a different photo of that person, **Then** every one of those pages is printed, and no other page.
2. **Given** a page that contains two different images of the person, **When** the search runs, **Then** that page appears once in the output.
3. **Given** the same image appears on several pages, **When** the search matches it, **Then** every page that shows that image is returned.
4. **Given** several pages match, **When** results are printed, **Then** they are ordered from the closest match to the least close, using each page's best-matching face.
5. **Given** default options, **When** results are printed, **Then** only page addresses are shown, with no scores or image addresses.

---

### User Story 2 - Trust the results: high confidence only (Priority: P2)

The searcher needs every returned page to really show the same person. A match counts only when it passes a similarity threshold. The default is the threshold recommended for the face model the pipeline uses, and the searcher can tighten or loosen it. A person who is not on the site produces an explicit "no matching pages" result, never a list of weak look-alikes. On request, the script also shows each page's match score and the matching image addresses, so the searcher can check why a page was returned.

**Why this priority**: The assignment asks for pages "with high confidence". Returning look-alikes would make the result list untrustworthy, but the core search (Story 1) is still demonstrable without tuning options.

**Independent Test**: With the fixture index from Story 1, search with a photo of a person who is not in it. The script reports no matching pages. Then search with a photo of the indexed person with scores turned on, and check that every shown score is within the threshold.

**Acceptance Scenarios**:

1. **Given** a photo of a person who appears in no indexed image, **When** the search runs with the default threshold, **Then** the script reports that no pages matched and exits successfully.
2. **Given** a face whose closest indexed face is just outside the threshold, **When** the search runs, **Then** the page of that indexed face is not returned.
3. **Given** the searcher sets a stricter threshold, **When** the search runs, **Then** the result is a subset of the default result.
4. **Given** the searcher asks for details, **When** results are printed, **Then** each page shows its best match score, the addresses of the matching images on it, and whether one of them is the page's lead image.
5. **Given** a threshold outside the valid range for the distance measure, **When** the script starts, **Then** it stops with a message naming the valid range, before any search.

---

### User Story 3 - Search a photo with several people (Priority: P3)

The searcher gives a group photo. By default, every detected face is searched and the results are printed in groups, one group per face, so it is clear which person each page belongs to. Faces are numbered left to right. The searcher can first list the faces, which prints each number with its position and saves a copy of the photo with the numbers drawn on it, and then search for one chosen face only.

**Why this priority**: Real query photos often contain more than one person, and the brief records this behavior as a decision. The single-face case (Story 1) already delivers the core value.

**Independent Test**: Use a query photo with two people, both indexed on different pages. The default search prints two groups, each listing only that person's pages. Listing faces prints two numbered entries and saves an annotated copy. Searching for face 2 prints only the second person's pages.

**Acceptance Scenarios**:

1. **Given** a query photo with two detected faces, **When** the search runs with default options, **Then** results are printed in two groups labeled "face 1" and "face 2", numbered left to right, each with its own pages (or its own "no matching pages").
2. **Given** a query photo with several faces, **When** the searcher asks to list faces, **Then** each face number is printed with its bounding box, an annotated copy is saved next to the photo as `<name>.faces.jpg`, its host path is printed, and no search is run.
3. **Given** an annotated copy was saved, **When** the script exits and its container is removed, **Then** the copy is still at the printed path on the host. **Given** the face list is run again on the same photo, **Then** the copy is overwritten, not duplicated.
4. **Given** a query photo with three faces, **When** the searcher asks to search face 2 only, **Then** only face 2's results are printed.
5. **Given** the searcher asks for a face number the photo doesn't have, **When** the script runs, **Then** it stops with a message stating how many faces were found.
6. **Given** a query photo with exactly one face, **When** the search runs, **Then** the output uses the same format, with a single group labeled "face 1".

---

### Edge Cases

- **No face in the query photo**: the script prints a clear "no face detected" message and exits with a failure status. It never crashes and never returns pages.
- **Query file missing, unreadable, or not an image**: a clear message naming the file and the problem, with a failure status.
- **Query photo in any format or size the pipeline accepts** (large PNG, image with transparency, animated image): it is normalized the same way the pipeline normalizes indexed images (single format, same maximum size, first frame only, transparency flattened) before faces are detected, so the query and the index are comparable.
- **The index is empty** (the pipeline never ran, or indexed no faces under the current model profile): the script says the index has no faces and suggests running the pipeline first. It does not report this as "no matching pages".
- **A pipeline run is in progress**: faces of images whose indexing has not completed are ignored, so a search mid-run sees only fully indexed images.
- **The database is unreachable**: a clear message naming the connection problem, with a failure status.
- **The index has no record of the script's face model profile** (for example, the model runtime was upgraded since the last run): the script refuses to search and says why. Faces stored under an older profile are never compared with the query, since distances across different setups are meaningless.
- **The query photo is itself one of the indexed images**: its pages are returned like any other match. This is valid and expected.
- **One indexed image contains both the searched person and other people**: only the searched person's face decides the match; the page is returned once.
- **Very small or partly cut-off faces in the query**: used only if the detector reports them at its standard confidence, the same rule the pipeline applies.
- **A page that was crawled again and no longer shows a matching image**: that page is not returned for it, because the link wasn't seen in the page's most recent successful crawl. If that crawl failed, the page's existing links still count.
- **The query photo's folder is not writable** when listing faces: the face list is still printed, then the script reports that the annotated copy could not be saved and exits with a failure status.
- **Many matches** (a person who appears on most crawled pages): every matching page is returned, with no result cap.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The search MUST be a separate script, distinct from the indexing pipeline, and MUST NOT depend on the pipeline's orchestrator to run.
- **FR-002**: The script MUST take a path to one query image as input.
- **FR-003**: The script MUST normalize the query image exactly as the pipeline normalizes indexed images, then detect its faces and compute their embeddings, using the same face model profile (detector, recognition model, preprocessing, distance measure) the pipeline used. The profile MUST be defined in one place shared by both.
- **FR-004**: The script MUST compare each query face against all indexed faces by vector similarity, using the profile's distance measure.
- **FR-005**: A stored face MUST count as a match only when its distance to the query face is strictly less than the confidence threshold. The default threshold MUST be the one recommended for the model profile's model and distance pairing. The searcher MUST be able to override it, and values outside the measure's valid range MUST be rejected before searching.
- **FR-005a**: Only indexed faces that were produced under the script's model profile, and that belong to images whose indexing has completed, MUST be compared with the query.
- **FR-006**: For each searched face, the script MUST return every page linked to any image that contains a matching face, with no cap on the number of pages.
- **FR-006a**: A page–image link MUST count only if the page's most recent successful crawl saw it. Links the pipeline recorded in earlier crawls but did not see again MUST be ignored. Pages whose latest crawl failed keep their existing links.
- **FR-007**: Each page MUST appear at most once per searched face, ranked by its best (smallest-distance) matching face, closest first.
- **FR-008**: By default, output MUST contain only page addresses. On request, the script MUST also show each page's best match score, the addresses of its matching images, and a mark when any matching image is the page's declared lead image.
- **FR-009**: When no indexed face is below the threshold, the script MUST report "no matching pages" for that face and exit with a success status.
- **FR-010**: By default, every detected query face MUST be searched, with results grouped per face. Faces MUST be numbered from 1, left to right by the horizontal position of their bounding boxes, with ties broken top to bottom.
- **FR-011**: The searcher MUST be able to restrict the search to one face by its number. A number with no matching face MUST stop the script with a message stating how many faces were found.
- **FR-012**: The searcher MUST be able to list the query's faces without searching: the script prints each face number with its bounding box, and saves a copy of the normalized query image with each face's box and number drawn on it. The copy MUST be saved next to the query photo as `<name>.faces.jpg`, overwriting an earlier copy, and MUST persist on the host after the script and its container exit. The script MUST print the copy's path as the searcher can open it on the host, not only as a path inside the container.
- **FR-013**: The script MUST be read-only toward the database: it MUST NOT create, change, or delete any stored record.
- **FR-014**: The script MUST handle these conditions with a clear message and a failure status, never an unhandled error: missing or unreadable query file, a file that is not a decodable image, no face detected in the query, unreachable database, an index with no faces, and an index with no record of the script's model profile.
- **FR-015**: Exit statuses MUST distinguish success (including "no matching pages") from failure, and the meaning of each status MUST be documented.
- **FR-016**: The script MUST run against the project's containerized database with one documented command, and that command MUST let the searcher pass an image from the host.
- **FR-017**: The README MUST document how to use the script (input, options, defaults, exit statuses) and show a real example invocation with its expected output, including a no-match example.
- **FR-018**: The pipeline MUST report outdated page–image links without deleting them. After each page is processed in a run, the download step MUST log that page's address and the number of its links not seen in this crawl, when that number is above zero. The run summary MUST show the total of such links found in the run.

### Key Entities

- **Query image**: the photo the searcher provides. It is normalized like an indexed image and is never stored.
- **Query face**: one face detected in the query image. Attributes: number (left-to-right order), bounding box, embedding.
- **Indexed face**: a face stored by the pipeline, with its bounding box, embedding, and the image it belongs to (read-only; defined in [001-image-indexer](../001-image-indexer/spec.md)).
- **Page–Image link**: the pipeline's record that an image appears on a page, with when it was last seen. Together with the page's last successful crawl time, it is what turns a matching face into the pages to return (FR-006a).
- **Match**: a pair of a query face and an indexed face whose distance is below the threshold. Attributes: distance, matched image, pages of that image.
- **Page result**: one page returned for one query face. Attributes: page address, best match distance, matching image addresses, lead-image mark (the last three shown only on request).
- **Model profile**: the detector, recognition model, preprocessing, distance measure, and default threshold shared with the pipeline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a person known to appear on the indexed site, a search with a different photo of them returns at least 80% of the pages where they appear, for each person in a hand-labelled test set of at least 5 people. The test report lists every labelled page that was missed.
- **SC-002**: On that same test set, 100% of returned pages show the searched person at the default threshold (no look-alikes).
- **SC-003**: For photos of at least 5 people who are not on the site, 100% of searches report no matching pages.
- **SC-004**: No page address appears more than once within one face's results, in 100% of searches.
- **SC-005**: Every failure case listed above (no face, bad file, empty index, unreachable database, unwritable folder for the annotated copy) produces a clear message and a documented exit status, with zero unhandled errors. A photo with several faces is not a failure: it ends with success and one result group per face.
- **SC-006**: A first-time user can run a search by following the README alone, with one command, and gets results within 30 seconds on an index from a default depth-1 run of every site the crawler can reach (excluding the first-time model download). Today that is CNN only, because people.com refuses the crawler. The measured time is reported together with the index size (faces, images, pages).
- **SC-007**: Running a search leaves the database unchanged: record counts and contents are identical before and after.

## Assumptions

- **Recorded decisions (brief)**: the face model profile is DeepFace with the RetinaFace detector, Facenet512 embeddings, and cosine distance, as used by the pipeline. The default threshold is DeepFace's recommended threshold for Facenet512 with cosine distance (0.30, per the read contract). Precision is favored over recall.
- **Faces per query** follows the brief's decision: search all faces, grouped per face, with an option to pick one and an option to list faces. This is marked for revisiting in [TODO.md](../../TODO.md) and [multi-face-queries.md](../../docs/briefs/multi-face-queries.md); no query-face size or confidence filter is added by this feature.
- **Input is a local file path**. Fetching a query image from a URL is not required by the assignment and is out of scope.
- **Lead-image marks** recorded by the pipeline are shown only in the detailed output. They never filter or reorder results: the assignment asks for "all pages", so a page that shows the person only in a thumbnail is returned too, and ordering stays by match closeness.
- **Noisy indexed faces** (tiny background faces, statues, posters) are matched like any other face in this feature. A size filter or ranking is deferred to [noisy-index-faces.md](../../docs/briefs/noisy-index-faces.md). Several renditions of one photo may appear among a page's matching images; the page is still listed once.
- **Outdated page links are filtered on the search side** (FR-006a), so the pipeline keeps its link history unchanged. This feature updates the pipeline's index read contract ([index-read.md](../001-image-indexer/contracts/index-read.md)) to state the guarantee it relies on (a link refreshed in a page's latest crawl is never older than that crawl). Reporting them in the Airflow task logs and run summary (FR-018) is the one pipeline change this feature makes. Deleting outdated links stays out of scope ([stale-page-links.md](../../docs/briefs/stale-page-links.md)).
- **Output is plain text** on standard output, one page address per line within each face group, with messages on standard error. A machine-readable format is not required.
- **Containerized runtime**: the script runs as a service or one-off command in the project's Docker Compose setup, with a host folder mounted so the searcher can pass images in, and annotated copies land in that same folder and persist on the host. Storage stays small: a copy is made only when faces are listed, never during a search, there is at most one per query photo (re-runs overwrite it), and it is drawn on the normalized image, so it is at most the pipeline's maximum size.
- **Out of scope** (per the brief): crawling or re-indexing, writing to the database, naming who the person is, facial attribute analysis, any UI or web API, and scheduling the search through the orchestrator.
- **Dependency**: the indexing pipeline ([001-image-indexer](../001-image-indexer/spec.md)) has completed at least one run and stored faces, embeddings, images, and page links.
