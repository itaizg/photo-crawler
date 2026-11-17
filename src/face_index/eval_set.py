"""The evaluation set under ``queries/eval/``: read, validate and select label sets (feature 003).

File formats: ``specs/003-evaluation-set/contracts/eval-set-files.md``. Every file is read as text
with pandas (no type inference) and each row becomes a frozen model once, at load (constitution
IX). Bad data never raises out of this module: every rule returns its errors as
``<file>:<line>: <message>`` strings (line 1 is the header), and ``read_folder`` returns all of
them at once, so collecting errors needs no exception handling (constitution VIII).
"""

import csv
import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import pandas as pd
from PIL import Image
from pydantic import (
    AfterValidator,
    Field,
    NonNegativeInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from face_index import config
from face_index.models import Record
from face_index.urls import host_of, normalize_page_url

SET_COLUMNS: Final[tuple[str, ...]] = ("label", "page_url", "hard_case", "lookalike_of")
METHODS_COLUMNS: Final[tuple[str, ...]] = (
    "crawl_date",
    "found_by",
    "labeller",
    "snapshot_holder",
    "onsite_pair",
)
RUN_COLUMNS: Final[tuple[str, ...]] = (
    "run_at",
    "label_set",
    "labels_sha256",
    "model_profile",
    "threshold",
    "min_face_px",
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
DISTANCE_COLUMNS: Final[tuple[str, ...]] = (
    "run_at",
    "label_set",
    "labels_sha256",
    "label",
    "page_url",
    "labelled",
    "distance",
)
ATTRIBUTION_COLUMNS: Final[tuple[str, ...]] = ("path", "source_url", "author", "licence")
ATTRIBUTION_FILE: Final[str] = "attribution.csv"
METHODS_FILE: Final[str] = "methods.csv"
RUNS_FILE: Final[str] = "runs.csv"
DISTANCES_FILE: Final[str] = "distances.csv"
MIN_LABELS: Final[int] = 5  # per role, per set (spec FR-001a, FR-002)
MIN_RECALL: Final[float] = 0.80  # feature 002 SC-001: each present label's recall
# Pages up to this distance are logged with their distance, so a run can be read as a curve
# (research R12). Only pages below the match threshold count as returned.
REPORT_CEILING: Final[float] = 0.50

LABEL_PATTERN: Final[str] = r"^(person|absent)-[a-z]+$"
_SET_NAME: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

type Label = Annotated[str, StringConstraints(pattern=LABEL_PATTERN)]

_FORMULA_PREFIXES: Final[tuple[str, ...]] = ("=", "+", "-", "@", "\t", "\r")


def _plain_text(value: str) -> str:
    """Free text that a spreadsheet will not run as a formula and a terminal will not obey."""
    if value.startswith(_FORMULA_PREFIXES):
        raise ValueError("must not start with =, +, -, @ or a tab (spreadsheet formula)")
    if not value.isprintable():
        raise ValueError("must not contain control characters")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("must be UTC, written with a Z (e.g. 2026-11-20T18:04:11Z)")
    return value


type FreeText = Annotated[str, StringConstraints(min_length=1), AfterValidator(_plain_text)]
type UtcTime = Annotated[datetime, AfterValidator(_utc)]
type Role = Literal["present", "absent"]
type Row = Mapping[str, str]


def role(label: str) -> Role:
    """A label's role comes from its prefix, so it is the same in every set (spec FR-001)."""
    return "present" if label.startswith("person-") else "absent"


def photo_path(label: str) -> str:
    """A label's one query photo, relative to the evaluation folder (spec FR-005)."""
    return f"photos/{label}.jpg"


class HardCase(StrEnum):
    NONE = "none"
    SMALL_FACE = "small_face"
    LOOK_ALIKE = "look_alike"


def _indexed_hosts() -> frozenset[str]:
    return frozenset(host for site in config.ENABLED_SITES for host in site.page_hosts)


class SetRow(Record):
    """One row of ``sets/<crawl date>.csv``: a (present label, page) or an absent label."""

    label: Label
    page_url: str
    hard_case: HardCase
    lookalike_of: Label | None

    @field_validator("lookalike_of", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _check(self) -> Self:
        if role(self.label) == "absent":
            if self.page_url:
                raise ValueError("an absent label has no page_url")
            if self.hard_case is HardCase.SMALL_FACE:
                raise ValueError("small_face marks a page, so it needs a present label")
        else:
            _check_page_url(self.page_url)
        if (self.hard_case is HardCase.LOOK_ALIKE) != (self.lookalike_of is not None):
            raise ValueError("lookalike_of is set exactly when hard_case is look_alike")
        if self.lookalike_of == self.label:
            raise ValueError("lookalike_of must name another label")
        return self


def _check_page_url(url: str) -> None:
    if not url:
        raise ValueError("a present label needs a page_url on every row")
    if not url.isprintable():
        raise ValueError(f"page_url contains control characters: {url}")
    if not url.startswith("https://"):
        raise ValueError(f"page_url must be https: {url}")
    if host_of(url) not in _indexed_hosts():
        raise ValueError(f"page_url is not on an indexed site: {url}")
    if normalize_page_url(url) != url:
        raise ValueError(f"page_url is not canonical; use {normalize_page_url(url)}")


class LabelSet(Record):
    """The labels of one labelling crawl (``sets/<crawl date>.csv``)."""

    crawl_date: date
    rows: tuple[SetRow, ...]

    @property
    def file(self) -> str:
        return f"sets/{self.crawl_date.isoformat()}.csv"

    def present(self) -> dict[str, tuple[str, ...]]:
        """Each present label with its labelled pages, in file order."""
        pages: dict[str, tuple[str, ...]] = {}
        for row in self.rows:
            if role(row.label) == "present":
                pages[row.label] = (*pages.get(row.label, ()), row.page_url)
        return pages

    def absent(self) -> tuple[str, ...]:
        return tuple(row.label for row in self.rows if role(row.label) == "absent")

    def labels(self) -> tuple[str, ...]:
        """Every label in file order: present labels first as they appear, then absent ones."""
        return (*self.present(), *self.absent())


class EvalFolder(Record):
    """Everything under the evaluation folder, valid as a whole."""

    root: Path
    sets: tuple[LabelSet, ...]  # sorted by crawl date

    def dates(self) -> tuple[date, ...]:
        return tuple(s.crawl_date for s in self.sets)

    def set_for(self, crawl_date: date) -> LabelSet | None:
        return next((s for s in self.sets if s.crawl_date == crawl_date), None)


def latest_set(folder: EvalFolder) -> LabelSet:
    """The default set of a run: the most recent crawl (spec FR-007a)."""
    return folder.sets[-1]


# --- reading -----------------------------------------------------------------------------------


def _field_count_errors(path: Path, name: str) -> list[str]:
    """Every record has as many fields as the header; blank lines are left to row validation."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        width = len(next(reader, []))
        return [
            f"{name}:{reader.line_num}: expected {width} fields, found {len(record)}"
            for record in reader
            if record and len(record) != width
        ]


def _read_csv(
    path: Path, name: str, columns: Sequence[str]
) -> tuple[list[tuple[int, Row]], list[str]]:
    """Rows with their file line numbers, or the errors that stop the file from being read."""
    try:  # file-reading boundary (constitution VIII): a bad file is reported, not raised
        widths = _field_count_errors(path, name)
        if widths:
            return [], widths
        frame = pd.read_csv(
            path,
            dtype="string",
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=False,
            index_col=False,  # never take a column as the index, which would drop its values
        )
    except (csv.Error, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        line = re.search(r"line (\d+)", str(exc))
        return [], [
            f"{name}:{line.group(1) if line else 1}: cannot read the file as CSV "
            f"({type(exc).__name__})"
        ]
    header = [str(c) for c in frame.columns]
    if header != list(columns):
        return [], [f"{name}:1: header must be {','.join(columns)}, got {','.join(header)}"]
    rows: list[tuple[int, Row]] = [
        (i + 2, {str(k): str(v) for k, v in record.items()})
        for i, record in enumerate(frame.to_dict(orient="records"))
    ]
    return rows, []


def _describe(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_url=False):
        where = ".".join(str(p) for p in error["loc"])
        given = error.get("input")
        detail = f" (got {given!r})" if isinstance(given, str) and where else ""
        parts.append(f"{where + ': ' if where else ''}{error['msg']}{detail}")
    return "; ".join(parts)


def _parse[M: Record](model: type[M], row: Row, where: str) -> M | str:
    """The row as a model, or its error: the one ``try``, at the boundary (constitution VIII)."""
    try:
        return model.model_validate(row)
    except ValidationError as exc:
        return f"{where}: {_describe(exc)}"


def _parse_rows[M: Record](
    model: type[M], rows: Sequence[tuple[int, Row]], name: str
) -> tuple[list[tuple[int, M]], list[str]]:
    parsed: list[tuple[int, M]] = []
    errors: list[str] = []
    for line, row in rows:
        match _parse(model, row, f"{name}:{line}"):
            case str() as error:
                errors.append(error)
            case item:
                parsed.append((line, item))
    return parsed, errors


# --- label sets ---------------------------------------------------------------------------------


def _set_rule_errors(name: str, rows: Sequence[tuple[int, SetRow]]) -> list[str]:
    """Rules across the rows of one set: duplicates and look-alike targets."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    absent_seen: set[str] = set()
    present = {row.label for _, row in rows if role(row.label) == "present"}
    for line, row in rows:
        key = (row.label, row.page_url)
        if key in seen:
            errors.append(f"{name}:{line}: duplicate row {row.label},{row.page_url}")
        seen.add(key)
        if role(row.label) == "absent":
            if row.label in absent_seen:
                errors.append(f"{name}:{line}: absent label {row.label} has more than one row")
            absent_seen.add(row.label)
        if row.lookalike_of is not None and row.lookalike_of not in present:
            errors.append(
                f"{name}:{line}: lookalike_of {row.lookalike_of} is not a present label in this set"
            )
    return errors


def _minimum_errors(name: str, rows: Sequence[SetRow]) -> list[str]:
    """Each set's minimums over distinct labels (spec FR-001a to FR-003)."""
    present = {r.label for r in rows if role(r.label) == "present"}
    absent = {r.label for r in rows if role(r.label) == "absent"}
    errors = [
        f"{name}: needs at least {MIN_LABELS} {kind} labels, has {len(labels)}"
        for kind, labels in (("present", present), ("absent", absent))
        if len(labels) < MIN_LABELS
    ]
    if not any(r.hard_case is HardCase.LOOK_ALIKE and role(r.label) == "absent" for r in rows):
        errors.append(f"{name}: needs an absent look-alike (hard_case look_alike on an absent row)")
    if not any(r.hard_case is HardCase.SMALL_FACE for r in rows):
        errors.append(f"{name}: needs a small-face page (hard_case small_face)")
    return errors


def _has_onsite_pair(label_set: LabelSet) -> bool:
    return any(
        r.hard_case is HardCase.LOOK_ALIKE and role(r.label) == "present" for r in label_set.rows
    )


def _set_date(name: str, path: Path) -> date | str:
    """The crawl date a set file is named by, or the naming error."""
    if path.suffix != ".csv" or not _SET_NAME.match(path.stem):
        return f"{name}: a set file is named by its crawl date, YYYY-MM-DD.csv"
    try:  # file-name boundary: a well-formed but impossible date such as 2026-13-40
        return date.fromisoformat(path.stem)
    except ValueError:
        return f"{name}: {path.stem} is not a valid date"


def _read_set(
    root: Path, path: Path, crawl_date: date
) -> tuple[LabelSet | None, list[str], set[str]]:
    name = path.relative_to(root).as_posix()
    raw, errors = _read_csv(path, name, SET_COLUMNS)
    rows, row_errors = _parse_rows(SetRow, raw, name)
    errors += row_errors + _set_rule_errors(name, rows)
    parsed = [row for _, row in rows]
    if not errors:  # minimums over a partly unreadable set would only add noise
        errors += _minimum_errors(name, parsed)
    for label in dict.fromkeys(row.label for row in parsed):
        if not (root / photo_path(label)).is_file():
            errors.append(f"{name}: {label} has no photo at {photo_path(label)}")
    label_set = LabelSet(crawl_date=crawl_date, rows=tuple(parsed))
    return (None if errors else label_set), errors, {row.label for row in parsed}


class _Sets(Record):
    """What the set files yield, valid or not: the cross-file rules need all of it."""

    valid: tuple[LabelSet, ...]
    dates: frozenset[date]  # of every well-named set file
    labels: frozenset[str]  # used by any row that parsed, in any set
    errors: tuple[str, ...]


def _read_sets(root: Path) -> _Sets:
    files = sorted(p for p in (root / "sets").glob("*") if p.is_file())
    if not files:
        no_set = ("sets/: no label set; add sets/<crawl date>.csv",)
        return _Sets(valid=(), dates=frozenset(), labels=frozenset(), errors=no_set)
    sets: list[LabelSet] = []
    dates: set[date] = set()
    labels: set[str] = set()
    errors: list[str] = []
    for path in files:
        name = path.relative_to(root).as_posix()
        match _set_date(name, path):
            case str() as error:
                errors.append(error)
            case crawl_date:
                dates.add(crawl_date)
                label_set, set_errors, set_labels = _read_set(root, path, crawl_date)
                errors += set_errors
                labels |= set_labels
                if label_set is not None:
                    sets.append(label_set)
    return _Sets(
        valid=tuple(sets),
        dates=frozenset(dates),
        labels=frozenset(labels),
        errors=tuple(errors),
    )


# --- methods.csv and runs.csv -------------------------------------------------------------------


def _true_or_false(value: object) -> object:
    if value not in ("true", "false"):
        raise ValueError("must be true or false")
    return value == "true"


class MethodRow(Record):
    """How one label set was labelled (spec FR-025, FR-025a)."""

    crawl_date: date
    found_by: Literal["browse", "search", "both"]
    labeller: FreeText
    snapshot_holder: FreeText
    onsite_pair: bool

    _onsite_pair = field_validator("onsite_pair", mode="before")(_true_or_false)


class RunRow(Record):
    """One label's result in one logged real-model run (spec FR-027)."""

    run_at: UtcTime
    label_set: date
    labels_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
    model_profile: FreeText
    threshold: float
    min_face_px: Annotated[int, Field(ge=0, le=1024)]
    faces: NonNegativeInt
    images: NonNegativeInt
    pages: NonNegativeInt
    label: Label
    expected: NonNegativeInt
    returned: NonNegativeInt
    missed: NonNegativeInt
    extra: NonNegativeInt
    label_errors: NonNegativeInt
    verdict: Literal["PASS", "FAIL"]


def _logged_page_url(url: str) -> str:
    _check_page_url(url)
    return url


class DistanceRow(Record):
    """One page's best distance to one label's query face in one logged run (research R12).

    A logged run has a row for every page within the report ceiling, labelled or not, and for
    every labelled page beyond it (``distance`` empty), so the distances can be read as a curve.
    """

    run_at: UtcTime
    label_set: date
    labels_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
    label: Label
    page_url: Annotated[str, AfterValidator(_logged_page_url)]
    labelled: bool
    distance: Annotated[float, Field(ge=0, le=2)] | None

    @field_validator("labelled", mode="before")
    @classmethod
    def _text_or_bool(cls, value: object) -> object:
        """``true``/``false`` from the file; a bool when the evaluator builds the row."""
        return value if isinstance(value, bool) else _true_or_false(value)

    @field_validator("distance", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value


def _methods_errors(root: Path, set_dates: frozenset[date], sets: Sequence[LabelSet]) -> list[str]:
    """Every set has exactly one methods row, and its onsite_pair agrees with the set."""
    path = root / METHODS_FILE
    if not path.is_file():
        return [f"{METHODS_FILE}: missing; one row per label set"]
    raw, errors = _read_csv(path, METHODS_FILE, METHODS_COLUMNS)
    rows, row_errors = _parse_rows(MethodRow, raw, METHODS_FILE)
    errors += row_errors
    by_date = {s.crawl_date: s for s in sets}
    seen: set[date] = set()
    for line, row in rows:
        where = f"{METHODS_FILE}:{line}"
        if row.crawl_date in seen:
            errors.append(f"{where}: more than one row for {row.crawl_date}")
        seen.add(row.crawl_date)
        if row.crawl_date not in set_dates:
            errors.append(f"{where}: no label set sets/{row.crawl_date}.csv for {row.crawl_date}")
        label_set = by_date.get(row.crawl_date)
        if label_set is not None and row.onsite_pair != _has_onsite_pair(label_set):
            has = "has" if _has_onsite_pair(label_set) else "has no"
            errors.append(
                f"{where}: onsite_pair is {str(row.onsite_pair).lower()}, but {label_set.file} "
                f"{has} on-site look-alike pair"
            )
    if not row_errors:
        errors += [f"sets/{d}.csv: no {METHODS_FILE} row for {d}" for d in sorted(set_dates - seen)]
    return errors


# --- photos and attribution.csv ------------------------------------------------------------------


class Licence(StrEnum):
    """Licences that allow committing the photo with the repository (spec FR-012, research R4)."""

    PD = "PD"
    CC0 = "CC0-1.0"
    CC_BY_2 = "CC-BY-2.0"
    CC_BY_3 = "CC-BY-3.0"
    CC_BY_4 = "CC-BY-4.0"
    CC_BY_SA_2 = "CC-BY-SA-2.0"
    CC_BY_SA_3 = "CC-BY-SA-3.0"
    CC_BY_SA_4 = "CC-BY-SA-4.0"


# Page-ID forms only: none can carry a person's name (spec FR-015, research R4).
SOURCE_URL_FORMS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"https://commons\.wikimedia\.org/w/index\.php\?curid=\d+"),
    re.compile(r"https://www\.flickr\.com/photo\.gne\?id=\d+"),
    re.compile(r"https://unsplash\.com/photos/[A-Za-z0-9_-]{11}"),
)
_PHOTO_NAME: Final[re.Pattern[str]] = re.compile(r"(person|absent)-[a-z]+\.jpg")


class AttributionRow(Record):
    """Where a committed photo comes from, who took it, and its licence (spec FR-013)."""

    path: Annotated[str, StringConstraints(pattern=r"^photos/(person|absent)-[a-z]+\.jpg$")]
    source_url: str
    author: FreeText
    licence: Licence

    @field_validator("source_url")
    @classmethod
    def _page_id_form(cls, url: str) -> str:
        if not any(form.fullmatch(url) for form in SOURCE_URL_FORMS):
            raise ValueError(
                "use the host's page-ID form: commons.wikimedia.org/w/index.php?curid=<n>, "
                "www.flickr.com/photo.gne?id=<n> or unsplash.com/photos/<id>"
            )
        return url


def _attribution_errors(root: Path, used_labels: frozenset[str]) -> list[str]:
    """Photos and attribution rows match one to one, and every photo is used (spec FR-014)."""
    photos_dir = root / "photos"
    files = sorted(p.name for p in photos_dir.glob("*")) if photos_dir.is_dir() else []
    errors = [
        f"photos/{name}: photos are named <label>.jpg, e.g. person-a.jpg"
        for name in files
        if not _PHOTO_NAME.fullmatch(name)
    ]
    photos = {f"photos/{name}" for name in files if _PHOTO_NAME.fullmatch(name)}
    errors += _photo_errors(root, sorted(photos))
    path = root / ATTRIBUTION_FILE
    if not path.is_file():
        return [*errors, f"{ATTRIBUTION_FILE}: missing; one row per photo"]
    raw, read_errors = _read_csv(path, ATTRIBUTION_FILE, ATTRIBUTION_COLUMNS)
    rows, row_errors = _parse_rows(AttributionRow, raw, ATTRIBUTION_FILE)
    errors += read_errors + row_errors
    attributed: set[str] = set()
    for line, row in rows:
        where = f"{ATTRIBUTION_FILE}:{line}"
        if row.path in attributed:
            errors.append(f"{where}: duplicate row for {row.path}")
        attributed.add(row.path)
        if row.path not in photos:
            errors.append(f"{where}: no photo at {row.path}")
    if not read_errors and not row_errors:
        errors += [
            f"{photo}: no attribution row in {ATTRIBUTION_FILE}"
            for photo in sorted(photos - attributed)
        ]
    errors += [
        f"{photo}: no label set uses this photo; remove it or label it"
        for photo in sorted(photos)
        if photo.removeprefix("photos/").removesuffix(".jpg") not in used_labels
    ]
    return errors


def read_run_log(root: Path) -> tuple[list[RunRow], list[str]]:
    """The run log's rows (empty when the file does not exist yet), or its errors."""
    path = root / RUNS_FILE
    if not path.is_file():
        return [], []
    raw, errors = _read_csv(path, RUNS_FILE, RUN_COLUMNS)
    rows, row_errors = _parse_rows(RunRow, raw, RUNS_FILE)
    return [row for _, row in rows], errors + row_errors


def read_distance_log(root: Path) -> tuple[list[DistanceRow], list[str]]:
    """The distance log's rows (empty when the file does not exist yet), or its errors.

    Every row must belong to a run in the run log, so the two files describe the same runs.
    """
    path = root / DISTANCES_FILE
    if not path.is_file():
        return [], []
    raw, errors = _read_csv(path, DISTANCES_FILE, DISTANCE_COLUMNS)
    rows, row_errors = _parse_rows(DistanceRow, raw, DISTANCES_FILE)
    errors += row_errors
    runs, _ = read_run_log(root)
    logged = {(r.run_at, r.label_set, r.labels_sha256) for r in runs}
    errors += [
        f"{DISTANCES_FILE}:{line}: no run at {row.run_at:%Y-%m-%dT%H:%M:%SZ} for label set "
        f"{row.label_set} in {RUNS_FILE}"
        for line, row in rows
        if (row.run_at, row.label_set, row.labels_sha256) not in logged
    ]
    return [row for _, row in rows], errors


def labels_sha256(root: Path, label_set: LabelSet) -> str:
    """The labels version: the set file followed by its photos, in label order (research R7)."""
    digest = hashlib.sha256((root / label_set.file).read_bytes())
    for label in label_set.labels():
        digest.update((root / photo_path(label)).read_bytes())
    return digest.hexdigest()[:12]


def _cells(row: RunRow) -> list[str]:
    values = row.model_dump()
    values["run_at"] = row.run_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    values["label_set"] = row.label_set.isoformat()
    values["threshold"] = f"{row.threshold:.2f}"
    return [str(values[column]) for column in RUN_COLUMNS]


def _distance_cells(row: DistanceRow) -> list[str]:
    return [
        row.run_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        row.label_set.isoformat(),
        row.labels_sha256,
        row.label,
        row.page_url,
        "true" if row.labelled else "false",
        "" if row.distance is None else f"{row.distance:.4f}",
    ]


def _append(path: Path, header: Sequence[str], cells: Sequence[Sequence[str]]) -> None:
    """Append rows, writing the header first if the file is new. Never rewrites."""
    is_new = not path.is_file()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        if is_new:
            writer.writerow(header)
        writer.writerows(cells)


def append_run(path: Path, rows: Sequence[RunRow], distances: Sequence[DistanceRow] = ()) -> None:
    """Append one run to the run log at ``path``, and its page distances to the distance log
    beside it. Neither file is ever rewritten."""
    _append(path, RUN_COLUMNS, [_cells(row) for row in rows])
    if distances:
        _append(
            path.with_name(DISTANCES_FILE),
            DISTANCE_COLUMNS,
            [_distance_cells(r) for r in distances],
        )


def stale_results_errors(folder: EvalFolder) -> list[str]:
    """The latest set's most recent logged run must match its current files (spec FR-001b).

    Only ``--check`` applies this rule: evaluation must still run on a corrected set, since that
    run is what makes the results current again.
    """
    latest = latest_set(folder)
    runs, _ = read_run_log(folder.root)
    logged = [r for r in runs if r.label_set == latest.crawl_date]
    if not logged:
        return [f"{latest.file}: no logged run for the latest set; run the evaluation with --log"]
    last = logged[-1]  # the log is append-only, so file order is run order (same-second runs too)
    current = labels_sha256(folder.root, latest)
    if last.labels_sha256 != current:
        return [
            f"{latest.file}: results are stale: the last logged run used labels "
            f"{last.labels_sha256}, the files are now {current}; run the evaluation with --log"
        ]
    # The last run is the trailing block of rows that share its time and labels version.
    block: list[RunRow] = []
    for row in reversed(logged):
        if (row.run_at, row.labels_sha256) != (last.run_at, last.labels_sha256):
            break
        block.append(row)
    labels = sorted(r.label for r in block)
    if labels != sorted(latest.labels()) or len({r.verdict for r in block}) != 1:
        return [
            f"{latest.file}: the last logged run is incomplete or inconsistent: it needs one row "
            f"per label, all with the same verdict; run the evaluation with --log"
        ]
    return []


_ENTRIES: Final[frozenset[str]] = frozenset(
    {"sets", "photos", ATTRIBUTION_FILE, METHODS_FILE, RUNS_FILE, DISTANCES_FILE}
)


def _layout_errors(root: Path) -> list[str]:
    """Only the documented files may be here, and none may be a link (read before anything else).

    A local index snapshot holds face embeddings of unlicensed people and must never be committed
    (spec FR-025a); a symbolic link could make an error message quote a file from elsewhere.
    """
    links = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_symlink())
    errors = [f"{link}: symbolic links are not allowed in the evaluation folder" for link in links]
    errors += [
        f"{p.name}: not an evaluation file; only sets/, photos/, {ATTRIBUTION_FILE}, "
        f"{METHODS_FILE}, {RUNS_FILE} and {DISTANCES_FILE} belong here "
        "(index snapshots are never committed)"
        for p in sorted(root.iterdir())
        if p.name not in _ENTRIES
    ]
    sets_dir = root / "sets"
    if sets_dir.is_dir():
        errors += [
            f"sets/{p.name}: sets/ holds only set files"
            for p in sorted(sets_dir.iterdir())
            if not p.is_file()
        ]
    return errors


def _photo_errors(root: Path, photos: Sequence[str]) -> list[str]:
    """Every committed photo decodes as an image, so ``--check`` catches a corrupt file."""
    errors: list[str] = []
    for photo in photos:
        try:  # image-decoding boundary (constitution VIII): a bad file is reported, not raised
            with Image.open(root / photo) as image:
                image.verify()
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{photo}: not a decodable image ({type(exc).__name__})")
    return errors


def read_folder(root: Path) -> tuple[EvalFolder | None, tuple[str, ...]]:
    """Every file under ``root`` validated together: the folder, or None and every error found."""
    layout = _layout_errors(root)
    if layout:  # nothing is read from a folder that holds links or unexpected files
        return None, tuple(layout)
    sets = _read_sets(root)
    errors = [
        *sets.errors,
        *_methods_errors(root, sets.dates, sets.valid),
        *_attribution_errors(root, sets.labels),
        *read_run_log(root)[1],
        *read_distance_log(root)[1],
    ]
    if errors:
        return None, tuple(errors)
    return EvalFolder(root=root, sets=tuple(sorted(sets.valid, key=lambda s: s.crawl_date))), ()
