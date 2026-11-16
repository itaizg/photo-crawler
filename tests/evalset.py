"""Builders for evaluation folders (feature 003), shared by unit and integration tests.

A default folder is valid: 5 present labels with 2 pages each, 5 absent labels, an absent
look-alike and a small-face page (specs/003-evaluation-set/contracts/eval-set-files.md).
"""

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Final

from tests import images

CRAWL: Final[date] = date(2026, 11, 20)
PRESENT: Final[tuple[str, ...]] = tuple(f"person-{c}" for c in "abcde")
ABSENT: Final[tuple[str, ...]] = tuple(f"absent-{c}" for c in "abcde")

SET_HEADER: Final[tuple[str, ...]] = ("label", "page_url", "hard_case", "lookalike_of")
ATTRIBUTION_HEADER: Final[tuple[str, ...]] = ("path", "source_url", "author", "licence")
METHODS_HEADER: Final[tuple[str, ...]] = (
    "crawl_date",
    "found_by",
    "labeller",
    "snapshot_holder",
    "onsite_pair",
)
RUNS_HEADER: Final[tuple[str, ...]] = (
    "run_at",
    "label_set",
    "labels_sha256",
    "model_profile",
    "threshold",
    "faces",
    "images",
    "pages",
    "label",
    "expected",
    "returned",
    "missed",
    "extra",
    "label_errors",
    "verdict",
)

type Row = tuple[str, ...]


def page(n: int) -> str:
    return f"https://edition.cnn.com/2026/11/20/story-{n}"


def default_rows() -> list[Row]:
    """Two pages per present label (the first one's second page is small), five absent labels."""
    rows: list[Row] = []
    for i, label in enumerate(PRESENT):
        rows.append((label, page(2 * i), "none", ""))
        rows.append((label, page(2 * i + 1), "small_face" if i == 0 else "none", ""))
    rows.append((ABSENT[0], "", "look_alike", PRESENT[0]))
    rows.extend((label, "", "none", "") for label in ABSENT[1:])
    return rows


def write_csv(path: Path, header: Sequence[str], rows: Sequence[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def labels_of(rows: Sequence[Row]) -> list[str]:
    return list(dict.fromkeys(row[0] for row in rows))


def photo_bytes(label: str) -> bytes:
    """A distinct, deterministic photo per label."""
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(label))
    return images.photo(160, 120, seed=seed)


def write_folder(
    root: Path,
    rows: Sequence[Row] | None = None,
    *,
    crawl_date: date = CRAWL,
    onsite_pair: bool = False,
) -> Path:
    """Write a complete evaluation folder: one set, its photos, attribution, methods, run log."""
    rows = default_rows() if rows is None else rows
    write_csv(root / "sets" / f"{crawl_date.isoformat()}.csv", SET_HEADER, rows)
    labels = labels_of(rows)
    for label in labels:
        photo = root / "photos" / f"{label}.jpg"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(photo_bytes(label))
    write_csv(
        root / "attribution.csv",
        ATTRIBUTION_HEADER,
        [
            (
                f"photos/{label}.jpg",
                f"https://commons.wikimedia.org/w/index.php?curid={100 + i}",
                "A Photographer",
                "CC-BY-SA-4.0",
            )
            for i, label in enumerate(labels)
        ],
    )
    write_csv(
        root / "methods.csv",
        METHODS_HEADER,
        [(crawl_date.isoformat(), "both", "labeller", "labeller", str(onsite_pair).lower())],
    )
    write_csv(root / "runs.csv", RUNS_HEADER, [])
    return root
