"""Scores, page aggregation and metrics for the search experiments.

See docs/briefs/search-experiments.md.

Pure numpy, no database and no model. Every score is "higher is more similar"; raw cosine
distance is ``1 - raw``. The constants are the brief's pre-registered values.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

type Matrix = NDArray[np.float64]
type Score = Literal["raw", "csls", "tnorm"]
type Rerank = Literal["none", "qe", "qe-skip"]

SCORES: Final[tuple[Score, ...]] = ("raw", "csls", "tnorm")
# "qe-skip" was added after the pre-registered grid: query expansion without renditions.
RERANKS: Final[tuple[Rerank, ...]] = ("none", "qe", "qe-skip")
CSLS_K: Final[int] = 10
SAME_PHOTO_DISTANCE: Final[float] = 0.02  # renditions of one photo are not neighbours (brief)
QE_N: Final[int] = 2
_BLOCK: Final[int] = 2048


def normalize(vectors: NDArray[np.floating]) -> Matrix:
    """Rows scaled to unit length, in float64 (pgvector's cosine ignores the length too)."""
    v = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(norms == 0.0, 1.0, norms)


def neighbour_means(
    rows: Matrix, index: Matrix, k: int = CSLS_K, *, skip_same_photo: bool
) -> NDArray[np.float64]:
    """Each row's mean cosine to its ``k`` nearest index faces.

    With ``skip_same_photo`` (rows are the index itself), the face and its renditions, at a
    distance below ``SAME_PHOTO_DISTANCE``, are not counted.
    """
    out = np.empty(len(rows))
    for start in range(0, len(rows), _BLOCK):
        sims = rows[start : start + _BLOCK] @ index.T
        if skip_same_photo:
            sims = np.where(sims > 1.0 - SAME_PHOTO_DISTANCE, -np.inf, sims)
        top = -np.partition(-sims, k - 1, axis=1)[:, :k]
        top = np.where(np.isfinite(top), top, np.nan)
        out[start : start + _BLOCK] = np.nanmean(top, axis=1)
    return out


def expand(
    queries: Matrix, index: Matrix, n: int = QE_N, *, skip_same_photo: bool = False
) -> Matrix:
    """Average query expansion: each query averaged with its ``n`` closest index faces.

    With ``skip_same_photo``, a face closer than ``SAME_PHOTO_DISTANCE`` to a neighbour already
    taken (another rendition of the same photo) is passed over for the next distinct face.
    """
    sims = queries @ index.T
    order = np.argsort(-sims, axis=1, kind="stable")
    if skip_same_photo:
        top = np.array([_distinct(index, row, n) for row in order], dtype=np.int64)
    else:
        top = order[:, :n]
    return normalize(queries + index[top].sum(axis=1))


def _distinct(index: Matrix, order: NDArray[np.int64], n: int) -> list[int]:
    """The first ``n`` faces in ``order`` that are not renditions of one taken before."""
    taken: list[int] = []
    for face in order:
        if all(index[face] @ index[t] <= 1.0 - SAME_PHOTO_DISTANCE for t in taken):
            taken.append(int(face))
            if len(taken) == n:
                break
    return taken


def scores(
    queries: Matrix,
    index: Matrix,
    score: Score,
    r_index: NDArray[np.float64] | None = None,
    k: int = CSLS_K,
) -> Matrix:
    """The query-by-face score matrix. CSLS needs ``r_index`` (``neighbour_means`` of the index)."""
    cos = queries @ index.T
    match score:
        case "raw":
            return cos
        case "tnorm":
            mean = cos.mean(axis=1, keepdims=True)
            std = cos.std(axis=1, keepdims=True)
            return (cos - mean) / np.where(std == 0.0, 1.0, std)
        case "csls":
            if r_index is None:
                raise ValueError("CSLS needs the index's neighbour means")
            r_query = neighbour_means(queries, index, k, skip_same_photo=False)
            return 2.0 * cos - r_query[:, None] - r_index[None, :]


@dataclass(frozen=True)
class Pages:
    """Which page each (face, page) link belongs to: the search's visibility rules, flattened."""

    urls: tuple[str, ...]
    link_face: NDArray[np.int64]  # face row of each link
    link_page: NDArray[np.int64]  # page column of each link

    @classmethod
    def build(cls, face_image: Sequence[int], image_pages: Mapping[int, Sequence[str]]) -> "Pages":
        urls = tuple(sorted({u for pages in image_pages.values() for u in pages}))
        column = {u: i for i, u in enumerate(urls)}
        faces: list[int] = []
        pages: list[int] = []
        for row, image in enumerate(face_image):
            for url in image_pages.get(int(image), ()):
                faces.append(row)
                pages.append(column[url])
        return cls(urls, np.asarray(faces, dtype=np.int64), np.asarray(pages, dtype=np.int64))

    def best(self, face_scores: Matrix) -> Matrix:
        """Each page's best face score per query; pages with no face are ``-inf``."""
        out = np.full((face_scores.shape[0], len(self.urls)), -np.inf)
        for q in range(face_scores.shape[0]):
            np.maximum.at(out[q], self.link_page, face_scores[q, self.link_face])
        return out


@dataclass(frozen=True)
class Metrics:
    auc: float
    recall_at_zero_false: float
    separable_people: int
    absent_below_all_true: int


def pairs(
    page_scores: Matrix,
    urls: Sequence[str],
    labels: Sequence[str],
    truth: Mapping[str, frozenset[str]],
) -> Iterator[tuple[str, bool, float]]:
    """(label, is_true, score) for every scored page, plus every labelled page.

    A labelled page with no score (not in the index, or no face on it) is a true page scored
    ``-inf``: missed, but still counted in recall. Wrong pages with no score are left out.
    """
    column = {u: i for i, u in enumerate(urls)}
    for q, label in enumerate(labels):
        true_pages = truth.get(label, frozenset())
        for url, i in column.items():
            value = float(page_scores[q, i])
            if url in true_pages:
                yield label, True, value if np.isfinite(value) else -np.inf
            elif np.isfinite(value):
                yield label, False, value
        for _ in true_pages - column.keys():
            yield label, True, -np.inf  # a labelled page the index does not show


def auc(true: Sequence[float], wrong: Sequence[float]) -> float:
    """P(random true pair > random wrong pair), ties counting one half."""
    t = np.asarray(true)[:, None]
    w = np.asarray(wrong)[None, :]
    if t.size == 0 or w.size == 0:
        return float("nan")  # no pairs to compare
    return float(((t > w).sum() + 0.5 * (t == w).sum()) / (t.size * w.size))


def metrics(
    page_scores: Matrix,
    urls: Sequence[str],
    labels: Sequence[str],
    truth: Mapping[str, frozenset[str]],
) -> Metrics:
    rows = list(pairs(page_scores, urls, labels, truth))
    true = [s for _, t, s in rows if t]
    wrong = [s for _, t, s in rows if not t]
    best_wrong = max(wrong, default=-np.inf)
    worst_true = min(true, default=np.inf)
    by_label: dict[str, tuple[list[float], list[float]]] = {}
    for label, is_true, score in rows:
        by_label.setdefault(label, ([], []))[0 if is_true else 1].append(score)
    separable = sum(
        1
        for label, (t, w) in by_label.items()
        if label in truth and t and min(t) > max(w, default=-np.inf)
    )
    absent_ok = sum(
        1
        for label, (_, w) in by_label.items()
        if label not in truth and max(w, default=-np.inf) < worst_true
    )
    return Metrics(
        auc=auc(true, wrong),
        recall_at_zero_false=sum(s > best_wrong for s in true) / len(true) if true else 0.0,
        separable_people=separable,
        absent_below_all_true=absent_ok,
    )
