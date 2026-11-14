"""Find every crawled page that shows the person in a photo (feature 002).

docker compose run --rm face-matcher queries/PHOTO [options]
uv run python -m face_index.face_matcher PHOTO [options]

Output formats and exit statuses: specs/002-face-search/contracts/face-matcher-cli.md.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Annotated, NoReturn

from pydantic import Field, ValidationError

from face_index import config, model_profile
from face_index.drawing import draw_boxes
from face_index.errors import IndexNotReady, InfrastructureError
from face_index.faces import DeepFaceEmbedder
from face_index.models import FaceResult, NormalizedImage, PageMatch, QueryFace, Record
from face_index.normalize import PillowNormalizer
from face_index.ports import FaceEmbedder, ImageNormalizer, SearchIndex
from face_index.search import (
    FaceNumberError,
    NoFaceError,
    QueryImageError,
    prepare_query,
    search_faces,
    select_faces,
)
from face_index.search_store import PostgresSearchIndex

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_BAD_IMAGE = 2
EXIT_NO_FACE = 3
EXIT_INDEX_NOT_READY = 4
EXIT_CANNOT_WRITE = 5
EXIT_USAGE = 64

EPILOG = """exit statuses:
  0   success, including faces with no matching pages
  1   database or face model unavailable
  2   query file problem (missing, unreadable, not a photo, too small or too large)
  3   no face detected in the query photo
  4   index not ready (no faces for the current model profile; run the pipeline)
  5   the annotated copy could not be written
  64  usage error"""


class _Exit(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Options(Record):
    """The command line, validated once at the edge (constitution IX)."""

    image: Path
    threshold: Annotated[float, Field(gt=0.0, le=2.0, allow_inf_nan=False)] = (
        model_profile.MATCH_THRESHOLD
    )
    face: Annotated[int, Field(ge=1)] | None = None
    list_faces: bool = False
    details: bool = False


_RULES = {
    "threshold": "the cosine distance threshold must be in (0, 2]",
    "face": "the face number must be 1 or more",
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _parse(argv: Sequence[str] | None) -> Options:
    parser = _Parser(
        prog="face_matcher",
        description="Print the crawled pages that show the person (or people) in a photo.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("image", help="query photo, e.g. queries/alice.jpg")
    parser.add_argument(
        "--threshold",
        default=None,  # None tells "not given" apart from an explicit 0.30 (--list-faces)
        metavar="T",
        help=(
            "a face matches when its cosine distance is below T, in (0, 2] "
            f"(default: {model_profile.MATCH_THRESHOLD:.2f})"
        ),
    )
    parser.add_argument(
        "--face",
        metavar="N",
        help="search only face N (faces are numbered from 1, left to right)",
    )
    parser.add_argument(
        "--list-faces",
        action="store_true",
        help="print the faces and save <name>.faces.jpg next to the photo; no search",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="also show each page's distance, lead-image mark and matching image URLs",
    )
    args = parser.parse_args(argv)
    if args.list_faces:
        given = [
            name
            for name, used in (
                ("--face", args.face is not None),
                ("--details", args.details),
                ("--threshold", args.threshold is not None),
            )
            if used
        ]
        if given:
            parser.error(f"--list-faces cannot be combined with {', '.join(given)}")
    values = {name: value for name, value in vars(args).items() if value is not None}
    try:
        return Options.model_validate(values)
    except ValidationError as exc:  # user input is a trust boundary: report it as usage
        field = str(exc.errors()[0]["loc"][0])
        parser.error(f"{values[field]!r}: {_RULES[field]}")


def _read(path: Path) -> bytes:
    if not path.exists():
        raise _Exit(
            EXIT_BAD_IMAGE,
            f"{path}: no such file. Put query photos in queries/, "
            "which is shared with the container",
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _Exit(EXIT_BAD_IMAGE, f"{path}: cannot read ({exc.strerror})") from exc


def _check_index(index: SearchIndex) -> int:
    profile_id = index.profile_id(model_profile.KEY)
    if profile_id is None or index.indexed_face_count(profile_id) == 0:
        raise _Exit(
            EXIT_INDEX_NOT_READY,
            f"the index has no faces for profile {model_profile.KEY}; "
            "run the image_indexer pipeline first",
        )
    return profile_id


def _plural(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"


def _page_lines(page: PageMatch, details: bool) -> list[str]:
    if not details:
        return [page.url]
    mark = "lead" if page.lead else "    "
    return [f"{page.best_distance:.3f} {mark} {page.url}"] + [
        f"    image: {url}" for url in page.image_urls
    ]


def _format(results: Sequence[FaceResult], details: bool) -> str:
    groups: list[str] = []
    for result in results:
        box = result.face.box
        found = _plural(len(result.pages)) if result.pages else "no matching pages"
        lines = [f"# face {result.face.number} x={box.x} y={box.y} w={box.w} h={box.h}: {found}"]
        for page in result.pages:
            lines.extend(_page_lines(page, details))
        groups.append("\n".join(lines) + "\n")
    return "\n".join(groups)


def _list_faces(path: Path, normalized: NormalizedImage, faces: Sequence[QueryFace]) -> int:
    """Print each face's box and save the numbered copy next to the photo (FR-012)."""
    noun = "face" if len(faces) == 1 else "faces"
    sys.stdout.write(
        f"# {len(faces)} {noun} in {path} (normalized to {normalized.width}x{normalized.height})\n"
    )
    for face in faces:
        box = face.box
        sys.stdout.write(f"{face.number} x={box.x} y={box.y} w={box.w} h={box.h}\n")
    sys.stdout.flush()
    copy = path.with_name(f"{path.stem}.faces.jpg")
    drawn = draw_boxes(normalized.jpeg, [(str(f.number), f.box) for f in faces])
    try:
        copy.write_bytes(drawn)
    except OSError as exc:
        raise _Exit(EXIT_CANNOT_WRITE, f"cannot write {copy}: {exc.strerror}") from exc
    sys.stdout.write(f"# saved {copy}\n")
    return EXIT_OK


