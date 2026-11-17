"""The search experiments: scoring (scripts/experiments/scoring.py) and the sanity check."""

import importlib.util
import math
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Final

import numpy as np
import pytest

SCRIPT: Final[Path] = Path(__file__).resolve().parents[2] / "scripts" / "experiments" / "scoring.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("experiment_scoring", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["experiment_scoring"] = module
    spec.loader.exec_module(module)
    return module


sc: Final[ModuleType] = load()


def unit(*angles_deg: float) -> np.ndarray:
    """Unit vectors in a plane, at the given angles from the first axis."""
    a = np.radians(angles_deg)
    return np.stack([np.cos(a), np.sin(a), np.zeros_like(a)], axis=1)


def test_normalize_scales_rows_and_keeps_zero_rows() -> None:
    v = sc.normalize(np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]))
    assert np.allclose(v[0], [0.6, 0.8, 0.0])
    assert np.allclose(v[1], 0.0)


def test_raw_score_is_cosine() -> None:
    q = unit(0)
    f = unit(0, 60, 90)
    assert np.allclose(sc.scores(q, f, "raw"), [[1.0, 0.5, 0.0]])


def test_tnorm_gives_each_query_mean_0_and_std_1() -> None:
    q = unit(0, 30)
    f = unit(0, 20, 50, 80, 110)
    z = sc.scores(q, f, "tnorm")
    assert np.allclose(z.mean(axis=1), 0.0)
    assert np.allclose(z.std(axis=1), 1.0)
    # Within one query the order is unchanged.
    assert (np.argsort(z, axis=1) == np.argsort(q @ f.T, axis=1)).all()


def test_neighbour_means_skip_renditions_of_the_same_photo() -> None:
    f = unit(0, 0.1, 60, 90)  # faces 0 and 1 are renditions of one photo
    r = sc.neighbour_means(f, f, k=1, skip_same_photo=True)
    assert math.isclose(r[0], 0.5, abs_tol=1e-3)  # nearest real neighbour: 60 degrees
    assert math.isclose(r[3], math.cos(math.radians(30)), abs_tol=1e-9)


def cos(deg: float) -> float:
    return math.cos(math.radians(deg))


def test_csls_is_2cos_minus_both_neighbourhoods() -> None:
    q = unit(10)
    f = unit(0, 0.1, 40, 80, 120)  # faces 0 and 1 are renditions of one photo
    # Each face's 2 nearest other faces, renditions of its own photo (and itself) left out,
    # worked out by hand rather than with neighbour_means.
    r_f = np.array(
        [
            (cos(40) + cos(80)) / 2,  # 0 degrees: skips 0.1, then 40 and 80
            (cos(39.9) + cos(79.9)) / 2,  # 0.1 degrees: skips 0
            (cos(39.9) + cos(40)) / 2,  # 40 degrees: 0.1 and 0 (or 80) away
            (cos(40) + cos(40)) / 2,  # 80 degrees: 40 and 120
            (cos(40) + cos(80)) / 2,  # 120 degrees: 80 and 40
        ]
    )
    assert np.allclose(sc.neighbour_means(f, f, k=2, skip_same_photo=True), r_f)
    # The query's 2 nearest faces count both renditions: 9.9 and 10 degrees away.
    r_q = (cos(9.9) + cos(10)) / 2
    assert math.isclose(sc.neighbour_means(q, f, k=2, skip_same_photo=False)[0], r_q)
    got = sc.scores(q, f, "csls", r_f, k=2)
    expected = [2 * cos(d) - r_q - r for d, r in zip((10, 9.9, 30, 70, 110), r_f, strict=True)]
    assert np.allclose(got[0], expected)


def test_csls_demotes_a_face_close_to_everything() -> None:
    # A hub sits between many faces; the true face is slightly farther from the query by cosine.
    rng = np.random.default_rng(0)
    crowd = sc.normalize(np.array([1.0, 0.0, 0.0]) + 0.35 * rng.standard_normal((40, 3)))
    hub = sc.normalize(np.array([[1.0, 0.05, 0.0]]))
    true = sc.normalize(np.array([[0.2, 1.0, 0.0]]))
    index = np.vstack([crowd, hub, true])
    query = unit(38)  # 35 degrees from the hub, 41 from the true face
    raw = sc.scores(query, index, "raw")[0]
    assert raw[-2] > raw[-1]  # by cosine the hub wins
    csls = sc.scores(query, index, "csls", sc.neighbour_means(index, index, skip_same_photo=True))[
        0
    ]
    assert csls[-1] > csls[-2]  # CSLS prefers the face that is close only to the query


def test_query_expansion_averages_the_top_faces() -> None:
    q = unit(0)
    f = unit(10, 20, 90)
    expected = sc.normalize(q + f[0] + f[1])
    assert np.allclose(sc.expand(q, f, n=2), expected)


def test_pages_take_each_pages_best_face() -> None:
    pages = sc.Pages.build([7, 7, 8, 9], {7: ["https://a", "https://b"], 8: ["https://b"]})
    assert pages.urls == ("https://a", "https://b")
    best = pages.best(np.array([[0.1, 0.4, 0.9, 1.0]]))
    assert best.tolist() == [[0.4, 0.9]]  # face 3's image has no visible page


