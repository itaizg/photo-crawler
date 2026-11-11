# Feature Specification: Image Indexer Pipeline

**Feature Branch**: `feat/photo-indexer`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Specify the image indexer part, using docs/image-indexer-brief.md"

**Sources**: [assignment.md](../../docs/assignment.md) (authoritative), [image-indexer-brief.md](../../docs/image-indexer-brief.md) (interpretation and recorded decisions). Where they disagree, the assignment wins.

## Clarifications

### Session 2026-11-11

- Q: How should a run's size and duration be bounded beyond crawl depth? → A: A page cap per site (default 50), plus a separate, higher rate limit for image servers (default 5 req/s). Both can be set per run, and a page cap of None means no cap. Rate limits apply to each host on its own, so different hosts may be crawled in parallel.
- Q: Which images on a crawled page should count as "appearing on" that page? → A: Every image on the page (after the size and format filters). When an image is the page's declared lead image (the one named in the page's metadata for link previews), its link to that page is marked as the lead. No layout rules are written for individual sites.
- Q: Should the pipeline keep the converted image files themselves, and if so, where? → A: Yes, in the database. Each converted JPEG's bytes are stored in the same transaction as its faces and page links, and can be exported back to an identical `.jpg` file. No extra compression beyond what the database already does.
- Q: What should happen if a run is triggered while another run is still in progress? → A: Only one full run is active at a time, and a new trigger waits in the queue. Individual steps (inside a run, or a single step the operator re-runs) MAY execute at the same time as each other when they don't collide. Two steps collide when they would send requests to the same host or write the same records. Colliding steps wait for each other. (Refined after PR review: a site's own network steps never overlap, and any host shared between sites is protected by one shared rate budget, instead of making the steps wait. See FR-003 and FR-022a.)

## User Scenarios & Testing *(mandatory)*

The primary user is the **operator**, the person who runs the pipeline to build the face index. That can be the developer, or an evaluator reproducing the project. The downstream consumer is the **face search script**, a separate deliverable that reads what this pipeline stores.

### User Story 1 - Build a searchable face index from the target sites (Priority: P1)

The operator starts the whole system with one documented command. They then trigger an indexing run with a crawl depth and a rate limit, and wait for it to finish. The run crawls CNN (edition.cnn.com) and People (people.com). It collects the images on the pages it visits and converts each one to a single format at a bounded size. It finds every face in each image and stores each face's location and a numeric face signature (embedding). It also stores the image's metadata and the page(s) the image came from.

**Why this priority**: This is the whole of the assignment's pipeline (scrape → normalize → detect and embed → store). Without it, the search script has nothing to search.

**Independent Test**: Start the stack from a clean state and trigger one run at depth 1. Then check the database: indexed images exist, faces exist with locations and signatures, every face links to an image, and every image links to at least one source page.

**Acceptance Scenarios**:

1. **Given** a clean installation, **When** the operator runs the single documented start command, **Then** the orchestrator, the database and every other required service start, and no other manual setup is needed.
2. **Given** the stack is running, **When** the operator triggers a run with depth 1 and a rate limit, **Then** the run crawls both sites' home pages and the same-site pages they link to, and completes without manual intervention.
3. **Given** a crawled page contains a photo of two people with visible faces, **When** the run processes it, **Then** two face records are stored, each with its own location and signature, both linked to that image and that page.
4. **Given** a crawled page contains an image with no visible face (a logo, a chart, a landscape), **When** the run processes it, **Then** no face record is created for that image.
5. **Given** a source image is a large PNG, **When** it is indexed, **Then** the stored copy is a JPEG whose longest side is at most 1024 px, and its recorded metadata matches the stored copy.

---

### User Story 2 - Re-run without redoing work, while still recording new pages (Priority: P2)

The operator triggers the pipeline again later, for example after new articles are published. Images already indexed are not downloaded or analyzed again. New pages that show already-indexed images are still linked to those images, so search results stay complete.

**Why this priority**: The crawl targets news sites, where one photo appears on many pages and the content changes daily. Re-running must be cheap, and it must never lose page links. Search depends on "all pages" being recorded.

