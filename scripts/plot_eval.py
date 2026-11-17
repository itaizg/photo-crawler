"""Draw the README's search-accuracy figures from the committed evaluation logs.

    uv run --with matplotlib python scripts/plot_eval.py

Reads queries/eval/distances.csv (each page's best distance up to 0.50, per label and run) and
the label sets, and writes light and dark SVGs to docs/img/. Uses the baseline run of each set:
Facenet512, threshold 0.30, no face-size filter. A pair is (label, page): positive when the
labels say the page shows the person, negative otherwise. Pages past 0.50 are not logged, so the
ROC curve stops at the point for threshold 0.50.
"""

import csv
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib  # ty: ignore[import-not-found]  # not a project dependency: uv run --with

matplotlib.use("svg")
import matplotlib.pyplot as plt  # ty: ignore[import-not-found]

type SetData = tuple[list[float | None], list[float], int, int]
type Theme = Mapping[str, str]

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "queries" / "eval"
OUT = ROOT / "docs" / "img"
PAGES_PER_SET = 97  # pages in each set's crawl snapshot (docs/search-evaluation.md)
DEFAULT = 0.30
CEILING = 0.50
RUNS = {  # baseline run of each set in runs.csv
    "2026-11-16": "2026-11-17T13:19:03Z",
    "2026-11-17": "2026-11-17T18:30:15Z",
}
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "grid": "#e4e3df",
        "s1": "#2a78d6",
        "s2": "#eb6834",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#f0efec",
        "ink2": "#c3c2b7",
        "grid": "#383835",
        "s1": "#3987e5",
        "s2": "#d95926",
    },
}


def load(label_set: str) -> SetData:
    """Return (true-page distances, wrong-page distances, positives, negatives) for one set."""
    labels = {r["label"] for r in csv.DictReader((EVAL / "sets" / f"{label_set}.csv").open())}
    true_pages: dict[str, int] = defaultdict(int)
    for r in csv.DictReader((EVAL / "sets" / f"{label_set}.csv").open()):
        if r["page_url"]:
            true_pages[r["label"]] += 1
    pos_d: list[float | None] = []
    neg_d: list[float] = []
    for r in csv.DictReader((EVAL / "distances.csv").open()):
        if r["label_set"] != label_set or r["run_at"] != RUNS[label_set]:
            continue
        d = float(r["distance"]) if r["distance"] else None
        if r["labelled"] == "true":
            pos_d.append(d)
        elif d is not None:
            neg_d.append(d)
    positives = sum(true_pages.values())
    negatives = len(labels) * PAGES_PER_SET - positives
    assert len(pos_d) == positives, (label_set, len(pos_d), positives)
    return pos_d, neg_d, positives, negatives


def style(ax: Any, t: Theme) -> None:  # noqa: ANN401  # a matplotlib Axes
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["ink2"])
    ax.tick_params(colors=t["ink2"], labelsize=9)
    ax.grid(color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def roc(data: Mapping[str, SetData], t: Theme, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.2), facecolor=t["surface"])
    style(ax, t)
    for (label_set, (pos, neg, p, n)), color in zip(data.items(), (t["s1"], t["s2"]), strict=False):
        cuts = sorted({d for d in pos if d is not None} | set(neg) | {DEFAULT, CEILING})
        xs, ys = [0.0], [0.0]
        for c in cuts:
            xs.append(sum(d < c for d in neg) / n)
            ys.append(sum(d is not None and d < c for d in pos) / p)
        ax.plot(
            xs,
            ys,
            color=color,
            linewidth=2,
            drawstyle="default",
            label=f"set {label_set} ({p} true pages)",
        )
        x0 = sum(d < DEFAULT for d in neg) / n
        y0 = sum(d is not None and d < DEFAULT for d in pos) / p
        ax.plot(
            [x0],
            [y0],
            "o",
            markersize=8,
            color=color,
            markeredgecolor=t["surface"],
            markeredgewidth=2,
            zorder=3,
        )
        ax.annotate(
            f"0.30: {y0:.0%} found",
            (x0, y0),
            xytext=(8, -4),
            textcoords="offset points",
            fontsize=9,
            color=t["ink"],
        )
    ax.set_xlabel(
        "False positive rate (wrong pages / all non-matching pairs)", color=t["ink2"], fontsize=9
    )
    ax.set_ylabel("True positive rate (recall)", color=t["ink2"], fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_xlim(left=0)
    ax.set_title(
        "ROC of the default search (Facenet512), thresholds up to 0.50",
        color=t["ink"],
        fontsize=11,
        loc="left",
    )
    leg = ax.legend(loc="lower right", fontsize=8, frameon=False)
    for txt in leg.get_texts():
        txt.set_color(t["ink"])
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"], bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def distributions(data: Mapping[str, SetData], t: Theme, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.4), sharey=True, facecolor=t["surface"])
    bins = [i / 50 for i in range(5, 23)]  # 0.10 .. 0.44 in steps of 0.02: the overlap
    for ax, (label_set, (pos, neg, p, _)) in zip(axes, data.items(), strict=True):
        style(ax, t)
        shown = [d for d in pos if d is not None]
        ax.hist(
            neg,
            bins=bins,
            color=t["s2"],
            alpha=0.85,
            label="wrong page",
            edgecolor=t["surface"],
            linewidth=1,
        )
        ax.hist(
            shown,
            bins=bins,
            color=t["s1"],
            alpha=0.85,
            label="true page",
            edgecolor=t["surface"],
            linewidth=1,
        )
        ax.axvline(DEFAULT, color=t["ink"], linewidth=1, linestyle="--")
        ax.text(
            DEFAULT - 0.005,
            ax.get_ylim()[1] * 0.92,
            "default 0.30",
            fontsize=8,
            color=t["ink"],
            ha="right",
        )
        beyond = sum(d > bins[-1] for d in shown) + p - len(shown)
        ax.set_title(
            f"Set {label_set}: {p} true pages ({beyond} past 0.44)",
            color=t["ink"],
            fontsize=10,
            loc="left",
        )
        ax.set_xlabel("Best face distance on the page (cosine)", color=t["ink2"], fontsize=9)
    axes[0].set_ylabel("Person-page pairs", color=t["ink2"], fontsize=9)
    leg = axes[0].legend(fontsize=8, frameon=False, loc="upper left")
    for txt in leg.get_texts():
        txt.set_color(t["ink"])
    fig.tight_layout()
    fig.savefig(path, facecolor=t["surface"], bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {s: load(s) for s in RUNS}
    for mode, t in THEMES.items():
        roc(data, t, OUT / f"roc-{mode}.svg")
        distributions(data, t, OUT / f"distances-{mode}.svg")
    for s, (pos, neg, p, n) in data.items():
        found = sum(d is not None and d < DEFAULT for d in pos)
        wrong = sum(d < DEFAULT for d in neg)
        print(f"{s}: {found}/{p} true pages, {wrong} wrong pages at {DEFAULT}; negatives {n}")


if __name__ == "__main__":
    main()
