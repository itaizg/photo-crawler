"""The evaluation set under ``queries/eval/``: read, validate and select label sets (feature 003).

File formats: ``specs/003-evaluation-set/contracts/eval-set-files.md``. Every file is read as text
with pandas (no type inference) and each row becomes a frozen model once, at load (constitution
IX). Bad data never raises out of this module: every rule returns its errors as
``<file>:<line>: <message>`` strings (line 1 is the header), and ``read_folder`` returns all of
them at once, so collecting errors needs no exception handling (constitution VIII).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import pandas as pd
from pydantic import StringConstraints, ValidationError, field_validator, model_validator

from face_index import config
from face_index.models import Record
from face_index.urls import host_of, normalize_page_url

SET_COLUMNS: Final[tuple[str, ...]] = ("label", "page_url", "hard_case", "lookalike_of")

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


def _read_set(root: Path, path: Path) -> tuple[LabelSet | None, list[str]]:
    name = path.relative_to(root).as_posix()
    if path.suffix != ".csv" or not _SET_NAME.match(path.stem):
        return None, [f"{name}: a set file is named by its crawl date, YYYY-MM-DD.csv"]
    try:
        crawl_date = date.fromisoformat(path.stem)
    except ValueError:
        return None, [f"{name}: {path.stem} is not a valid date"]
    raw, errors = _read_csv(path, name, SET_COLUMNS)
    rows, row_errors = _parse_rows(SetRow, raw, name)
    errors += row_errors + _set_rule_errors(name, rows)
    for label in dict.fromkeys(row.label for _, row in rows):
        if not (root / photo_path(label)).is_file():
            errors.append(f"{name}: {label} has no photo at {photo_path(label)}")
    label_set = LabelSet(crawl_date=crawl_date, rows=tuple(row for _, row in rows))
    return (None if errors else label_set), errors


def _read_sets(root: Path) -> tuple[list[LabelSet], list[str]]:
    files = sorted(p for p in (root / "sets").glob("*") if p.is_file())
    if not files:
        return [], ["sets/: no label set; add sets/<crawl date>.csv"]
    sets: list[LabelSet] = []
    errors: list[str] = []
    for path in files:
        label_set, set_errors = _read_set(root, path)
        errors += set_errors
        if label_set is not None:
            sets.append(label_set)
    return sets, errors


def read_folder(root: Path) -> tuple[EvalFolder | None, tuple[str, ...]]:
    """Every file under ``root`` validated together: the folder, or None and every error found."""
    sets, errors = _read_sets(root)
    if errors:
        return None, tuple(errors)
    return EvalFolder(root=root, sets=tuple(sorted(sets, key=lambda s: s.crawl_date))), ()