**Independent Test**: Run the pipeline twice in a row. The second run downloads and analyzes no previously indexed image, and face counts stay the same. A page added between runs that reuses a known image is linked to that image after the second run.

**Acceptance Scenarios**:

1. **Given** an image URL is already indexed, **When** a later run finds that URL on any page, **Then** the image is not downloaded again, and the page is linked to the existing image record if it was not linked already.
2. **Given** a new image URL whose content is identical to an already-indexed image, **When** a run downloads it, **Then** no new faces are computed: the new URL and its page are linked to the existing image record.
3. **Given** the same image appears on three different pages within one run, **When** the run completes, **Then** the image is stored once and is linked to all three pages.

---

### User Story 3 - Tune and control a run (Priority: P3)

The operator sets crawl depth, page cap and rate limits for each run, without editing code. The crawler obeys each site's robots.txt and never leaves the two target sites when following page links.

**Why this priority**: Being polite to the sites and running reproducible demos both depend on this. It is also required to keep run time predictable.

**Independent Test**: Trigger two runs with different depth and rate values. Check that the deepest page visited matches the depth, that the request pacing matches the rate, and that no page disallowed by robots.txt or outside the two sites was fetched.

**Acceptance Scenarios**:

1. **Given** depth 0, **When** a run starts, **Then** only the two home pages are crawled for images.
2. **Given** a page rate of N and an image-server rate of M requests per second, **When** a run executes, **Then** no page host receives more than N requests, and no image host more than M requests, in any one-second window.
3. **Given** a page cap of 50, **When** a run executes, **Then** at most 50 pages per site are crawled. **Given** a page cap of None, **Then** only the depth limits the crawl.
4. **Given** robots.txt disallows a path, **When** a crawled page links to that path, **Then** the path is not fetched.
5. **Given** a page links to a site other than CNN or People, **When** the run follows links, **Then** that external page is not crawled.
6. **Given** the operator triggers a run without setting depth, page cap or rates, **When** it starts, **Then** documented defaults are used.

---

### User Story 4 - Diagnose failures without losing the run (Priority: P3)

When an image cannot be downloaded or decoded, or a page fails to load, the run goes on with everything else. The operator can see in the run's task logs which item failed and why.

**Why this priority**: Live news sites always produce some broken links, timeouts and odd formats. If one bad item stopped the run, a full crawl would never finish.

**Independent Test**: Include a known-broken image URL and an unreachable page in a run, for example with a test fixture. The run completes, every other item is indexed, and the logs name the broken URL and its error.

**Acceptance Scenarios**:

1. **Given** one image returns an error or is corrupt, **When** the run processes it, **Then** that image is skipped, the cause is logged with its URL, and all other images are processed.
2. **Given** one page times out, **When** the run crawls, **Then** the other pages are still crawled and the timeout is logged.
3. **Given** an image failed in a previous run, **When** a later run finds it again, **Then** it is retried. A failure does not mark the image as indexed.

---

### Edge Cases

