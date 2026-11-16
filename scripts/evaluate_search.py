"""Measure the face search against a hand-labelled label set (feature 003; SC-001 to SC-003).

Evaluate the latest label set in queries/eval/ against the Compose database, as the read-only
role (the password is read from the Compose secrets volume at run time):

    PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" \\
      FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces \\
      uv run python scripts/evaluate_search.py [FOLDER] [--set YYYY-MM-DD] [--log]

Validate every evaluation file, with no database and no model:

    uv run python scripts/evaluate_search.py [FOLDER] --check

File formats and the output are specified in specs/003-evaluation-set/contracts/. The search's
own code runs at the default threshold; for each photo the largest detected face is searched.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, LiteralString, NoReturn

from face_index import config, eval_set, model_profile
from face_index.errors import IndexNotReady, InfrastructureError
from face_index.faces import DeepFaceEmbedder
from face_index.models import QueryFace, Record
from face_index.normalize import PillowNormalizer
from face_index.ports import FaceEmbedder, SearchIndex
from face_index.search import NoFaceError, QueryImageError, prepare_query, search_faces
from face_index.search_store import PostgresSearchIndex

MIN_RECALL: Final[float] = 0.80
DEFAULT_FOLDER: Final[Path] = Path("queries/eval")

EXIT_PASS: Final[int] = 0
EXIT_FAIL: Final[int] = 1
EXIT_CANNOT_EVALUATE: Final[int] = 2
EXIT_USAGE: Final[int] = 64

EPILOG: Final[LiteralString] = """exit statuses:
  0   PASS: every recall >= 0.80, no extra pages, no absent matches, no label errors
  1   FAIL: the verdict was computed and a criterion failed
  2   cannot evaluate: invalid files, unusable photos, no model profile, database unavailable
      (with --check: some file is invalid)
  64  usage error"""


def printable(text: str) -> str:
    """Untrusted text (file values in error messages) with control characters escaped."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


class LabelReport(Record):
    """One label's result in one run (data-model.md, "Label report")."""

    label: str
    expected: int
    returned: int
    missed: tuple[str, ...]
    extra: tuple[str, ...]
    label_errors: tuple[str, ...] = ()

    @property
    def recall(self) -> float:
        return (self.expected - len(self.missed)) / self.expected if self.expected else 1.0


def evaluate_present(
    label: str, pages: Sequence[str], returned: Sequence[str], *, known: frozenset[str]
) -> LabelReport:
    """Labelled pages missing from the index are label errors, not misses (spec FR-018)."""
    got = list(dict.fromkeys(returned))
    indexed = [u for u in pages if u in known]
    return LabelReport(
        label=label,
        expected=len(indexed),
        returned=len(got),
        missed=tuple(u for u in indexed if u not in set(got)),
        extra=tuple(u for u in got if u not in set(pages)),
        label_errors=tuple(u for u in pages if u not in known),
    )


def evaluate_absent(label: str, returned: Sequence[str]) -> LabelReport:
    """Every page returned for someone who is not on the site is a false match (SC-003)."""
    got = tuple(dict.fromkeys(returned))
    return LabelReport(label=label, expected=0, returned=len(got), missed=(), extra=got)


def passes(reports: Iterable[LabelReport]) -> bool:
    return all(not r.label_errors and not r.extra and r.recall >= MIN_RECALL for r in reports)


def _faces(
    root: Path, labels: Sequence[str], embedder: FaceEmbedder
) -> dict[str, QueryFace] | None:
    """The largest face of each label's photo, or None after listing every unusable photo."""
    normalizer = PillowNormalizer()
    faces: dict[str, QueryFace] = {}
    problems: list[str] = []
    for label in labels:
        path = eval_set.photo_path(label)
        try:  # model boundary: one photo's failure is listed, the others still run (FR-019)
            _, detected = prepare_query((root / path).read_bytes(), normalizer, embedder)
        except NoFaceError:
            problems.append(f"{path}: no face detected")
            continue
        except QueryImageError as exc:
            problems.append(f"{path}: {exc}")
            continue
        faces[label] = max(detected, key=lambda f: f.box.w * f.box.h)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return None if problems else faces


def _pages(face: QueryFace, index: SearchIndex, profile_id: int) -> list[str]:
    [result] = search_faces([face], index, profile_id, model_profile.MATCH_THRESHOLD)
    return [p.url for p in result.pages]


def _print_report(reports: Sequence[LabelReport], ok: bool) -> None:
    print(f"{'label':<14}{'expected':>9}{'returned':>9}{'recall':>8}{'extra':>7}{'errors':>8}")
    for r in reports:
        recall = f"{r.recall:.2f}" if eval_set.role(r.label) == "present" else "-"
        print(
            f"{r.label:<14}{r.expected:>9}{r.returned:>9}{recall:>8}"
            f"{len(r.extra):>7}{len(r.label_errors):>8}"
        )
        for url in r.missed:
            print(f"    missed: {printable(url)}")
        for url in r.extra:
            print(f"    extra:  {printable(url)}")
        for url in r.label_errors:
            print(f"    label error (page not indexed): {printable(url)}")
    print("PASS" if ok else "FAIL")


def _select(folder: eval_set.EvalFolder, crawl_date: date | None) -> eval_set.LabelSet | None:
    if crawl_date is None:
        return eval_set.latest_set(folder)
    chosen = folder.set_for(crawl_date)
    if chosen is None:
        available = ", ".join(d.isoformat() for d in folder.dates())
        print(f"error: no label set {crawl_date}; available: {available}", file=sys.stderr)
    return chosen