def test_auc_counts_ties_as_half() -> None:
    assert sc.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert sc.auc([0.5], [0.5]) == 0.5
    assert sc.auc([0.1], [0.9]) == 0.0


def test_metrics_on_a_known_case() -> None:
    urls = ("https://a", "https://b", "https://c")
    labels = ("person-a", "person-b", "absent-a")
    truth = {
        "person-a": frozenset({"https://a"}),
        "person-b": frozenset({"https://b", "https://x"}),
    }
    page_scores = np.array(
        [
            [0.9, 0.2, 0.1],  # person-a: separable
            [0.3, 0.7, -np.inf],  # person-b: https://x has no face, so never found
            [0.4, 0.1, 0.8],  # absent-a: 0.8 beats person-b's 0.7
        ]
    )
    m = sc.metrics(page_scores, urls, labels, truth)
    assert m.separable_people == 1
    assert m.absent_below_all_true == 0
    assert m.recall_at_zero_false == pytest.approx(1 / 3)  # only person-a's 0.9 beats 0.8
    # true 0.9, 0.7, -inf against wrong 0.2, 0.1, 0.3, 0.4, 0.1, 0.8: 6 + 5 + 0 of 18 pairs
    assert m.auc == pytest.approx(11 / 18)


def test_a_labelled_page_with_no_face_is_a_missed_true_page() -> None:
    urls = ("https://a", "https://b")
    truth = {"person-a": frozenset({"https://a", "https://b"})}
    page_scores = np.array([[0.9, -np.inf]])  # https://b is indexed, but no face is scored on it
    rows = list(sc.pairs(page_scores, urls, ["person-a"], truth))
    assert sorted(rows) == [("person-a", True, -np.inf), ("person-a", True, 0.9)]
    m = sc.metrics(page_scores, urls, ["person-a"], truth)
    assert m.recall_at_zero_false == pytest.approx(1 / 2)


# -- the sanity check against the committed logs (scripts/experiments/run.py) -------------------


def load_run() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))  # run.py imports its sibling modules by name
    spec = importlib.util.spec_from_file_location("experiment_run", SCRIPT.parent / "run.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def logged_baseline(run: ModuleType) -> tuple[object, np.ndarray, list[str]]:
    """Page scores rebuilt from the reference run's logged distances (1 - distance)."""
    folder, _ = run.eval_set.read_folder(run.EVAL)
    label_set = folder.set_for(date(2026, 11, 16))
    reference = run._reference(label_set)
    urls = sorted({r.page_url for r in reference})
    column = {u: i for i, u in enumerate(urls)}
    page_scores = np.full((len(label_set.labels()), len(urls)), -np.inf)
    for q, label in enumerate(label_set.labels()):
        for r in reference:
            if r.label == label and r.distance is not None:
                page_scores[q, column[r.page_url]] = 1.0 - r.distance
    return label_set, page_scores, urls


def test_the_sanity_check_accepts_the_logged_run_and_catches_a_change() -> None:
    run = load_run()
    label_set, page_scores, urls = logged_baseline(run)
    assert run._reference(label_set), "the committed logs hold an unfiltered run with distances"
    assert run.sanity_check(label_set, page_scores, urls) == []
    page_scores[0, np.isfinite(page_scores[0]).argmax()] -= 0.001  # one distance off by 0.001
    problems = run.sanity_check(label_set, page_scores, urls)
    assert len(problems) == 1
    assert "against logged" in problems[0]


def test_a_snapshot_survives_save_and_load(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "experiment_snapshot", SCRIPT.parent / "snapshot.py"
    )
    assert spec is not None
    assert spec.loader is not None
    snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snapshot)

    snap = snapshot.Snapshot(
        face_image=np.array([7, 7, 9], dtype=np.int64),
        boxes=np.array([[1, 2, 30, 40], [5, 6, 7, 8], [0, 0, 64, 64]], dtype=np.int64),
        embeddings=np.arange(3 * 512, dtype=np.float32).reshape(3, 512),
        image_pages={7: ("https://a", "https://b"), 9: ("https://b",)},
    )
    target = tmp_path / "x" / "facenet512.npz"
    snap.save(target)
    back = snapshot.Snapshot.load(target)
    assert back.face_image.tolist() == [7, 7, 9]
    assert (back.boxes == snap.boxes).all()
    assert (back.embeddings == snap.embeddings).all()
    assert back.image_pages == snap.image_pages