- **Images served from a CDN on another domain** (CNN and People host images on separate media hosts): images referenced by an in-scope page are fetched wherever they are hosted. The domain restriction applies only to the pages being crawled.
- **Responsive and lazy-loaded images** (multiple size variants, or a placeholder until scroll): one variant per image is chosen, the largest available, and placeholders are ignored.
- **The lead image appears in the page body as a different rendition** (another size or crop, so a different URL and fingerprint): only the image named in the page metadata gets the lead mark. The body copy is linked as an ordinary image, and the two are not merged.
- **A page with no declared lead image**, or one whose lead image is a logo or below the size threshold: the page has no lead-marked link.
- **Tiny images** (icons, tracking pixels, avatars below a usable size): skipped before face detection. They cannot contain a usable face.
- **Vector images (SVG)**: skipped, since they are logos and graphics, not photographs.
- **Animated images**: only the first frame is indexed.
- **Images with transparency**: flattened onto a solid background before conversion to the single format.
- **Images already smaller than the maximum size**: kept at their original size, never upscaled.
- **Very many faces in one image** (crowd shots): every detected face is stored, with no per-image cap.
- **The same page reached by different URLs** (tracking parameters, fragments, trailing slashes): treated as one page.
- **Redirects**: every redirect hop is checked (scope for pages, robots.txt, address safety, rate limit) *before* it is requested. A page redirect to another site is not followed.
- **References to non-public addresses** (loopback, private, link-local or other reserved IP ranges, by literal IP or after DNS lookup, including through a redirect): never requested. The item is logged as a failure.
- **Oversized images** (a very large download, or a picture whose pixel count would exhaust memory when decoded): the download is aborted above a byte limit, and decoding is refused above a pixel limit. Neither stops the run.
- **Images referenced only through inline CSS** (`style="background-image: url(…)"`): collected like `<img>` images. Background images declared in external stylesheets are not collected.
- **A run triggered while another run is in progress**: it waits in the queue and starts when the active run finishes (FR-022).
- **The operator re-runs a single failed step while other steps are executing**: it runs at once if it doesn't collide with them (different hosts, different records). Otherwise it waits. Either way it creates no duplicates (FR-022a, FR-022b).
- **robots.txt is unreachable**: the crawler uses the conservative default (it does not crawl that site) and logs the reason.
- **Faces partly cut off at the image edge, or very small**: indexed only if the detector reports them as faces at its standard confidence. Anything below that is not a "visible face".

## Requirements *(mandatory)*

### Functional Requirements

**Crawling**

- **FR-001**: The system MUST crawl starting from the home pages of https://edition.cnn.com/ and https://people.com/.
- **FR-002**: The system MUST follow links only to pages within those two sites, up to a crawl depth set for each run (depth 0 = home pages only).
- **FR-003**: The system MUST limit its request rate with two values set for each run: a page rate for the hosts of the target sites' pages, and an image rate for the hosts that serve images. Each limit applies to each host on its own, so requests to different hosts MAY run in parallel. A host's limit is shared by every step executing at once, so two steps that happen to contact the same host together never exceed it.
- **FR-004**: The system MUST obey each host's robots.txt rules before fetching a page or image.
- **FR-005**: Depth, page cap, page rate and image rate MUST be settable when a run is triggered, without code changes. Documented defaults apply when they are not set: depth 1, page cap 50 per site, page rate 1 request per second per host, image rate 5 requests per second per host.
- **FR-005a**: The system MUST stop crawling new pages on a site once that site's page cap is reached for the run. A page cap of None means no cap, and the crawl is then bounded only by depth.
- **FR-006**: The system MUST collect every image on each crawled page, including responsive and lazy-loaded images, thumbnails and author photos, and record which page(s) each image was found on. Images are not filtered by where they sit in the page's layout.
- **FR-006a**: When a page declares a lead image in its metadata (the image used for link previews), the system MUST collect that image and mark its link to that page as the lead. Every other link is unmarked. A page has at most one lead image.
- **FR-007**: The system MUST normalize page URLs so that the same page reached through cosmetic URL variants (fragments, tracking parameters, parameter order, trailing slashes) is recorded once. Query parameters that can select different content MUST be kept.
- **FR-007a**: The system MUST NOT request any address that resolves to a loopback, private, link-local or otherwise non-public IP range, including redirect targets.

**Normalization**

- **FR-008**: The system MUST convert every indexed image to one format (JPEG) and scale it down so that its longest side is at most 1024 px, keeping the aspect ratio and never upscaling.
- **FR-009**: The system MUST keep the normalized copy of each indexed image in the database, as the exact JPEG bytes, and face locations MUST refer to the coordinates of that copy. Images skipped before normalization (FR-010) or containing no faces store no bytes.
- **FR-009a**: The operator MUST be able to export any stored image to a `.jpg` file that is byte-identical to the stored copy.
- **FR-010**: The system MUST skip images that cannot or should not be processed, and record why: vector graphics, images below a minimum size, and images above a maximum pixel count. Content that fails to decode is a failure (FR-023), not a skip, so it is retried on a later run.
- **FR-010a**: The system MUST bound the resources one image can use: downloads above a maximum size are aborted, and decoding is refused above a maximum pixel count.

