# Contract: `export_image` operator command (FR-009a)

This writes a stored normalized image to a `.jpg` file that is byte-identical to the database copy.

## Invocation

```bash
docker compose exec airflow-scheduler \
  python -m face_index.export_image (--id <image_id> | --sha256 <hex>) [--boxes] -o <path.jpg>
```

| Argument | Required | Meaning |
|---|---|---|
| `--id N` / `--sha256 HEX` | exactly one | Selects the image by primary key or by content fingerprint (64 hex characters) |
| `-o PATH` | yes | Output file. Use `-` for stdout. Parent directories must exist. |
| `--boxes` | no | Instead of the exact bytes, writes a **copy** with each face's box and `face_index` drawn on it. The output is then not byte-identical, and the command says so on stderr. |

## Behavior

| Case | Exit code | Output |
|---|---|---|
| Found, status `indexed` or `pending` | 0 | File written; stderr prints `wrote <path> (<bytes> B, <w>x<h>, <n> faces)` |
| Found, status `no_faces` or `skipped` | 3 | `image <id> has no stored bytes (status=<status>)` |
| Not found | 2 | `no image with <selector>` |
| Bad arguments | 64 | argparse usage |
| Database unreachable | 1 | Error message without connection secrets |

## Guarantee

Without `--boxes`, `sha256(file) == sha256(image.jpeg)`. A test asserts this.
