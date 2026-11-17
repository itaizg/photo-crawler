# Search accuracy evaluation

The full results behind the README's summary: how the search is measured, the first label set's results and distances, the face-size filter, and the offline experiments.

The search is measured against hand-labelled label sets committed under [`queries/eval/`](../queries/eval/), each from one crawl of the site: at least 5 people who appear on it, with every page they appear on, and at least 5 people who do not. Each person has one openly licensed photo that is not from the site, named by a neutral label (`person-a`, `absent-a`) and attributed in `attribution.csv`. Every set includes hard cases: a look-alike of a labelled person, a page where the face is small, and two labelled people who resemble each other when the crawl has them. The criteria are the face search's SC-001 to SC-003: at least 80% of each person's pages found, no page returned that does not show the person, and nothing returned for people not on the site.

Run it from the host against the Compose database, as the read-only role (the password is read from the Compose secrets volume, nothing is typed or committed):

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py
```

It prints a per-label table (expected pages, returned pages, recall, extra pages, label errors, and every missed or extra URL), then `PASS` (exit 0) or `FAIL` (exit 1); exit 2 means it could not evaluate (invalid files, a photo with no usable face, no index). `--set YYYY-MM-DD` picks an older set and `--check` validates the files without a database. With `--log`, the run is appended to [`runs.csv`](../queries/eval/runs.csv), and every page's best distance up to the report ceiling (0.80; 0.50 in the runs logged with Facenet512) to `queries/eval/distances.csv`, so a run can be read as a curve, not only as one verdict.

CI validates the files and runs the evaluator against a seeded mock on every pull request. It also renders the committed results as an HTML report: the verdict, the per-label table, a chart of every logged page distance against the threshold, and the run history. The report is attached to each workflow run as the `eval-report` artifact, and its summary appears on the run's page. To build it locally:

```bash
uv run python scripts/eval_report.py --out eval-report.html
```

## Results

**Label set 2026-11-16, run 2026-11-17 10:34 UTC: FAIL.** The run used the labeller's snapshot of the 2026-11-16 CNN crawl (15,822 faces, 5,160 images, 97 pages), profile `retinaface+Facenet512+cosine+align+base@deepface0.0.104/tf2.21`, at the default threshold of 0.30. The table is copied from the run's output; every run is kept in [`queries/eval/runs.csv`](../queries/eval/runs.csv).

| label | expected | returned | recall | extra | label errors |
|---|---:|---:|---:|---:|---:|
| person-a | 3 | 3 | 1.00 | 0 | 0 |
| person-b | 3 | 0 | 0.00 | 0 | 0 |
| person-c (small face on 1 page) | 6 | 6 | 1.00 | 0 | 0 |
| person-d (small face on 1 page) | 2 | 0 | 0.00 | 0 | 0 |
| person-e | 2 | 2 | 1.00 | 0 | 0 |
| absent-a | 0 | 0 | - | 0 | 0 |
| absent-b | 0 | 0 | - | 0 | 0 |
| absent-c | 0 | 1 | - | 1 | 0 |
| absent-d (look-alike of person-c) | 0 | 3 | - | 3 | 0 |
| absent-e | 0 | 0 | - | 0 | 0 |

Against the criteria:

- **SC-001, recall of at least 0.80: fails for 2 of 5 people.** person-b and person-d were not found on any of their 5 pages between them.
- **SC-002, no extra pages for present people: passes.** No present person was returned on a page that does not show them.
- **SC-003, nothing returned for absent people: fails for 2 of 5.** absent-c matched 1 page and the look-alike absent-d matched 3. In the match review, the labeller judged that none of these pages shows the person.

**How the labels were completed.** The set was labelled by browsing, and the first run (the first block in `runs.csv`) reported 8 extra pages. The labeller then reviewed every matched image with `face-matcher --details` and confirmed that each extra page does show the person. Pages are labelled from what their images show, so those pages were added to the set. Pages above the threshold were reviewed the same way, which added 3 pages for person-b and person-d that the search does not return. Each person is therefore labelled on the pages the review found, which may still miss a page the search never ranked.

**What the distances show.** The run at 13:19 UTC repeated these counts with `--log`, which also recorded every page's best distance to each query photo up to 0.50 in [`distances.csv`](../queries/eval/distances.csv) (the CI report charts them):

| query | true pages (distance) | closest wrong page (distance) |
|---|---|---|
| person-a | 0.150, 0.171, 0.256 | 0.413 |
| person-b | 0.313, 0.342, 0.344 | 0.365 |
| person-c | 0.100 to 0.123 (6 pages) | 0.342 |
| person-d | 0.365, 0.392 | 0.349 |
| person-e | 0.157, 0.158 | 0.370 |
| absent-c | none | 0.280, then 0.349 |
| absent-d | none | 0.233, 0.296, 0.299, then 0.303 |

- **For person-a, c and e the search separates cleanly:** true pages at 0.26 or less, the nearest wrong page at 0.34 or more.
- **person-b is a near miss.** All 3 pages are ranked above any wrong page but lie just past 0.30.
- **person-d cannot be found by a threshold.** A wrong page (0.349) is closer than both true pages.
- **No single threshold separates true from wrong matches.** absent-d's wrong pages start at 0.233, below person-b's true pages. Across the set:

  | threshold | true pages found (of 16) | wrong pages returned |
  |---:|---:|---:|
  | 0.30 | 11 | 4 |
  | 0.35 | 14 | 10, including 1 each for person-c and person-d |
  | 0.40 | 16 | 38 |

  A higher threshold trades the recall failure for more false matches and also breaks SC-002, so the default stays at 0.30. These numbers come from this set alone and are not a basis for choosing a threshold; tuning on them would overfit it.
- **A few pages are close to many faces.** `/travel/news`, `/world/heroes` and `/world/americas` are within 0.40 of three different query photos each, and include absent-d's three false matches. `/world` does the same, but it is an index page with many faces. A face on those pages that resembles many people (small, blurred or side-on) would explain this; it is the case in [noisy-index-faces.md](briefs/noisy-index-faces.md), and those pages' faces are the first thing to inspect.
- **Every labelled page is within 0.50** of its person's photo, so the misses come from where the cutoff sits, not from faces missing from the index.

**Hard cases in this set.**

- The small-face rows are weak. person-c and person-d appear on the Ukraine page both large and small, so the large face can carry the match. The 40 px run showed that person-c appears only small on `/politics/president-donald-trump-47`; a later set should mark such a page `small_face`.
- The crawl had no on-site look-alike pair: no two labelled people on the site resemble each other (`onsite_pair=false`).

## Results, label set 2026-11-17

**Label set 2026-11-17, run 2026-11-17 18:30 UTC: FAIL.** The second set, on a second crawl of CNN (16,816 faces, 5,577 images, 97 pages), with 10 present and 10 absent people. The profile and threshold are the same as for 2026-11-16. The table is copied from the run's output, and the run is in [`queries/eval/runs.csv`](../queries/eval/runs.csv).

| label | expected | returned | recall | extra | label errors |
|---|---:|---:|---:|---:|---:|
| person-a | 3 | 3 | 1.00 | 0 | 0 |
| person-b | 3 | 0 | 0.00 | 0 | 0 |
| person-c | 6 | 5 | 0.83 | 0 | 0 |
| person-d | 7 | 0 | 0.00 | 0 | 0 |
| person-e | 2 | 2 | 1.00 | 0 | 0 |
| person-f (small face on 4 pages) | 23 | 0 | 0.00 | 0 | 0 |
| person-g | 2 | 0 | 0.00 | 0 | 0 |
| person-h | 4 | 3 | 0.75 | 0 | 0 |
| person-i | 4 | 2 | 0.50 | 0 | 0 |
| person-j | 4 | 3 | 0.75 | 0 | 0 |
| absent-a | 0 | 0 | - | 0 | 0 |
| absent-b | 0 | 0 | - | 0 | 0 |
| absent-c | 0 | 1 | - | 1 | 0 |
| absent-d (look-alike of person-c) | 0 | 2 | - | 2 | 0 |
| absent-e | 0 | 0 | - | 0 | 0 |
| absent-f | 0 | 0 | - | 0 | 0 |
| absent-g | 0 | 0 | - | 0 | 0 |
| absent-h | 0 | 0 | - | 0 | 0 |
| absent-i | 0 | 0 | - | 0 | 0 |
| absent-j | 0 | 0 | - | 0 | 0 |

Against the criteria:

- **SC-001, recall of at least 0.80: fails for 7 of 10 people.** Only person-a, person-c and person-e pass. For person-b, person-d, person-f and person-g the search returned none of their labelled pages at 0.30, although every one of those pages is labelled; their closest pages lie just past the threshold (0.313, 0.349, 0.3002 and 0.3008), and person-h, person-i and person-j (0.75, 0.50, 0.75) each missed one or two pages.
- **SC-002, no extra pages for present people: passes.** No present person was returned on a page that does not show them. At 0.35 one is.
- **SC-003, nothing returned for absent people: fails for 2 of 10.** absent-c matched 1 page and the look-alike absent-d matched 2. The labeller judged that none of these pages shows the person (pages that also matched in 2026-11-16).

**What the distances show.** The run logged every page's best distance up to 0.50 in [`distances.csv`](../queries/eval/distances.csv).

- **Several misses are just past the threshold.** person-f's closest labelled page is 0.3002 and person-g's is 0.3008. person-b's three pages lie between 0.313 and 0.344, as in 2026-11-16, so that near miss repeats on a new crawl.
- **person-d is the hardest.** 5 of its 7 pages lie between 0.349 and 0.495 and 2 are beyond 0.50. The closest wrong page for person-d is 0.447, so a higher threshold would not return wrong pages first for this person.
- **The formal-portrait query photos probably match worse for the US politicians.** person-f, person-g and person-i have the weakest recall among the new people, and their query photos are formal portraits compared with angled, small or blurred news faces; this is a guess, not tested. person-f has 4 labelled pages (the markets pages, marked `small_face`) where no face lies within 0.50.
- **Across the set:**

  | threshold | true pages found (of 58) | wrong pages returned (present people) | wrong pages returned (absent people) |
  |---:|---:|---:|---:|
  | 0.30 | 18 | 0 | 3 |
  | 0.35 | 31 | 1 | 6 |
  | 0.40 | 43 | 16 | 34 |

  The default stays at 0.30. As in 2026-11-16, no threshold separates the wrong pages from the true ones, and these counts are not a basis for choosing one.

**How the labels were made.** The labels were not made by browsing. For each present person, a page was a candidate if an image on it was returned by the search at up to distance 0.45, or if a caption on a freshly crawled page named the person. The labeller then approved each candidate in a review page that showed the page's images with the detected face boxes. Caption-only candidates and look-alike matches were rejected where the person was not in the image. Two pages the labeller was unsure about (person-f on `/world` and `/world/middle-east`) were left out.

**Hard cases in this set.** The small-face case is person-f on the four `/markets` pages, where the face is small and the person does not also appear large. The look-alike is absent-d for person-c. There is no on-site look-alike pair (`onsite_pair=false`).

**Bias in this set.** The candidates came partly from the baseline search itself, so pages the baseline cannot rank are under-labelled. Pages where a person appears but is neither returned at 0.45 nor named in a caption are missing from the labels. Putin and Trump in particular are named on more pages than are labelled, and recall is therefore probably over-stated for them rather than under-stated.

## Face-size filter: tried at 40 px, not adopted

`--min-face PX` (on `face_matcher` and the evaluator) compares only indexed faces at least PX pixels on their shorter side. The value was fixed at 40 px before the run, from the brief's common floor for Facenet-style models, not chosen from these results. The run at 14:27 UTC (`min_face_px=40` in `runs.csv`) compares with the unfiltered run at 13:19, and with a control run at 4 px (14:28), which removes almost no face:

| | no filter | 4 px (control) | 40 px |
|---|---:|---:|---:|
| verdict | FAIL | FAIL | FAIL |
| true pages returned (of 16) | 11 | 11 | 10 |
| wrong pages below 0.30 | 4 | 4 | 4 |
| wrong pages below 0.40 | 38 | 38 | 19 |

The control run's 188 logged distances equal the unfiltered run's exactly, so the filter changes nothing by itself; the differences at 40 px come from the faces it removes.

- **It does not remove the false matches.** absent-c's page (0.280) and absent-d's three pages (0.238 to 0.299) come from faces of 40 px or more, so they are clear faces that look like those people, not tiny background faces.
- **It costs a true page.** person-c is no longer found on `/politics/president-donald-trump-47` (0.110 before), where the face is under 40 px. That is the "appears only small" case this set lacked. person-c's recall drops to 0.83, still above 0.80. person-b's `/world/middle-east` page moves from 0.342 to 0.445 for the same reason.
- **It halves the near misses** between 0.30 and 0.40 (38 wrong pages to 19), including the pages that were close to several queries (`/travel/news` for person-c, `/world` for absent-d). Small faces were adding noise there, but not at the threshold in use.

The filter stays available and off by default (0). The remaining false matches are clear look-alikes, which a size filter cannot separate. The next things to try are other approaches: several query photos per person, a stronger embedding model, or score normalization. Any of them needs a second label set to check against.

## Experiments

Whether hubness reduction (CSLS), score normalization (T-norm), query expansion or the ArcFace model separate true from wrong matches better is measured offline, on a local export of the index, with a grid and metrics fixed before any run: [search-experiments.md](briefs/search-experiments.md). Results below are exploratory, on the 10 people of the 2026-11-16 set; no default changes before a second label set confirms them.

Every combination, over the 5 present and 5 absent people (16 true pages). AUC compares true pages against all wrong pages. Recall at zero false matches is the share of true pages that score better than every wrong page, absent people included. Separable is the number of people whose true pages all beat their own wrong pages. Absent ok is the number of absent people whose best wrong page scores below every true page. The Facenet512 raw row reproduces the logged run (checked before anything was reported).

| Model | Score | Re-ranking | AUC | Recall at zero false matches | Separable | Absent ok |
|---|---|---|---|---|---|---|
| Facenet512 (current) | raw cosine | none | 0.994 | 0.62 | 4/5 | 0/5 |
| Facenet512 | CSLS | none | 0.976 | 0.12 | 3/5 | 0/5 |
| Facenet512 | T-norm | none | 0.986 | 0.38 | 4/5 | 0/5 |
| Facenet512 | raw cosine | query expansion | 0.996 | 0.31 | 4/5 | 0/5 |
| Facenet512 | CSLS | query expansion | 0.985 | 0.00 | 4/5 | 0/5 |
| Facenet512 | T-norm | query expansion | 0.991 | 0.00 | 4/5 | 0/5 |
| ArcFace | raw cosine | none | **0.999** | **0.88** | **5/5** | **2/5** |
| ArcFace | CSLS | none | 0.946 | 0.12 | 3/5 | 0/5 |
| ArcFace | T-norm | none | 0.999 | 0.81 | 5/5 | 2/5 |
| ArcFace | raw cosine | query expansion | 0.996 | 0.31 | 4/5 | 0/5 |
| ArcFace | CSLS | query expansion | 0.991 | 0.00 | 5/5 | 0/5 |
| ArcFace | T-norm | query expansion | 0.995 | 0.06 | 4/5 | 0/5 |

**Correction: query expansion without renditions (added after pre-registration, 2026-11-17).** The query-expansion rows above average each query with its 2 closest index faces, and those are often one photo stored at two sizes. The corrected arm skips a face closer than 0.02 to a neighbour already taken, as the CSLS arm already did. Re-scored on the same snapshot; the 12 rows above reproduced exactly and are kept as pre-registered. These 6 rows choose nothing:

| Model | Score | Re-ranking | AUC | Recall at zero false matches | Separable | Absent ok |
|---|---|---|---|---|---|---|
| Facenet512 | raw cosine | query expansion, renditions skipped | 0.996 | 0.25 | 4/5 | 0/5 |
| Facenet512 | CSLS | query expansion, renditions skipped | 0.987 | 0.00 | 4/5 | 0/5 |
| Facenet512 | T-norm | query expansion, renditions skipped | 0.991 | 0.00 | 4/5 | 0/5 |
| ArcFace | raw cosine | query expansion, renditions skipped | 0.996 | 0.31 | 4/5 | 0/5 |
| ArcFace | CSLS | query expansion, renditions skipped | 0.991 | 0.00 | 5/5 | 0/5 |
| ArcFace | T-norm | query expansion, renditions skipped | 0.995 | 0.06 | 4/5 | 0/5 |

The correction changes little: recall at zero false matches for Facenet512 with raw cosine falls from 0.31 to 0.25, and the ArcFace rows are unchanged. Query expansion still lowers recall in every case, so the conclusion below stands.

What the table shows:

- **Re-scoring does not help Facenet512.** CSLS and T-norm lower both AUC and recall. Query expansion raises AUC by 0.002 and halves recall, because it pulls wrong pages closer too. The same holds on ArcFace.
- **The model is what helps.** ArcFace with raw cosine and no re-ranking is the only row that beats the baseline on both recall and AUC. ArcFace: recall at zero false matches 0.88 (14 of 16 true pages), 5/5 people separable, 2/5 absent people below every true page, AUC 0.999. Facenet512 (the baseline): recall at zero false matches 0.62 (10 of 16), 4/5 separable, 0/5 absent people below every true page, AUC 0.994. AUC is near 1 for both because thousands of easy wrong pairs dominate it, so recall at zero false matches is the number that separates them. ArcFace with T-norm is close but adds nothing over raw.
- **ArcFace does not pass either.** No single threshold meets all three criteria with ArcFace: 2 true pages still score below a wrong page, and 3 absent people still score above a true page.
- **One combination is worth confirming: ArcFace, raw cosine, no re-ranking.** The gain is spread over three people but not evenly: person-b gains 3 of the 4 extra true pages, person-a 1 and person-d becomes separable. Without person-b it still beats the baseline (recall 11 against 10 pages, 4 against 3 separable people), so it is not carried by one person, but it leans on one.
- **Not a result yet.** The set is 10 people, one labeller and one crawl; one true page is 6 points of recall. The best of 12 combinations on one set can win by luck. ArcFace distances are on a different scale, so the 0.30 threshold does not carry over, and a new threshold must be chosen on a second label set, not here. The ArcFace index would also need a full re-embedding and a new schema profile.

The snapshots and the per-person results stay local (`eval-snapshots/`, never committed), because they hold embeddings of people who gave no licence.

**Per-person results for the baseline and the candidate** (true pages above every wrong page, out of each person's true pages; "separable" means that person's true pages all beat their own wrong pages):

| Person | Facenet512 raw | Separable | ArcFace raw | Separable |
|---|---|---|---|---|
| person-a | 2 of 3 | yes | 3 of 3 | yes |
| person-b | 0 of 3 | yes | 3 of 3 | yes |
| person-c | 6 of 6 | yes | 6 of 6 | yes |
| person-d | 0 of 2 | no | 0 of 2 | yes |
| person-e | 2 of 2 | yes | 2 of 2 | yes |
| Total | 10 of 16 | 4/5 | 14 of 16 | 5/5 |

Absent people below every true page: ArcFace raw has absent-a and absent-c, the baseline has none.

**Experiment details** (run on 2026-11-17 on the 2026-11-16 label set; full design in [search-experiments.md](briefs/search-experiments.md)):

- **Method.** The index was exported once, read-only, to a local file; the stored JPEGs were re-embedded once with ArcFace (same detector, alignment and confidence filter); all 12 combinations were then scored offline in numpy, with no database and no change to the search. Query photos use their largest face, as the evaluator does. A page's score is its best face score among the images the search would show for it.
- **Fixed before the run, not tuned:** CSLS with k = 10 (neighbours closer than 0.02 skipped, because each size of a photo is a separate image); T-norm over all index faces for each query; query expansion with the top 2 faces. Metrics are threshold-free, because score scales differ between methods.
- **Check before reporting.** The Facenet512 raw row had to reproduce the logged unfiltered run in `queries/eval/distances.csv` within 5e-5 (4 decimals), and it did.
- **Data.** 15,822 faces in 5,160 images on 97 pages. The experiments read a clean copy of the committed label set (`--eval`), so photos added to `queries/eval` since do not matter.
- **Commands** (read-only reader DSN for the first two):

  ```bash
  uv run python scripts/experiments/export_index.py --set 2026-11-16
  uv run python scripts/experiments/reembed.py --set 2026-11-16 --model ArcFace --workers 4
  uv run python scripts/experiments/run.py --set 2026-11-16 --eval <clean copy of queries/eval>
  ```

## Confirmation on the 2026-11-17 set: not confirmed

The rule was fixed before any run on this set ([search-experiments.md](briefs/search-experiments.md#choice-for-confirmation-recorded-2026-11-17-before-any-run-on-set-2026-11-17)): ArcFace with raw cosine and no re-ranking is confirmed only if it improves both AUC and recall at zero false matches over Facenet512 there too. Scored on 2026-11-17 (16,816 faces, 5,577 images, 97 pages; 10 present people with 58 true pages, 10 absent people); the Facenet512 row reproduced the logged run first.

| Model | Recall at zero false matches | AUC | Separable | Absent ok |
|---|---|---|---|---|
| Facenet512 (current) | 0.28 | 0.913 | 7/10 | 0/10 |
| ArcFace | 0.28 | 0.912 | 4/10 | 0/10 |

ArcFace does not improve either number, so **the default stays Facenet512**. Its gain on 2026-11-16 (0.88 against 0.62) did not carry over to 20 new people and a new crawl: the earlier result was most likely the luck of choosing the best of 12 combinations on 10 people, plus labels completed from Facenet512's matches. The other 17 combinations on this set are descriptive only, and none beats the baseline on both numbers either (`eval-snapshots/`, local).

**The fitted ArcFace threshold, for information.** The cutoff fitted on 2026-11-16 alone is 0.47 (`scripts/experiments/fit_threshold.py`, pairs in [`threshold-fit.csv`](../queries/eval/threshold-fit.csv)). Read out of sample on 2026-11-17, it finds 19 of 58 true pages with 1 wrong page: 4 of 10 people reach recall 0.80, 9 of 10 get no extra page, and all 10 absent people return nothing. It is not adopted, because the model it belongs to was not confirmed.

## Improvement log

| Date | Change | Result on the 2026-11-16 set | Status |
|---|---|---|---|
| 2026-11-17 | Face-size filter `--min-face 40` | Same 4 false matches, one true page lost | Not adopted |
| 2026-11-17 | CSLS, T-norm, query expansion (Facenet512 and ArcFace) | Lower recall at zero false matches in every case; AUC also lower, except Facenet512 with query expansion (+0.002) | Not adopted |
| 2026-11-17 | ArcFace, raw cosine, no re-ranking | ArcFace: recall at zero false matches 0.88 (14 of 16), separable 5/5, absent people below every true page 2/5, AUC 0.999. Baseline Facenet512: 0.62 (10 of 16), 4/5, 0/5, AUC 0.994. No single threshold passes with either | **Not confirmed** on the 2026-11-17 set (below); the default stays Facenet512 |

## Labelling method and its limits

How each set was labelled (browsing, searching or both), by whom and on which crawl date is in [`queries/eval/methods.csv`](../queries/eval/methods.csv). The labeller keeps a snapshot of that crawl's index locally, so the numbers can be re-run; it is never committed, because it holds face embeddings of people who gave no licence.

- **Recall is only as good as the labels.** A page the labeller did not find is invisible to the measurement.
- **The 2026-11-16 labels lean towards Facenet512.** They were completed by reviewing Facenet512's matches and near misses (`face-matcher --details`), and some were edited after the first run. A page that only ArcFace ranks high was never reviewed, so it may be scored as a wrong page for ArcFace. The experiments' grid was fixed before the run; the labels were not. The 2026-11-17 set used candidates from Facenet512 too (see its bias note).
- **The sample is small** and limited to public figures with openly licensed photos.
- **Rendition checks are manual.** That a query photo is not a copy of an indexed image is checked in review, from the attribution's source page.
- **Results are tied to one crawl.** The site changes hourly. On an index crawled on another date, labelled pages that are gone are reported as label errors and the run fails; that is drift, not a search failure. To measure a new crawl, add a new label set for it ([quickstart §5](../specs/003-evaluation-set/quickstart.md)).

## Correcting a set or taking a photo down

A set may be corrected (a labelling mistake, a wrong licence, a takedown request) as long as every set keeps its minimums: a removed label is replaced in the same change. After a change to the latest set or one of its photos, that set needs a fresh logged run before CI passes, so these results never describe inputs that have since changed. Attribution or methods corrections need no new run, since they change no result ([quickstart §5b](../specs/003-evaluation-set/quickstart.md)). Deleting a photo does not remove it from git history; for a takedown, a maintainer also rewrites the history on the remote.
