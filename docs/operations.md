# Operations

Running, re-running and maintaining the pipeline, and running the tests. Moved here from the README; the commands are unchanged.

## Run summary

The `summarize` task's log ends with a table: pages crawled, images found, reused, skipped (by reason), indexed, faces stored, failures, dead links (HTTP 404/410), known dead links not requested, links abandoned, and outdated page links. A page link is outdated when a re-crawl of the page no longer finds that image there. The download task logs each such page (`outdated links page=… count=…`), the links are kept, and the search ignores them.

## Re-index after a model change

The face model, its detector settings and the DeepFace and TensorFlow versions form the model profile key. The search compares only faces of the current profile, so after the key changes (after a DeepFace or TensorFlow upgrade, or a model change) the existing faces cannot be found until they are embedded again. Run the pipeline with `reindex` set:

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"reindex": true, "page_cap": 1}'
```

The first task deletes the faces of every other profile in one transaction, and sets those images back to `pending`; their stored JPEGs stay. The normal detection tasks then embed them with the current model, so nothing is downloaded again. The re-index takes about as long as the first index (an hour or more for 5,000 images on CPU), and `page_cap: 1` keeps the crawl in the same run to one page per site. While it runs, **search covers only the images already re-embedded**; an image is never in the index under two profiles. If the run is interrupted, or the per-run batch limit defers some images (the log says `detection deferred`), trigger it again: images still `pending` are picked up and nothing is duplicated. Images with no faces keep their status: their JPEGs are not kept, so they cannot be looked at again. The re-index therefore refuses to start when the detection settings (detector, alignment, minimum confidence) differ from those of an earlier profile; start a fresh index then. Start a re-index only when no other run is in progress (the DAG runs one at a time, so wait for a running one to finish). Old `model_profile` rows are kept as history. A run without `reindex` never touches other profiles' faces.

## Re-runs, abandoned links and dead links

**Re-runs** skip every image URL already known, reuse identical content found under a new URL, and still record new pages. A link that fails is retried on later runs, **at most 3 times**; then it is abandoned and logged. To give abandoned links another chance:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM fetch_attempt WHERE attempts >= 3; UPDATE image SET detect_attempts = 0 WHERE status = '\''pending'\'';"'
```

**Dead links** (HTTP 404/410) are not failures, but they are logged (`page dead url=… status=404`), counted, and stored in the `dead_link` table. A link that answered 410 is not requested again; one that answered 404 is requested again after 30 days (`config.DEAD_LINK_RECHECK`). To list them, or to request them all again on the next run:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "SELECT url, kind, status, last_checked_at FROM dead_link ORDER BY last_checked_at DESC;"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM dead_link;"'
```

If you re-run a single task by hand in the Airflow UI, clear it **with downstream** (the default), so detection picks up what it stages.

To look at a stored image, byte-identical to the database copy, or with its face boxes drawn:

```bash
docker compose exec airflow-scheduler uv run --no-project python -m face_index.export_image --id 42 --boxes -o /tmp/42.jpg
docker compose cp airflow-scheduler:/tmp/42.jpg .
```

## Tests and smoke runs

```bash
docker compose up -d postgres         # loopback database for integration tests
export FACE_INDEX_TEST_DSN="postgresql://faces:$(docker compose exec -T postgres cat /run/db-secrets/postgres_password)@127.0.0.1:5432/faces_test"
uv sync --locked --group dag          # `dag` adds Airflow for the DAG tests
uv run pytest -m "not model"          # unit, integration, DAG and offline smoke tests
uv run pytest -m model                # real DeepFace model
scripts/smoke.sh                      # the real DAG in the stack, depth 0, one page per site
```

**Project in iCloud Drive?** iCloud sets the macOS `hidden` flag on the venv's `.pth` files and
Python 3.13 skips hidden ones, so `import face_index` fails outside pytest (pytest adds `src` itself).
Keep the venv outside iCloud (`export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/photo-crawler`), or run
`chflags nohidden .venv/lib/python3.13/site-packages/*.pth`. Delete any `… 2.pth` conflict copies.

| Tier | What it proves | Time |
|---|---|---|
| Offline smoke (`-m smoke`) | The whole pipeline, run 4 times, on a tiny mock web: storage, dedup, links, skips, re-runs, 3-attempt cap | ~1 s |
| Model smoke (`-m model`) | The real model; optional face photos go in `tests/fixtures/images/` | ~15 s |
| Stack smoke (`scripts/smoke.sh`) | Real Airflow, real model, live home pages | ~4 min |

## Known limitations

- **The 30-minute target for a default run on the enabled sites is open.** Only CNN is enabled. The one measured run took 72 minutes for CNN, on the machine above rather than the reference machine, so it neither passes nor formally fails the criterion. CNN articles carry about 100 images each, so 50 pages give about 4,700 images; detection, at about 0.7 s per image with 2 workers, is most of the time. An estimate from those per-image times, not a measurement: about 15 pages fit 30 minutes for one site, and about 10 pages per site if People were enabled too (downloads run in parallel per host, detection does not). Raising `FACE_WORKERS` helps if Docker has the memory. Changing the default `page_cap` or the target is an open decision.
- **people.com is disabled.** It answers the crawler's honest User-Agent (`photo-crawler/0.1.0`) with HTTP 402 from Cloudflare, and its terms forbid scraping. The crawler does not disguise itself to get around this, so the site stays in `config.SITES` with `enabled=False` and the DAG has no `crawl_people`/`download_people` tasks. Leaving People out and documenting why was agreed with the assignment's reviewer (2026-11-15); the other option offered, indexing lead images from People's public news sitemap, was not taken. With the site's permission, set `enabled=True` to crawl it again; while it refuses, a run that includes it records the 402, marks `crawl_people` and the run failed, and still indexes CNN.
- **Several sizes of one photo** are stored as separate images by design, so `--details` can list the same picture more than once under one page. The page itself is listed once.
- **Faces that are depictions** (statues, posters) or tiny background faces are indexed and searched like any other face. A 40 px size filter was measured and not adopted, because the false matches are clear look-alike faces ([search-evaluation.md](search-evaluation.md)).
- **The search fails its accuracy criteria** on both label sets (see the README's [Search accuracy](../README.md#search-accuracy)): it misses most pages of several people, and a look-alike can pass the threshold. The threshold was not tuned to hide this. ArcFace, the one candidate, was not confirmed on the held-out set, so the default stays Facenet512.
