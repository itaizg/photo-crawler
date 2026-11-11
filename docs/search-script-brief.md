# Mission Brief — Face Search Script (`face_matcher.py`)

Source of truth: [assignment.md](assignment.md). Quoted text is from the assignment; anything else is interpretation.

## Mission

> "allow the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character."

A standalone script that answers: *"On which crawled pages does this person appear?"*

## Goals

1. **Accept a query image** — "Takes a new image as input."
2. **Embed the query face(s)** — "Extracts face embeddings from the input image."
3. **Search by vector similarity** — "Searches for similar faces using vector similarity" against the embeddings the pipeline stored.
4. **Return pages, not images** — "Returns the pages containing images of the same character with high confidence."
5. **Cover the whole site** — return "all pages" that contain the character, not just the top hit.

## Constraints

- **Separate script** — "Implement the search in a separate script", distinct from the Airflow pipeline.
- **Python** — "Use Python for implementation."
- **Docker Compose** — "Run everything using Docker Compose"; the script must work against the Compose-hosted database.
- **Same model as the pipeline** — the query must be embedded with the indexer's setup (DeepFace Facenet512 + RetinaFace, cosine distance); otherwise similarities are meaningless.
- **Reads the pipeline's database** — the data source is what the pipeline stores: "image metadata, face bounding boxes, and face embeddings."
- **High confidence** — results must pass a similarity threshold; precision is favored over recall.
- **Documented** — README must cover "How to use the face matching script" and "Example usage and expected outputs."
- **Owned quality** — "you are fully responsible for the quality, correctness, and outcomes of the code."

## Non-Goals

- Crawling, scraping, format conversion, or resizing site images (pipeline's job).
- Writing to or re-indexing the database; the script is read-only.
- Identifying *who* the character is (naming); only matching to the same person.
- Facial attribute analysis (age, emotion, etc.), even though DeepFace supports it.
- A web UI or API service; a command-line script satisfies the requirement.
- Being scheduled or orchestrated by Airflow.

## Success Criteria

1. Given an image of a character known to be on the site, the script returns the URLs of the pages where that character appears.
2. Each returned page is a true match (no look-alikes) at the chosen threshold.
3. Pages are de-duplicated; one page with several matching images is listed once.
4. An image of someone *not* on the site returns no results rather than weak matches.
5. Images with no detectable face, or with several faces, are handled with a clear message or defined behavior, never a crash.
6. Runs with one documented command inside the Docker Compose setup.
7. The README shows a real example invocation and its expected output.

## Decisions

- **High confidence** — configurable threshold; defaults to DeepFace's recommended threshold for Facenet512 + cosine.
- **Faces in the query** — every detected face is searched, with results grouped per face; no interactive prompt. `--face N` limits the search to one face. (Revisit: see [TODO.md](TODO.md).)
- **Face numbering** — faces are numbered left to right by bounding box. `--list-faces` prints each number with its box and saves a copy of the query image with the numbers drawn on.
- **Scores and matched image URLs in output** — configurable; off by default (page URLs only).
