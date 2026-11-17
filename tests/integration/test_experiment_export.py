"""The experiments' index export against loopback Postgres (scripts/experiments/export_index.py)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import numpy as np
import psycopg

from face_index.store import PostgresIndexStore
from tests.integration.test_search_store import ORTHOGONAL, QUERY, Seeder, at_distance

SCRIPTS: Final[Path] = Path(__file__).resolve().parents[2] / "scripts" / "experiments"


def load_export() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))  # export_index imports its sibling module by name
    spec = importlib.util.spec_from_file_location("experiment_export", SCRIPTS / "export_index.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_matches_the_search_visibility_rules(db_dsn: str, store: PostgresIndexStore) -> None:
    seed = Seeder(store)
    a = seed.page("https://cnn.test/a")
    b = seed.page("https://cnn.test/b")
    shared = seed.image(a, [QUERY, ORTHOGONAL], url="https://img.test/shared.jpg")
    seed.image(b, [at_distance(0.2)], size=20)
    seed.image(a, [], index=False)  # pending: not exported
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        # The shared image is also shown on page b, so it belongs to both pages.
        conn.execute("INSERT INTO page_image (page_id, image_id) VALUES (%s, %s)", (b, shared))
        snap = load_export().export(conn)
    assert len(snap.face_image) == 3
    assert snap.embeddings.shape == (3, 512)
    assert np.allclose(snap.embeddings[0], QUERY)
    assert snap.boxes[2].tolist()[2:] == [20, 20]
    assert snap.image_pages[shared] == ("https://cnn.test/a", "https://cnn.test/b")
    assert set(snap.image_pages) == set(snap.face_image.tolist())  # the pending image is left out
