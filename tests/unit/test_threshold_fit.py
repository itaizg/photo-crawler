from datetime import date
from pathlib import Path

import pytest

from face_index import eval_set, model_profile
from face_index.eval_set import FitPair

EVAL = Path(__file__).resolve().parents[2] / "queries" / "eval"


def pair(labelled: bool, distance: float | None, model: str = "ArcFace") -> FitPair:
    return FitPair(
        model=model,
        label_set=date(2026, 11, 16),
        label="person-a",
        page_url="https://edition.cnn.com/world",
        labelled=labelled,
        distance=distance,
    )


def test_cutoff_separates_the_labelled_from_the_rest() -> None:
    pairs = [pair(True, 0.40), pair(True, 0.52), pair(False, 0.61), pair(False, 0.70)]
    # Strictly below the cutoff is a match: 0.53 .. 0.61 all keep both labelled pairs and no other.
    assert eval_set.fit_threshold(pairs) == 0.53


def test_a_tie_goes_to_the_lowest_cutoff() -> None:
    assert eval_set.fit_threshold([pair(True, 0.20), pair(False, 0.50)]) == 0.21


def test_a_match_is_strictly_below_the_cutoff() -> None:
    # A labelled pair at exactly 0.30 is not matched by 0.30, so the fit must go above it.
    assert eval_set.fit_threshold([pair(True, 0.30), pair(False, 0.60)]) == 0.31


def test_a_labelled_page_never_found_counts_as_a_miss_at_every_cutoff() -> None:
    pairs = [pair(True, 0.30), pair(True, None), pair(False, 0.55)]
    assert eval_set.fit_threshold(pairs) == 0.31  # F1 = 2/3 here; reaching 0.55 only adds an FP


def test_pooling_trades_one_set_against_the_other() -> None:
    near = [pair(True, 0.30), pair(False, 0.45)]
    far = [pair(True, 0.50), pair(False, 0.65)]
    # Both labelled pairs fit under 0.51 with one false match (0.45): F1 = 4/5 beats 2/3 at 0.31.
    assert eval_set.fit_threshold(near + far) == 0.51


def test_no_labelled_pages_is_an_error() -> None:
    with pytest.raises(ValueError, match="labelled"):
        eval_set.fit_threshold([pair(False, 0.5)])


def test_a_pair_without_a_distance_must_be_labelled() -> None:
    with pytest.raises(ValueError, match="labelled"):
        pair(False, None)


def test_the_committed_fit_is_the_match_threshold() -> None:
    """The cutoff is not a typed-in number: it is the fit of the committed pairs.

    Changing the model, the labels or the threshold without refitting
    (scripts/experiments/fit_threshold.py) fails here, as the DeepFace comparison used to.
    """
    pairs, errors = eval_set.read_threshold_fit(EVAL)
    assert errors == []
    assert {p.model for p in pairs} == {model_profile.MODEL}
    assert eval_set.fit_threshold(pairs) == model_profile.MATCH_THRESHOLD
