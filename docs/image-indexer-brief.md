# Mission Brief — Image Indexer (Airflow Pipeline)

Source of truth: [assignment.md](assignment.md). Quoted text is from the assignment; anything else is interpretation or a recorded decision.

## Mission

> "implement a system that crawls a website, extracts images containing people with visible faces, and indexes them for image-based search."

An Airflow pipeline that turns the target sites into a searchable database of faces, linked back to the pages they appear on.

## Goals

1. **Scrape** — "Scrapes images from the given website containing people with visible faces."
2. **Normalize** — "Converts all images to a single format (e.g., JPEG or PNG) and resizes them to a fixed maximum size."
3. **Detect and embed** — "Detects faces and extracts embeddings for all detected faces using a pre-trained deep learning model."
4. **Store** — "Stores results (image metadata, face bounding boxes, and face embeddings) in a database."
5. **Keep page provenance** — record which page(s) each image came from, since search must "return a list of all pages on the site."

## Constraints

- **Target sites** — https://edition.cnn.com/ and https://people.com/; the crawl stays within these domains.
- **Runtime-configurable crawl** — depth and rate limits are set per run (e.g., DAG params), not hard-coded.
- **Python** — "Use Python for implementation."
- **Airflow** — "Orchestrate the pipeline with Apache Airflow."
- **Docker Compose** — "Run everything using Docker Compose (Airflow + database + any other required services)."
- **DeepFace** — via [serengil/deepface](https://github.com/serengil/deepface), no training: **Facenet512** embeddings (512-d), **RetinaFace** detector, cosine distance.
- **Single format, bounded size** — every stored image is JPEG, at most 1024 px on its longest side.
- **Every face** — embed "all detected faces," not just one face per image.
- **One model** — model, detector and preprocessing are fixed and recorded so the search script can match them.
- **Incremental** — re-runs skip images already indexed. Each image stores both its URL and a content hash; a known URL is skipped without downloading, and a new URL whose hash already exists reuses the existing record (new URL and page linked, no re-embedding).
- **robots.txt** — the crawler obeys each site's robots.txt.
- **Postgres + pgvector** — single database for metadata, page links and embeddings (see Storage Decision).
- **Documented** — README must cover "How to run the pipeline," "Libraries/models used," and "Your approach and architecture decisions."
- **Owned quality** — "you are fully responsible for the quality, correctness, and outcomes of the code."

## Non-Goals

- Querying or matching faces (the search script's job).
- Training or fine-tuning a face model.
- Identifying or naming the people found.
- Facial attribute analysis (age, emotion, etc.).
- Crawling beyond CNN and People.
- A UI for browsing the index.

## Success Criteria

1. One documented command starts the full stack (Airflow + database + services) with Docker Compose.
2. Triggering the DAG crawls both sites at the requested depth and rate, and finishes without manual steps.
3. Stored images all share one format and are within the fixed maximum size.
4. Images without a visible face are not indexed as faces.
5. Every detected face has its own row: bounding box, embedding, and a link to its image and source page(s).
6. Image metadata (e.g., source URL, page URL, dimensions) is stored for every indexed image.
7. The same image found on several pages is linked to all of them.
8. A re-run does not re-download or re-embed indexed images, but still records any new pages they appear on.
9. A failure in one image or page does not fail the whole run; the cause is visible in Airflow logs.
10. The search script can query the stored embeddings with no further processing.

## Storage Decision (decided)

**Postgres + pgvector** over a dedicated vector store. Metadata, the image↔page links, the skip-if-indexed check, and the embeddings live in one database with one transaction. At this crawl's scale (thousands to tens of thousands of faces) pgvector's HNSW index is more than fast enough. A separate vector store would add a second service and a second copy of the data to keep in sync.