def _load(root: Path, crawl_date: date | None) -> eval_set.LabelSet | int:
    """The label set to evaluate, or the exit status after printing why there is none."""
    folder, errors = eval_set.read_folder(root)
    if folder is None:
        for error in errors:
            print(f"error: {printable(error)}", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    label_set = _select(folder, crawl_date)
    return EXIT_USAGE if label_set is None else label_set


def run(
    root: Path,
    crawl_date: date | None,
    index: SearchIndex,
    embedder: FaceEmbedder,
    *,
    log_to: Path | None = None,
) -> int:
    """Evaluate one label set (the latest by default). Tests pass ``log_to=None``."""
    match _load(root, crawl_date):
        case int() as status:
            return status
        case label_set:
            return _evaluate(root, label_set, index, embedder, log_to=log_to)


def _evaluate(
    root: Path,
    label_set: eval_set.LabelSet,
    index: SearchIndex,
    embedder: FaceEmbedder,
    *,
    log_to: Path | None,
) -> int:
    run_at = datetime.now(UTC).replace(microsecond=0)
    profile_id = index.profile_id(model_profile.KEY)
    if profile_id is None:
        print(f"error: no faces for profile {model_profile.KEY}", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    size = index.index_size(profile_id)
    if size.faces == 0:
        print(
            f"error: the index has no faces for profile {model_profile.KEY}; run the pipeline",
            file=sys.stderr,
        )
        return EXIT_CANNOT_EVALUATE
    version = eval_set.labels_sha256(root, label_set)  # before any photo is read
    present = label_set.present()
    known = index.known_pages([u for pages in present.values() for u in pages])

    faces = _faces(root, label_set.labels(), embedder)
    if faces is None:
        return EXIT_CANNOT_EVALUATE
    reports = [
        evaluate_present(label, pages, _pages(faces[label], index, profile_id), known=known)
        for label, pages in present.items()
    ] + [
        evaluate_absent(label, _pages(faces[label], index, profile_id))
        for label in label_set.absent()
    ]

    if eval_set.labels_sha256(root, label_set) != version:
        print(
            "error: the label set or its photos changed during the run; run it again",
            file=sys.stderr,
        )
        return EXIT_CANNOT_EVALUATE
    print(
        f"label set {label_set.crawl_date} (labels {version}), profile {model_profile.KEY}, "
        f"threshold {model_profile.MATCH_THRESHOLD:.2f}"
    )
    print(f"index: {size.faces} faces, {size.images} images, {size.pages} pages")
    print()
    ok = passes(reports)
    _print_report(reports, ok)
    if log_to is not None:
        verdict = "PASS" if ok else "FAIL"
        eval_set.append_run(
            log_to,
            [
                eval_set.RunRow(
                    run_at=run_at,
                    label_set=label_set.crawl_date,
                    labels_sha256=version,
                    model_profile=model_profile.KEY,
                    threshold=model_profile.MATCH_THRESHOLD,
                    faces=size.faces,
                    images=size.images,
                    pages=size.pages,
                    label=r.label,
                    expected=r.expected,
                    returned=r.returned,
                    missed=len(r.missed),
                    extra=len(r.extra),
                    label_errors=len(r.label_errors),
                    verdict=verdict,
                )
                for r in reports
            ],
        )
        print(f"logged to {log_to}", file=sys.stderr)
    return EXIT_PASS if ok else EXIT_FAIL


def check(root: Path) -> int:
    """Validate every evaluation file, and that the latest results are current; no database."""
    folder, errors = eval_set.read_folder(root)
    if folder is not None:
        errors = tuple(eval_set.stale_results_errors(folder))
    for error in errors:
        print(printable(error))
    if errors:
        return EXIT_CANNOT_EVALUATE
    print(f"ok {root}")
    return EXIT_PASS


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(
        prog="evaluate_search",
        description="Measure the face search against a hand-labelled label set.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=DEFAULT_FOLDER,
        help=f"evaluation folder (default: {DEFAULT_FOLDER})",
    )
    parser.add_argument(
        "--set",
        dest="crawl_date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="label set to evaluate (default: the latest)",
    )
    parser.add_argument("--log", action="store_true", help="append this run to runs.csv")
    parser.add_argument("--check", action="store_true", help="only validate the files")
    args = parser.parse_args(argv)
    if args.log and args.check:
        parser.error("--log and --check cannot be combined")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    root: Path = args.folder
    if root.suffix == ".toml":
        print(
            "error: labels are now CSV label sets under queries/eval/sets/ "
            "(specs/003-evaluation-set/quickstart.md)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not root.is_dir():
        print(f"error: {root} is not a folder", file=sys.stderr)
        return EXIT_USAGE
    if args.check:
        return check(root)
    label_set = _load(root, args.crawl_date)  # every file and option is checked before the database
    if isinstance(label_set, int):
        return label_set
    log_to = root / "runs.csv" if args.log else None
    try:  # configuration boundary; the DSN itself is never printed
        dsn = config.database_dsn()
    except RuntimeError as exc:  # FACE_INDEX_DSN not set
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    try:  # infrastructure boundary
        with closing(PostgresSearchIndex(dsn)) as index:
            return _evaluate(root, label_set, index, DeepFaceEmbedder(), log_to=log_to)
    except (InfrastructureError, IndexNotReady) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE


if __name__ == "__main__":
    raise SystemExit(main())
