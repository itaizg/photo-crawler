# Notes: 003 Search Evaluation Set

## First real run (T018, T031), 2026-11-17

- The set is 2026-11-16 (browsing, one labeller). The run used the labeller's local snapshot of that crawl: 15,822 faces, 5,160 images and 97 pages.
- The verdict is FAIL. The table and the reading against SC-001 to SC-003 are in the README, under "Search accuracy evaluation". Every query photo had a usable face, and there were no label errors.
- Surprise: 8 extra pages for 3 present people, all section fronts that reuse article photos. Each person was labelled on one page only, so these are probably missing labels, not false matches. They are not verified yet.
- Surprise: 2 present people were missed on their only labelled page (person-b, person-d). It is not yet known whether their faces were not detected or were detected but too far from the query photo.
- Weak hard case: the small-face rows (person-c, person-d) are on a page where the same people also appear large.
- Running on macOS with the repository in iCloud Drive: iCloud marks the virtual environment's `.pth` files hidden, and Python 3.13 skips hidden `.pth` files, so `import face_index` fails. Workaround: `chflags nohidden .venv/lib/python3.13/site-packages/*.pth`. Better: keep the repository, or `UV_PROJECT_ENVIRONMENT`, outside iCloud.

## Final check (T036), 2026-11-17

- §1, on a fresh clone of `claude/vibrant-gates-qklwhp` at `a231257`: `uv run --locked python scripts/evaluate_search.py --check` printed `ok queries/eval` and exited 0. The quickstart's "one `ok` line per file" was wrong and is corrected.
- §2 was not run locally: this machine has no pgvector test database. CI's `eval-set` job runs the same mock end-to-end tests and passed: https://github.com/itaizg/photo-crawler/actions/runs/44060264471/job/133960013981 (43 s, no secrets).
- §§3 and 4 were run by the labeller on their own clone, against the 2026-11-16 snapshot. The output is in the README, and the logged run is the first block of `queries/eval/runs.csv`.

| Criterion | Met | Evidence |
|---|---|---|
| SC-001 | yes | Set 2026-11-16: 5 present and 5 absent labels; absent look-alike absent-d → person-c; small-face rows person-c and person-d (weak, see above); README notes there is no on-site pair; `--check` passes and the real run loaded with no errors |
| SC-002 | yes | `test_a_bad_row_is_reported_with_its_line`, `test_a_wrong_or_missing_column_is_reported_on_line_1`, `test_a_duplicate_row_is_reported` in `tests/unit/test_eval_set.py` (CI `test` job) |
| SC-003 | yes | `tests/integration/test_evaluate_search_e2e.py` in the CI `eval-set` job above, 43 s, no secrets |
| SC-004 | yes, with a caveat | One command gave the table and FAIL. On macOS with the repository in iCloud, `.pth` files had to be un-hidden first (see above) |
| SC-005 | yes | 10 photos, 10 attribution rows, all Commons `curid=` links with allowed licences; rejection tests in the US3 section of `tests/unit/test_eval_set.py` |
| SC-006 | yes | README "Search accuracy evaluation": results, method, crawl date, bias, re-labelling and correction steps; `specs/002-face-search/quickstart.md` §7 points to the new format |
| SC-007 | yes | `runs.csv` holds one block of 10 rows for the one real run in the README; mock runs pass `log_to=None`; `test_a_bad_run_log_is_reported` |

## Label correction and final run, 2026-11-17

- The labeller reviewed every matched image with `face-matcher --details`, using the review page built for this; results above 0.30 were taken from a 0.45 threshold run. Every extra page from the first run showed the person, so the extras were missing labels, not search errors. 11 pages were added: 8 from extras, and 3 above the threshold (person-b and person-d).
- That took two corrections and two more logged runs. Both times, the stale-results rule turned CI red until a new run was pushed, as designed.
- Final run, 10:34 UTC, labels 9f57e0b488b1: SC-001 fails (person-b 0/3, person-d 0/2), SC-002 passes (no extra pages), SC-003 fails (absent-c 1 page, absent-d 3 pages). True and false match distances overlap between 0.233 and 0.392, so changing the threshold cannot pass. Details are in the README.