def load_script(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))  # the scripts import their sibling modules by name
    spec = importlib.util.spec_from_file_location(
        f"experiment_{name}", SCRIPT.parent / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_export_reads_pgvector_vectors_and_page_links() -> None:
    from pgvector import Vector

    export = load_script("export_index")
    a = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    faces = [(7, 1, 2, 30, 40, Vector(a)), (9, 0, 0, 64, 64, Vector(a[::-1].copy()))]
    links = [(7, "https://a"), (7, "https://b"), (9, "https://b")]
    snap = export.to_snapshot(faces, links)
    assert snap.face_image.tolist() == [7, 9]
    assert snap.boxes.tolist() == [[1, 2, 30, 40], [0, 0, 64, 64]]
    assert np.allclose(snap.embeddings[0], a)
    assert np.allclose(snap.embeddings[1], a[::-1])
    assert snap.image_pages == {7: ("https://a", "https://b"), 9: ("https://b",)}


# -- the parallel re-embedding (scripts/experiments/reembed.py) ---------------------------------


class FakeEmbedder:
    """Faces for every JPEG except b"bad", which fails as an undecodable image would."""

    def embed(self, jpeg: bytes) -> list[object]:
        from face_index.errors import ItemError
        from face_index.models import Box, DetectedFace

        if jpeg == b"bad":
            raise ItemError("JPEG could not be decoded")
        n = len(jpeg)
        return [
            DetectedFace(
                box=Box(x=i, y=0, w=10, h=10), confidence=0.99, embedding=(float(n),) * 512
            )
            for i in range(n)
        ]


def test_embedding_in_parallel_matches_one_process_and_records_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    reembed = load_script("reembed")
    monkeypatch.setattr(reembed, "_embedder", lambda model: FakeEmbedder())
    items = [(7, b"ab"), (8, b"bad"), (9, b"abc"), (10, b"a")]
    alone, failed_alone = reembed.embed_batch(items, "ArcFace", None)
    with ThreadPoolExecutor(max_workers=3) as pool:
        together, failed_together = reembed.embed_batch(items, "ArcFace", pool)
    assert together == alone  # same faces, in image order
    assert [image for image, _ in alone] == [7, 7, 9, 9, 9, 10]
    assert failed_alone == failed_together == [8]


def test_failures_survive_a_resume_and_reach_the_snapshot(tmp_path: Path) -> None:
    reembed = load_script("reembed")
    snapshot = load_script("snapshot")
    rows = [(i, f) for i, f in zip([7, 7], FakeEmbedder().embed(b"ab"), strict=True)]
    reembed.save_part(tmp_path, 0, [7, 8], [8], rows)
    reembed.save_part(tmp_path, 1, [9], [], [])
    assert reembed.done_parts(tmp_path) == ({7, 8, 9}, {8})
    assert not list(tmp_path.glob("*.partial"))  # sidecars are moved into place whole

    base = snapshot.Snapshot(
        face_image=np.array([7], dtype=np.int64),
        boxes=np.zeros((1, 4), dtype=np.int64),
        embeddings=np.zeros((1, 512), dtype=np.float32),
        image_pages={7: ("https://a",)},
    )
    target = tmp_path / "arcface.npz"
    reembed._assemble(tmp_path, base, {8}, target)
    back = snapshot.Snapshot.load(target)
    assert back.failed_images == (8,)
    assert back.face_image.tolist() == [7, 7]


def test_a_truncated_sidecar_means_the_part_is_redone(tmp_path: Path) -> None:
    reembed = load_script("reembed")
    reembed.save_part(tmp_path, 0, [7], [], [])
    (tmp_path / "00001.npz").write_bytes(b"")
    (tmp_path / "00001.json").write_text('{"images": [8, 9', encoding="utf-8")  # cut off
    assert reembed.done_parts(tmp_path) == ({7}, set())
    assert not (tmp_path / "00001.json").exists()  # its faces are left out of the snapshot
    assert reembed._next_number(tmp_path) == 2  # the redone part does not overwrite a file


def test_a_second_run_on_the_same_output_is_refused(tmp_path: Path) -> None:
    from contextlib import ExitStack

    reembed = load_script("reembed")
    with ExitStack() as first:
        assert reembed._lock(tmp_path, first)
        with ExitStack() as second:
            assert not reembed._lock(tmp_path, second)
    with ExitStack() as third:
        assert reembed._lock(tmp_path, third)  # free again once the first run ends


def test_the_baseline_is_found_under_any_library_versions() -> None:
    """Faces indexed before a DeepFace or TensorFlow upgrade must still export (LIKE pattern)."""
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location(
        "experiment_export", SCRIPT.parent / "export_index.py"
    )
    assert spec is not None
    assert spec.loader is not None
    export = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export)
    pattern = export.baseline_key()
    assert pattern == "retinaface+Facenet512+cosine+align+base@%"
    for versions in (("0.0.95", "2.20"), ("0.0.104", "2.21")):
        key = export.model_profile.profile_key(*versions, "Facenet512")
        assert key.startswith(pattern.removesuffix("%"))


def test_the_sanity_check_uses_the_reference_runs_own_ceiling() -> None:
    """The reference run is a Facenet512 run logged up to 0.50; a page at 0.60 is not missing."""
    run = load_run()
    label_set, page_scores, urls = logged_baseline(run)
    row, col = np.argwhere(~np.isfinite(page_scores))[0]
    page_scores[row, col] = 1.0 - 0.60  # beyond 0.50, inside today's 0.80 report ceiling
    assert run.sanity_check(label_set, page_scores, urls) == []