**Face detection and embedding**

- **FR-011**: The system MUST detect all faces in each normalized image with a pre-trained detection model, using no training or fine-tuning.
- **FR-012**: The system MUST compute a face signature (embedding) for every detected face with a pre-trained recognition model, not just for the largest or first face.
- **FR-013**: The system MUST use one fixed combination of detector, recognition model, preprocessing and distance measure, defined in a single shared place. It MUST record that combination with the stored data so the search script can verify it uses the same one.
- **FR-014**: Images in which no face is detected MUST NOT produce face records.

**Storage**

- **FR-015**: For every indexed image, the system MUST store its metadata: source URL(s), the page(s) it appeared on, a content fingerprint, the normalized dimensions, the original dimensions, the original format, and when it was indexed.
- **FR-016**: For every detected face, the system MUST store its own record: bounding box, detection confidence, embedding, and a link to its image (and through it, to every source page).
- **FR-017**: The system MUST record images that were processed but contained no faces, so that later runs skip them.
- **FR-018**: All stored data (image metadata, page links and embeddings) MUST live in one database, where the search script can query embeddings by vector similarity with no further processing.

**Incremental runs and integrity**

- **FR-019**: Before downloading, the system MUST check whether an image URL is already indexed. If it is, it MUST NOT download it again, and only link any new page to it.
- **FR-020**: After downloading a new URL, the system MUST check the content fingerprint. If identical content is already indexed, the system MUST link the new URL and page to the existing record without computing faces again.
- **FR-021**: An image MAY be staged (with its normalized bytes, source URL and page links) before its faces are computed, but it MUST NOT be marked as indexed until its complete set of faces is stored. Marking it indexed and storing its faces MUST happen together or not at all. Readers of the index (the search script) see only indexed images, so they never see an image with a partial set of faces or a missing copy.
- **FR-022**: Only one full run MAY be active at a time. A run triggered while another is active MUST wait in the queue and start when the active run finishes.
- **FR-022a**: Steps that do not collide MAY execute at the same time as each other. This covers steps inside a run (for example the two sites, or downloading and face detection) and a single step the operator re-runs on its own. Two steps collide when they would write the same records, or when they are the same site's network steps. Colliding steps MUST NOT execute at the same time. Requests from different steps to a shared host are paced by that host's single, shared rate limit (FR-003).
- **FR-022b**: Repeating a step (a retry or a manual re-run) MUST NOT create duplicate image, face or page-link records.

**Reliability and observability**

- **FR-023**: A failure on one page or one image MUST NOT fail the whole run. The failed item is skipped, and is retried on a later run. Infrastructure failures (the database or the face model being unavailable) are not item failures: they MUST fail the step, so the orchestrator retries it.
- **FR-024**: Every skipped or failed item MUST be logged in the run's task logs with its URL and the reason. That includes normal skips (format, size), not only errors.
- **FR-025**: Each run MUST log a summary: pages crawled, images found, images skipped (by reason), images newly indexed, images reused, faces stored, and failures.

**Operation and documentation**

- **FR-026**: The entire system (orchestrator, database and every other required service) MUST start with one documented command.
- **FR-027**: The README MUST cover how to run the pipeline, the libraries and models used, and the approach and architecture decisions (including the storage decision and the incremental strategy).

### Key Entities

