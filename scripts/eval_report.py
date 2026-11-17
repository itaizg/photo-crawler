"""Render the committed evaluation results as one self-contained HTML page (feature 003, R12).

Reads only the files under queries/eval/ (no database, no model), so CI can run it on every pull
request and attach the page to the run:

    uv run python scripts/eval_report.py [FOLDER] --out eval-report.html [--summary FILE]

``--summary`` also appends a short Markdown version, e.g. to ``$GITHUB_STEP_SUMMARY``. The page
shows the latest logged run of the latest label set, the distance of every logged page, and the
run history. Exit status 0 when the page was written, 2 when the files are invalid (the page then
lists the errors).
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from typing import Final

from face_index import eval_set, model_profile
from face_index.eval_set import DistanceRow, RunRow

EXIT_OK: Final[int] = 0
EXIT_INVALID: Final[int] = 2
MIN_RECALL: Final[float] = eval_set.MIN_RECALL
CEILING: Final[float] = eval_set.REPORT_CEILING  # pages beyond it are logged without a distance

type Run = tuple[RunRow, ...]

STYLE: Final[str] = """
:root {
  color-scheme: light;
  --surface: #fcfcfb; --page: #f4f4f1; --border: #e2e1dc;
  --text: #0b0b0b; --text-2: #52514e; --text-3: #6f6e69; --grid: #e2e1dc;
  --true: #2a78d6; --wrong: #eb6834; --good: #0a7d0a; --bad: #b42f2f;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --page: #121211; --border: #34332f;
    --text: #f0efec; --text-2: #c3c2b7; --text-3: #9c9b92; --grid: #34332f;
    --true: #3987e5; --wrong: #d95926; --good: #3fbf3f; --bad: #f07272;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --page: #121211; --border: #34332f;
  --text: #f0efec; --text-2: #c3c2b7; --text-3: #9c9b92; --grid: #34332f;
  --true: #3987e5; --wrong: #d95926; --good: #3fbf3f; --bad: #f07272;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1000px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 8px; }
p, li { color: var(--text-2); }
.meta { color: var(--text-3); font-size: 13px; }
.meta code { word-break: break-all; }
.nowrap, .verdict { white-space: nowrap; }
.chart { min-width: 640px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin: 12px 0; overflow-x: auto; }
.verdict { display: inline-flex; gap: 6px; align-items: center; font-weight: 600;
  border-radius: 999px; padding: 2px 12px; border: 1.5px solid currentColor; }
.verdict.pass { color: var(--good); } .verdict.fail { color: var(--bad); }
.criteria { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.criteria div { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px; }
.criteria b { display: block; font-size: 13px; color: var(--text-2); font-weight: 500; }
.criteria span { font-size: 20px; font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
th { color: var(--text-2); font-weight: 500; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.url { word-break: break-all; }
.warn { border-left: 4px solid var(--bad); padding: 8px 12px; background: var(--surface); }
.legend { display: flex; gap: 20px; flex-wrap: wrap; font-size: 13px; color: var(--text-2);
  margin-bottom: 8px; }
.legend svg { vertical-align: -2px; margin-right: 6px; }
svg text { fill: var(--text-2); font: 12px system-ui, sans-serif; }
svg .axis { stroke: var(--grid); }
svg .threshold { stroke: var(--text-2); stroke-dasharray: 4 3; stroke-width: 1.5; }
svg .true { fill: var(--true); stroke: var(--surface); stroke-width: 2; }
svg .wrong { fill: var(--wrong); stroke: var(--surface); stroke-width: 2; }
details summary { cursor: pointer; color: var(--text-2); }
"""


def _blocks(rows: Sequence[RunRow]) -> list[Run]:
    """The run log split into runs: consecutive rows sharing a time, set and labels version."""
    runs: list[list[RunRow]] = []
    for row in rows:
        key = (row.run_at, row.label_set, row.labels_sha256)
        if runs and (runs[-1][0].run_at, runs[-1][0].label_set, runs[-1][0].labels_sha256) == key:
            runs[-1].append(row)
        else:
            runs.append([row])
    return [tuple(r) for r in runs]


def _recall(row: RunRow) -> float | None:
    if eval_set.role(row.label) == "absent":
        return None
    return (row.expected - row.missed) / row.expected if row.expected else 1.0


def _verdict(verdict: str) -> str:
    mark = "✓" if verdict == "PASS" else "✗"
    return f'<span class="verdict {verdict.lower()}">{mark} {verdict}</span>'


def _criteria(run: Run) -> list[tuple[str, str, bool]]:
    present = [r for r in run if eval_set.role(r.label) == "present"]
    absent = [r for r in run if eval_set.role(r.label) == "absent"]
    found = sum(1 for r in present if (_recall(r) or 0.0) >= MIN_RECALL)
    extra = sum(r.extra for r in present)
    matched = sum(1 for r in absent if r.extra)
    return [
        (
            f"People found on ≥ {MIN_RECALL:.0%} of their pages",
            f"{found} of {len(present)}",
            found == len(present),
        ),
        ("Extra pages for present people", str(extra), extra == 0),
        ("Absent people with any match", f"{matched} of {len(absent)}", matched == 0),
    ]


def _criteria_html(run: Run) -> str:
    cells = "".join(
        f"<div><b>{escape(name)}</b><span>{escape(value)}</span> "
        f'<span class="meta">{"met" if ok else "not met"}</span></div>'
        for name, value, ok in _criteria(run)
    )
    return f'<div class="criteria">{cells}</div>'


def _label_table(run: Run) -> str:
    head = (
        "<tr><th>label</th><th class=num>expected</th><th class=num>returned</th>"
        "<th class=num>recall</th><th class=num>missed</th><th class=num>extra</th>"
        "<th class=num>label errors</th></tr>"
    )
    body = "".join(
        f"<tr><td>{escape(r.label)}</td><td class=num>{r.expected}</td>"
        f"<td class=num>{r.returned}</td>"
        f"<td class=num>{'-' if (rc := _recall(r)) is None else f'{rc:.2f}'}</td>"
        f"<td class=num>{r.missed}</td><td class=num>{r.extra}</td>"
        f"<td class=num>{r.label_errors}</td></tr>"
        for r in run
    )
    return f'<div class="card"><table>{head}{body}</table></div>'


# --- distance chart -----------------------------------------------------------------------------

_LEFT: Final[int] = 96
_PLOT: Final[int] = 560
_LANE: Final[int] = 104  # the "not found" lane beyond the ceiling
_ROW: Final[int] = 30
_TOP: Final[int] = 28


def _x(distance: float | None) -> float:
    if distance is None:
        return _LEFT + _PLOT + 64
    return _LEFT + min(distance, CEILING) / CEILING * _PLOT


def _mark(row: DistanceRow, y: float) -> str:
    x = _x(row.distance)
    where = "not found up to 0.50" if row.distance is None else f"distance {row.distance:.3f}"
    kind = "labelled page" if row.labelled else "not a labelled page"
    tip = f"<title>{escape(row.label)} · {escape(row.page_url)} · {where} · {kind}</title>"
    if row.labelled:
        return f'<circle class="true" cx="{x:.1f}" cy="{y:.1f}" r="5">{tip}</circle>'
    s = 6
    points = f"{x:.1f},{y - s:.1f} {x + s:.1f},{y:.1f} {x:.1f},{y + s:.1f} {x - s:.1f},{y:.1f}"
    return f'<polygon class="wrong" points="{points}">{tip}</polygon>'


def _chart(labels: Sequence[str], rows: Sequence[DistanceRow]) -> str:
    height = _TOP + len(labels) * _ROW + 24
    width = _LEFT + _PLOT + _LANE + 8
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img" '
        'aria-label="Best distance of each page to each label\'s query photo">'
    ]
    bottom = _TOP + len(labels) * _ROW
    for tick in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
        x = _x(tick)
        parts.append(
            f'<line class="axis" x1="{x:.1f}" x2="{x:.1f}" y1="{_TOP - 6}" y2="{bottom}"/>'
        )
        parts.append(f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle">{tick:.1f}</text>')
    lane = _x(None)
    parts.append(f'<text x="{lane:.1f}" y="{bottom + 16}" text-anchor="middle">not found</text>')
    t = _x(model_profile.MATCH_THRESHOLD)
    parts.append(
        f'<line class="threshold" x1="{t:.1f}" x2="{t:.1f}" y1="{_TOP - 14}" y2="{bottom}"/>'
    )
    parts.append(
        f'<text x="{t + 4:.1f}" y="{_TOP - 16}">threshold {model_profile.MATCH_THRESHOLD:.2f}'
        " (matches are left of it)</text>"
    )
    for i, label in enumerate(labels):
        y = _TOP + i * _ROW + _ROW / 2
        parts.append(
            f'<text x="{_LEFT - 10}" y="{y + 4:.1f}" text-anchor="end">{escape(label)}</text>'
        )
        own = sorted((r for r in rows if r.label == label), key=lambda r: r.labelled)
        parts += [_mark(r, y) for r in own]  # labelled pages drawn last, on top
    parts.append("</svg>")
    return "".join(parts)


def _legend() -> str:
    circle = '<svg width="12" height="12"><circle class="true" cx="6" cy="6" r="5"/></svg>'
    diamond = (
        '<svg width="14" height="14"><polygon class="wrong" points="7,1 13,7 7,13 1,7"/></svg>'
    )
    return (
        f'<div class="legend"><span>{circle}labelled page (the person is on it)</span>'
        f"<span>{diamond}not a labelled page (a wrong match)</span></div>"
    )


def _distance_table(rows: Sequence[DistanceRow]) -> str:
    body = "".join(
        f"<tr><td>{escape(r.label)}</td><td class=url>{escape(r.page_url)}</td>"
        f"<td>{'yes' if r.labelled else 'no'}</td>"
        f"<td class=num>{'not found' if r.distance is None else f'{r.distance:.3f}'}</td></tr>"
        for r in sorted(rows, key=lambda r: (r.label, r.distance is None, r.distance or 0.0))
    )
    return (
        "<details><summary>All logged distances as a table</summary><table>"
        "<tr><th>label</th><th>page</th><th>labelled</th><th class=num>distance</th></tr>"
        f"{body}</table></details>"
    )


def _distances_html(run: Run, distances: Sequence[DistanceRow]) -> str:
    first = run[0]
    own = [
        d
        for d in distances
        if (d.run_at, d.label_set, d.labels_sha256)
        == (first.run_at, first.label_set, first.labels_sha256)
    ]
    if not own:
        return (
            "<p>This run was logged before page distances were recorded. The next run with "
            "<code>--log</code> records them.</p>"
        )
    return (
        "<p>Each mark is one page's best distance to the label's query photo, up to "
        f"{CEILING:.2f}. A page is returned when it is left of the threshold. Labelled pages "
        "that were not found up to the ceiling sit in the last column. Hover a mark for its "
        "page.</p>"
        f'<div class="card">{_legend()}{_chart([r.label for r in run], own)}</div>'
        f"{_distance_table(own)}"
    )


def _history(runs: Sequence[Run]) -> str:
    rows = []
    for run in reversed(runs):
        first = run[0]
        values = "".join(f"<td class=num>{escape(v)}</td>" for _, v, _ in _criteria(run))
        rows.append(
            f"<tr><td class=nowrap>{first.run_at:%Y-%m-%d %H:%M} UTC</td>"
            f"<td class=nowrap>{first.label_set}</td>"
            f"<td><code>{escape(first.labels_sha256)}</code></td>"
            f"<td>{_verdict(first.verdict)}</td>{values}</tr>"
        )
    return (
        '<div class="card"><table><tr><th>run</th><th>label set</th><th>labels</th>'
        "<th>verdict</th><th class=num>people found</th><th class=num>extra pages</th>"
        f"<th class=num>absent matched</th></tr>{''.join(rows)}</table></div>"
    )


def _page(body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Search Evaluation</title><style>{STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def render(root: Path) -> tuple[str, str, bool]:
    """The HTML page, a Markdown summary, and whether the files were valid."""
    folder, errors = eval_set.read_folder(root)
    if folder is None:
        items = "".join(f"<li>{escape(e)}</li>" for e in errors)
        page = _page(
            f"<h1>Search evaluation</h1><div class=warn><p>The evaluation files are "
            f"invalid, so there are no results to show:</p><ul>{items}</ul></div>"
        )
        return page, "## Search evaluation\n\nThe evaluation files are invalid.\n", False
    latest = eval_set.latest_set(folder)
    runs = [
        r for r in _blocks(eval_set.read_run_log(root)[0]) if r[0].label_set == latest.crawl_date
    ]
    if not runs:
        page = _page(
            f"<h1>Search evaluation</h1><p>Label set {latest.crawl_date} has no logged run yet.</p>"
        )
        return page, f"## Search evaluation\n\nNo logged run for set {latest.crawl_date}.\n", True
    run = runs[-1]
    first = run[0]
    stale = eval_set.stale_results_errors(folder)
    warning = f'<div class="warn"><p>{escape(stale[0])}</p></div>' if stale else ""
    body = (
        f"<h1>Search evaluation {_verdict(first.verdict)}</h1>"
        f'<p class="meta">Label set {first.label_set} (labels <code>{escape(first.labels_sha256)}'
        f"</code>) · run {first.run_at:%Y-%m-%d %H:%M} UTC · profile "
        f"<code>{escape(first.model_profile)}</code> · threshold {first.threshold:.2f} · index "
        f"{first.faces:,} faces, {first.images:,} images, {first.pages:,} pages</p>"
        f"{warning}{_criteria_html(run)}"
        f"<h2>Per label</h2>{_label_table(run)}"
        f"<h2>Distances</h2>{_distances_html(run, eval_set.read_distance_log(root)[0])}"
        f"<h2>Run history</h2>{_history(runs)}"
    )
    return _page(body), _markdown(run, stale), True


def _markdown(run: Run, stale: Iterable[str]) -> str:
    first = run[0]
    lines = [
        f"## Search evaluation: {first.verdict}",
        "",
        f"Label set {first.label_set}, run {first.run_at:%Y-%m-%d %H:%M} UTC, "
        f"threshold {first.threshold:.2f}. The full report with page distances is attached "
        "to this workflow run as `eval-report`.",
        "",
        *(f"> {e}" for e in stale),
        "| label | expected | returned | recall | extra |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {r.label} | {r.expected} | {r.returned} | "
            f"{'-' if (rc := _recall(r)) is None else f'{rc:.2f}'} | {r.extra} |"
            for r in run
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_report", description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", type=Path, default=Path("queries/eval"))
    parser.add_argument("--out", type=Path, required=True, help="HTML file to write")
    parser.add_argument("--summary", type=Path, help="also append a Markdown summary here")
    args = parser.parse_args(argv)
    page, summary, valid = render(args.folder)
    args.out.write_text(page, encoding="utf-8")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as f:
            f.write(summary)
    print(f"wrote {args.out}", file=sys.stderr)
    return EXIT_OK if valid else EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
