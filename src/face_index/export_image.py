"""Export a stored image to a .jpg file (FR-009a, contracts/export-image-cli.md).

python -m face_index.export_image (--id N | --sha256 HEX) [--boxes] -o PATH
"""

import argparse
import hashlib
import io
import sys
from collections.abc import Sequence
from typing import NoReturn

from PIL import Image, ImageDraw

from face_index import config
from face_index.errors import InfrastructureError
from face_index.store import ExportedImage, PostgresIndexStore

EXIT_OK, EXIT_DB, EXIT_NOT_FOUND, EXIT_NO_BYTES, EXIT_USAGE = 0, 1, 2, 3, 64


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="python -m face_index.export_image", description=__doc__)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--id", type=int, help="image primary key")
    which.add_argument("--sha256", help="content fingerprint, 64 hex characters")
    parser.add_argument("--boxes", action="store_true", help="draw face boxes on a copy")
    parser.add_argument("-o", "--output", required=True, help="output file, or - for stdout")
    args = parser.parse_args(argv)
    if args.sha256 is not None and (len(args.sha256) != 64 or not _is_hex(args.sha256)):
        parser.error("--sha256 must be 64 hex characters")
    return args


def _is_hex(text: str) -> bool:
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def _with_boxes(image: ExportedImage) -> bytes:
    assert image.jpeg is not None
    picture = Image.open(io.BytesIO(image.jpeg)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for index, x, y, w, h in image.boxes:
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(255, 0, 0), width=2)
        draw.text((x + 3, y + 3), str(index), fill=(255, 0, 0))
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=config.JPEG_QUALITY)
    return out.getvalue()


def main(argv: Sequence[str] | None = None, dsn: str | None = None) -> int:
    args = _parse(argv)
    selector = f"id={args.id}" if args.id is not None else f"sha256={args.sha256}"
    store = PostgresIndexStore(dsn or config.database_dsn())
    try:
        found = store.export_image(args.id, bytes.fromhex(args.sha256) if args.sha256 else None)
    except InfrastructureError as exc:
        print(f"error: {exc}", file=sys.stderr)  # the message never contains the DSN
        return EXIT_DB
    finally:
        store.close()
    if found is None:
        print(f"no image with {selector}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if found.status == "pending":
        print(f"image {found.id} is not indexed yet (status=pending)", file=sys.stderr)
        return EXIT_NO_BYTES
    if found.jpeg is None:
        print(f"image {found.id} has no stored bytes (status={found.status})", file=sys.stderr)
        return EXIT_NO_BYTES

    data = _with_boxes(found) if args.boxes else found.jpeg
    if args.output == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(args.output, "wb") as handle:
            handle.write(data)
    if args.boxes:
        print("note: --boxes output is a modified copy, not byte-identical", file=sys.stderr)
    print(
        f"wrote {args.output} ({len(data)} B, {found.width}x{found.height}, "
        f"{len(found.boxes)} faces, sha256 {hashlib.sha256(data).hexdigest()[:12]})",
        file=sys.stderr,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
