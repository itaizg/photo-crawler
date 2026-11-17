import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

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


# -- scripts/experiments/fit_threshold.py ---------------------------------------------------------

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "experiments"


def load_fit() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))  # the script imports its sibling modules by name
    spec = importlib.util.spec_from_file_location("fit_threshold", SCRIPTS / "fit_threshold.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_absent_person_with_no_pair_still_counts_in_sc003() -> None:
    folder, errors = eval_set.read_folder(EVAL)
    assert folder is not None, errors
    label_set = folder.set_for(date(2026, 11, 16))
    assert label_set is not None
    absent = label_set.absent()
    only_one = [
        FitPair(
            model="ArcFace",
            label_set=label_set.crawl_date,
            label=absent[0],
            page_url="https://edition.cnn.com/world",
            labelled=False,
            distance=0.70,
        ),
    ] + [  # every present person has its labelled pages, as pairs_for writes them
        FitPair(
            model="ArcFace",
            label_set=label_set.crawl_date,
            label=label,
            page_url=url,
            labelled=True,
            distance=0.30,
        )
        for label, urls in label_set.present().items()
        for url in urls
    ]
    text = load_fit().reading(only_one, 0.50, [label_set])
    assert f"/{len(absent)} absent people" in text


def test_a_set_cannot_be_fitted_and_confirmed(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--set", "2026-11-16", "--confirm", "2026-11-16"]
    assert load_fit().main(args) == 2
    assert "fitted and confirmed" in capsys.readouterr().err