def _run(
    options: Options,
    index_factory: Callable[[], SearchIndex],
    embedder: FaceEmbedder,
    normalizer: ImageNormalizer,
) -> int:
    data = _read(options.image)
    with closing(index_factory()) as index:
        profile_id = _check_index(index)  # before the model loads: a down database fails fast
        print("loading face model…", file=sys.stderr)
        normalized, faces = prepare_query(data, normalizer, embedder)
        if options.list_faces:
            return _list_faces(options.image, normalized, faces)
        chosen = select_faces(faces, options.face)
        results = search_faces(chosen, index, profile_id, options.threshold)
    sys.stdout.write(_format(results, options.details))
    return EXIT_OK


type _Failure = (
    _Exit | QueryImageError | NoFaceError | FaceNumberError | IndexNotReady | InfrastructureError
)


def _status(failure: _Failure, image: Path) -> tuple[int, str]:
    """The one place an expected failure becomes an exit status and a message (contract)."""
    match failure:
        case _Exit(status=status, message=message):
            return status, message
        case QueryImageError():
            return EXIT_BAD_IMAGE, f"{image}: {failure}"
        case NoFaceError():
            return EXIT_NO_FACE, f"no face detected in {image}"
        case FaceNumberError():
            return EXIT_USAGE, str(failure)
        case IndexNotReady():
            return EXIT_INDEX_NOT_READY, f"{failure}; run the image_indexer pipeline first"
        case InfrastructureError():
            return EXIT_UNAVAILABLE, str(failure)  # the message never contains the DSN


def _default_index() -> SearchIndex:
    try:
        return PostgresSearchIndex(config.database_dsn())
    except RuntimeError as exc:  # FACE_INDEX_DSN not set
        raise _Exit(EXIT_UNAVAILABLE, str(exc)) from exc


def main(
    argv: Sequence[str] | None = None,
    *,
    index_factory: Callable[[], SearchIndex] | None = None,
    embedder: FaceEmbedder | None = None,
    normalizer: ImageNormalizer | None = None,
) -> int:
    """Run the search; an unexpected exception is a bug and propagates with its traceback."""
    options = _parse(argv)
    try:
        return _run(
            options,
            index_factory or _default_index,
            embedder or DeepFaceEmbedder(),
            normalizer or PillowNormalizer(),
        )
    except (
        _Exit,
        QueryImageError,
        NoFaceError,
        FaceNumberError,
        IndexNotReady,
        InfrastructureError,
    ) as failure:
        status, message = _status(failure, options.image)
        print(f"error: {message}", file=sys.stderr)
        return status


if __name__ == "__main__":
    raise SystemExit(main())
