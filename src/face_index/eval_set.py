"""The evaluation set under ``queries/eval/``: read, validate and select label sets (feature 003).

File formats: ``specs/003-evaluation-set/contracts/eval-set-files.md``. Every file is read as text
with pandas (no type inference) and each row becomes a frozen model once, at load (constitution
IX). Bad data never raises out of this module: every rule returns its errors as
``<file>:<line>: <message>`` strings (line 1 is the header), and ``read_folder`` returns all of
them at once, so collecting errors needs no exception handling (constitution VIII).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import pandas as pd
from pydantic import (
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
ATTRIBUTION_COLUMNS: Final[tuple[str, ...]] = ("path", "source_url", "author", "licence")
ATTRIBUTION_FILE: Final[str] = "attribution.csv"
METHODS_FILE: Final[str] = "methods.csv"
RUNS_FILE: Final[str] = "runs.csv"
MIN_LABELS: Final[int] = 5  # per role, per set (spec FR-001a, FR-002)

LABEL_PATTERN: Final[str] = r"^(person|absent)-[a-z]+$"
_SET_NAME: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

type Label = Annotated[str, StringConstraints(pattern=LABEL_PATTERN)]
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


def _read_csv(
    path: Path, name: str, columns: Sequence[str]
) -> tuple[list[tuple[int, Row]], list[str]]:
    """Rows with their file line numbers, or the errors that stop the file from being read."""
    try:
        frame = pd.read_csv(
            path, dtype="string", keep_default_na=False, na_filter=False, skip_blank_lines=False
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        return [], [f"{name}:1: cannot read the file as CSV ({type(exc).__name__})"]
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
    labeller: Annotated[str, StringConstraints(min_length=1)]
    snapshot_holder: Annotated[str, StringConstraints(min_length=1)]
    onsite_pair: bool

    _onsite_pair = field_validator("onsite_pair", mode="before")(_true_or_false)


class RunRow(Record):
    """One label's result in one logged real-model run (spec FR-027)."""

    run_at: datetime
    label_set: date
    labels_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
    model_profile: Annotated[str, StringConstraints(min_length=1)]
    threshold: float
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
    author: Annotated[str, StringConstraints(min_length=1)]
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


def read_folder(root: Path) -> tuple[EvalFolder | None, tuple[str, ...]]:
    """Every file under ``root`` validated together: the folder, or None and every error found."""
    sets = _read_sets(root)
    errors = [
        *sets.errors,
        *_methods_errors(root, sets.dates, sets.valid),
        *_attribution_errors(root, sets.labels),
        *read_run_log(root)[1],
    ]
    if errors:
        return None, tuple(errors)
    return EvalFolder(root=root, sets=tuple(sorted(sets.valid, key=lambda s: s.crawl_date))), ()
