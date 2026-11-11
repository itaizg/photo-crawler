# photo-crawler

System for crawling a website. It extracts images containing people with visible faces, and indexes them for image-based search. The system allows the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character.

This repository currently contains the **image indexer** (the Airflow pipeline). The face matching script is the next feature (`specs/`, feature 002).

## How to run the pipeline

Requirements: Docker with Compose v2 and at least 8 GB of memory for Docker.

```bash
docker compose up -d --build --wait
```

That one command builds the Airflow image (Python 3.13, DeepFace, model weights cached at build time), and starts Postgres with pgvector, the Airflow API server, scheduler and DAG processor. No `.env` is needed. The UI is at http://localhost:8080.

Trigger a run with the defaults (depth 1, at most 50 pages per site, 1 page request/s and 5 image requests/s per host):

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer
```

Or pass your own settings (all optional):

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"depth": 1, "page_cap": null, "page_rate": 1.0, "image_rate": 5.0}'
```

| Param | Default | Meaning |
|---|---|---|
| `depth` | 1 | Link hops from each home page (0–10); 0 = home pages only |
| `page_cap` | 50 | Maximum pages per site; `null` = no cap |
| `page_rate` | 1.0 | Page requests per second, per host (≤ 10) |
| `image_rate` | 5.0 | Image requests per second, per host (≤ 20) |

The `summarize` task's log ends with a table: pages crawled, images found, reused, skipped (by reason), indexed, faces stored, failures, and links abandoned.

**Re-runs** skip every image URL already known, reuse identical content found under a new URL, and still record new pages. A link that fails is retried on later runs, **at most 3 times**; then it is abandoned and logged. To give abandoned links another chance:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM fetch_attempt WHERE attempts >= 3; UPDATE image SET detect_attempts = 0 WHERE status = '\''pending'\'';"'
```

If you re-run a single task by hand in the Airflow UI, clear it **with downstream** (the default), so detection picks up what it stages.

To look at a stored image, byte-identical to the database copy, or with its face boxes drawn:

```bash
docker compose exec airflow-scheduler python -m face_index.export_image --id 42 --boxes -o /tmp/42.jpg
docker compose cp airflow-scheduler:/tmp/42.jpg .
```

## Libraries and models

| Purpose | Choice |
|---|---|
| Orchestration | Apache Airflow 3.4 (LocalExecutor, Task SDK) |
| Face detection | DeepFace with the **RetinaFace** detector, confidence ≥ 0.9 |
| Face embeddings | DeepFace **Facenet512** (512-d), aligned, `base` normalization, **cosine** distance |
| Storage | PostgreSQL 17 + **pgvector** (HNSW index, `vector_cosine_ops`) |
| Crawling | httpx (manual redirects, IP pinning), BeautifulSoup + lxml, `urllib.robotparser` |
| Images | Pillow |
| Tooling | uv, ruff, ty, pytest, pre-commit, GitHub Actions |

The model setup lives in one place, `src/face_index/model_profile.py`, and is recorded in the `model_profile` table, so the search script can check it embeds queries the same way.

## Approach and architecture decisions

Full reasoning: [`specs/001-image-indexer/research.md`](specs/001-image-indexer/research.md) (R1–R13).

```
init_schema ─┬─ crawl_cnn    ── download_cnn    ─┐
             └─ crawl_people ── download_people ─┴─ plan_batches ── detect (mapped) ── summarize
```

- **Ports and adapters.** `face_index` holds all logic and never imports Airflow; DAG tasks are thin wrappers. The pipeline depends only on protocols (`Fetcher`, `Pacer`, `ImageNormalizer`, `FaceEmbedder`, `IndexStore`), so the whole pipeline runs offline in tests with fakes.
- **One database.** Postgres + pgvector holds image metadata, the converted JPEG bytes, page links, faces and embeddings. An image becomes `indexed` in the same transaction that stores its faces; staged (`pending`) images are invisible to search. No Redis: the LocalExecutor needs no broker, and the per-host rate limit is one Postgres row per host.
- **Download and detection are decoupled.** Downloads stage images as `pending`; detection runs in parallel batches (`face_cpu` pool, 2 slots by default) with row locks, so no image is processed twice.
- **Polite and safe crawling.** robots.txt is obeyed; each host has a shared page budget and image budget; every redirect hop is checked before it is requested; requests to private or local network addresses are refused, and connections are pinned to the checked IP. Downloads stop at 20 MB and images over 50 megapixels are not decoded.
- **Normalization.** Every stored image is an RGB JPEG (quality 90, no EXIF) whose longest side is at most 1024 px; images are never upscaled. SVGs and images under 64 px are skipped with a recorded reason.
- **Incremental by URL and by content.** A known URL is never downloaded again; a new URL whose SHA-256 matches a stored image is linked to it without re-detecting faces. Lead images (`og:image`) are marked per page.
- **Failure isolation.** One bad page or image is logged with its URL and counted; the run continues. Database or model outages fail the task instead, so Airflow retries it.
- **No secrets in git.** The local database uses no password (trust auth, reachable only from the Compose network and 127.0.0.1); Airflow's JWT secret is generated at first start into a private volume.

## Tests and smoke runs

```bash
docker compose up -d postgres         # loopback database for integration tests
uv sync --locked --group dag          # `dag` adds Airflow for the DAG tests
uv run pytest -m "not model"          # unit, integration, DAG and offline smoke tests
uv run pytest -m model                # real DeepFace model
scripts/smoke.sh                      # the real DAG in the stack, depth 0, one page per site
```

| Tier | What it proves | Time |
|---|---|---|
| Offline smoke (`-m smoke`) | The whole pipeline, run 4 times, on a tiny mock web: storage, dedup, links, skips, re-runs, 3-attempt cap | ~1 s |
| Model smoke (`-m model`) | The real model; optional face photos go in `tests/fixtures/images/` | ~15 s |
| Stack smoke (`scripts/smoke.sh`) | Real Airflow, real model, live home pages | ~4 min |

## Example usage and expected outputs

From a real stack smoke run on 2026-11-11 (depth 0, one page per site), Apple M-series, 14 cores, Docker with 8 GB:

```
PASS: at least one face stored (312)
PASS: images are JPEG and at most 1024 px (0)
PASS: every face is linked to a page (0)
PASS: every face has a 512-d embedding (0)
```

The CNN home page gave 196 images: 122 with faces (312 faces in total) and 74 without. The run took about 4 minutes.

## Known limitations

- **people.com refuses the crawler** with HTTP 402 for its honest User-Agent (`photo-crawler/0.1.0`). The pipeline records the failure, retries it on up to 3 runs, and indexes CNN normally. The crawler does not disguise itself to get around this; crawling People needs the site's permission.
- The face matching script (and the constitution's final check, "run it against the resulting database") arrives with feature 002.
