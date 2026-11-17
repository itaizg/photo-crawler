# Notes: 003 Search Evaluation Set

## First real run (T018, T031), 2026-11-17

- The set is 2026-11-16 (browsing, one labeller). The run used the labeller's local snapshot of that crawl: 15,822 faces, 5,160 images and 97 pages.
- The verdict is FAIL. The table and the reading against SC-001 to SC-003 are in the README, under "Search accuracy evaluation". Every query photo had a usable face, and there were no label errors.
- Surprise: 8 extra pages for 3 present people, all section fronts that reuse article photos. Each person was labelled on one page only, so these are probably missing labels, not false matches. They are not verified yet.
- Surprise: 2 present people were missed on their only labelled page (person-b, person-d). It is not yet known whether their faces were not detected or were detected but too far from the query photo.
- Weak hard case: the small-face rows (person-c, person-d) are on a page where the same people also appear large.
- Running on macOS with the repository in iCloud Drive: iCloud marks the virtual environment's `.pth` files hidden, and Python 3.13 skips hidden `.pth` files, so `import face_index` fails. Workaround: `chflags nohidden .venv/lib/python3.13/site-packages/*.pth`. Better: keep the repository, or `UV_PROJECT_ENVIRONMENT`, outside iCloud.
