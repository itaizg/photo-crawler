"""Measure the face search against hand-labelled photos (SC-001 to SC-003, research R8).

    FACE_INDEX_DSN=postgresql://faces@127.0.0.1:5432/faces \\
      uv run python scripts/evaluate_search.py queries/eval/labels.toml

The labels file (TOML) lists people on the site, with the pages they appear on, and people who
are not on the site. Photo paths are relative to the labels file:

    [[person]]
    name = "person-a"          # a neutral label, never a real name in committed files
    photo = "person-a.jpg"     # not taken from the indexed site
    pages = ["https://edition.cnn.com/...", "..."]

    [[absent]]
    photo = "absent-1.jpg"

It uses the search's own code at the default threshold. For each photo the largest detected face
is searched. Exit status 0 when every person's recall is at least 0.80 (SC-001), no person gets a
page they are not labelled on (SC-002), and no absent person gets any page (SC-003); 1 otherwise.
"""

import sys
import tomllib
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from face_index import config, model_profile
from face_index.faces import DeepFaceEmbedder
from face_index.models import Record
from face_index.normalize import PillowNormalizer
from face_index.ports import FaceEmbedder, SearchIndex
from face_index.search import prepare_query, search_faces
from face_index.search_store import PostgresSearchIndex

MIN_RECALL = 0.80


class Person(Record):
    name: str
    photo: str
    pages: tuple[str, ...]


class Absent(Record):
    photo: str


class Labels(Record):
    """The labels file, validated once when it is read (constitution IX)."""

    person: tuple[Person, ...] = ()
    absent: tuple[Absent, ...] = ()


class PersonReport(Record):
    name: str
    expected: int
    returned: int
    missed: tuple[str, ...]
    extra: tuple[str, ...]

    @property
    def recall(self) -> float:
        return (self.expected - len(self.missed)) / self.expected if self.expected else 1.0


def parse_labels(text: str) -> tuple[tuple[Person, ...], tuple[Absent, ...]]:
    labels = Labels.model_validate(tomllib.loads(text))
    return labels.person, labels.absent


def evaluate_person(person: Person, returned: Sequence[str]) -> PersonReport:
    expected = set(person.pages)
    got = list(dict.fromkeys(returned))
    return PersonReport(
        name=person.name,
        expected=len(expected),
        returned=len(got),
        missed=tuple(u for u in person.pages if u not in set(got)),
        extra=tuple(u for u in got if u not in expected),
    )


def passes(reports: Sequence[PersonReport], absent_pages: Sequence[int]) -> bool:
    return (
        all(r.recall >= MIN_RECALL for r in reports)
        and all(not r.extra for r in reports)
        and all(n == 0 for n in absent_pages)
    )


def _pages_for(
    photo: Path, index: SearchIndex, profile_id: int, embedder: FaceEmbedder
) -> list[str]:
    _, faces = prepare_query(photo.read_bytes(), PillowNormalizer(), embedder)
    largest = max(faces, key=lambda f: f.box.w * f.box.h)
    [result] = search_faces([largest], index, profile_id, model_profile.MATCH_THRESHOLD)
    return [p.url for p in result.pages]


def run(labels: Path, index: SearchIndex, embedder: FaceEmbedder) -> int:
    people, absent = parse_labels(labels.read_text(encoding="utf-8"))
    profile_id = index.profile_id(model_profile.KEY)
    if profile_id is None:
        print(f"error: no faces for profile {model_profile.KEY}", file=sys.stderr)
        return 1
    folder = labels.parent
    reports = [
        evaluate_person(p, _pages_for(folder / p.photo, index, profile_id, embedder))
        for p in people
    ]
    absent_pages = [len(_pages_for(folder / a.photo, index, profile_id, embedder)) for a in absent]

    print(f"{'person':<16}{'expected':>9}{'returned':>9}{'recall':>8}{'extra':>7}")
    for r in reports:
        print(f"{r.name:<16}{r.expected:>9}{r.returned:>9}{r.recall:>8.2f}{len(r.extra):>7}")
        for url in r.missed:
            print(f"    missed: {url}")
        for url in r.extra:
            print(f"    extra:  {url}")
    for a, n in zip(absent, absent_pages, strict=True):
        print(f"absent {a.photo}: {n} pages")
    ok = passes(reports, absent_pages)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 64
    with closing(PostgresSearchIndex(config.database_dsn())) as index:
        return run(Path(args[0]), index, DeepFaceEmbedder())


if __name__ == "__main__":
    raise SystemExit(main())