- **Page**: a crawled web page on CNN or People. Attributes: normalized URL, site, crawl depth at which it was found, last crawled time. A page shows many images.
- **Image**: one unique picture, identified by its content fingerprint. Attributes: normalized JPEG bytes (for images with faces), normalized dimensions, original dimensions and format, status (staged, indexed with faces, indexed with no faces, or skipped with a reason), indexed time. An image has one or more source URLs, appears on one or more pages, and has zero or more faces.
- **Image source URL**: an address from which an image's content was downloaded. Several URLs can resolve to the same image. This is what lets a re-run skip a known URL without downloading it.
- **Page–Image link**: records that an image appears on a page, and whether it is that page's declared lead image. The relationship is many-to-many. This is what lets search return "all pages", and also lets it tell the page about a person apart from pages that only show their thumbnail.
- **Face**: one detected face in one image. Attributes: bounding box (in normalized-image coordinates), detection confidence, embedding. It belongs to exactly one image.
- **Model profile**: the fixed detector, recognition model, preprocessing and distance measure used to produce the embeddings, shared with the search script.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator who has never seen the project goes from a fresh clone to a running stack with one documented command, and to a completed indexing run with one trigger, with no other manual steps.
- **SC-002**: A run at depth 1 on both sites completes without manual intervention. Any page or image that failed shows up in the run summary and logs, not as a failed run.
- **SC-003**: 100% of stored images are JPEG with the longest side ≤ 1024 px.
- **SC-004**: In a manual spot check of 50 randomly sampled indexed images, none of the face records point to a region that has no face.
- **SC-005**: 100% of face records have a bounding box, an embedding, and a link to an image that links to at least one page.
- **SC-006**: An image that appears on several crawled pages is stored once and linked to every one of those pages.
- **SC-007**: A second run right after a first one downloads 0 previously indexed images and computes 0 new embeddings for them, and still records any new page links.
- **SC-008**: Injecting broken images and unreachable pages into a run does not make the run fail. Each one is named, with a cause, in the logs.
- **SC-009**: The search script can find the nearest stored faces for a query embedding using only what this pipeline stored, with no extra build or transformation step.
- **SC-010**: No crawl request goes to a page path disallowed by robots.txt, or to a page outside the two target sites. Per-host request pacing never exceeds the configured page or image rate, and no site has more crawled pages than the page cap (when one is set).
- **SC-011**: With default settings, a run on both sites from an empty index completes within 30 minutes on a typical laptop.

## Assumptions

- **Mandated technology (assignment / constitution)**: the implementation is in Python, orchestrated by Apache Airflow, and runs entirely under Docker Compose. In this spec, "run", "trigger", and "task logs" refer to an Airflow DAG run, its trigger parameters, and its task logs. These are fixed by the assignment, not design choices.
- **Recorded decisions (brief)**: the face model setup is DeepFace with the RetinaFace detector, Facenet512 embeddings (512 dimensions), and cosine distance. Storage is a single Postgres database with pgvector. Normalized images are JPEG, at most 1024 px on the longest side. These choices are recorded in the brief and are applied as given here.
- **Default crawl settings**: depth 1, a page cap of 50 per site, 1 request per second per page host, and 5 requests per second per image host. These are polite, demo-sized defaults that the operator can override for each run. Image servers are content delivery networks built for heavy traffic, so they get a higher rate than the news pages themselves. Setting the page cap to None gives a full crawl at the chosen depth.
- **Minimum image size**: images smaller than 64 px on their shorter side are treated as unable to contain a usable face, and are skipped. This is a single documented default.
- **What counts as a "visible face"**: whatever the detector reports at its standard confidence threshold. No extra attribute or quality filtering is applied.
- **Image scope**: images referenced by in-scope pages may be fetched from any host (including media CDNs). The robots.txt and rate rules apply to those hosts too.
- **Retained copies**: normalized images are kept inside the database, not on a separate file volume, so there is one store, one backup and one transaction. Storage grows by roughly 200 MB per default run. That is acceptable at this scale, and similarity search does not read the image bytes. JPEG data is already compressed, so no further lossless compression is applied. If size ever matters, the conversion quality setting (a planning decision) is the lever.
- **Scale**: one run covers roughly thousands to tens of thousands of faces. No horizontal scaling is required.
- **Out of scope** (brief non-goals): querying or matching faces, training models, identifying or naming people, facial attribute analysis, crawling other sites, and any browsing UI.
- **Dependency**: the face search script (a separate feature, [search-script-brief.md](../../docs/search-script-brief.md)) consumes this pipeline's stored data and model profile.
