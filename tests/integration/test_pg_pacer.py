"""PostgresPacer shares one budget per (host, kind) across callers (FR-003, research R4).

The test asserts on reserved slot timestamps, never on elapsed time (constitution IV).
"""

import itertools
import threading
from datetime import datetime, timedelta

from face_index.pacing import PostgresPacer
from face_index.store import PostgresIndexStore


def test_concurrent_callers_get_distinct_evenly_spaced_slots(
    store: PostgresIndexStore, db_dsn: str
) -> None:
    slots: list[datetime] = []
    lock = threading.Lock()

    def reserve_ten() -> None:
        pacer = PostgresPacer(db_dsn, {"page": 1.0, "image": 5.0})
        for _ in range(10):
            slot, _now = pacer.reserve("media.example", "image")
            with lock:
                slots.append(slot)
        pacer.close()

    threads = [threading.Thread(target=reserve_ten) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ordered = sorted(slots)
    assert len(set(ordered)) == 20
    gaps = {b - a for a, b in itertools.pairwise(ordered)}
    # Each reservation starts no earlier than the previous slot + 0.2 s.
    assert min(gaps) >= timedelta(seconds=0.2)


def test_wait_sleeps_for_the_computed_delay_only(store: PostgresIndexStore, db_dsn: str) -> None:
    sleeps: list[float] = []
    pacer = PostgresPacer(db_dsn, {"page": 2.0, "image": 5.0}, sleep=sleeps.append)
    pacer.wait("a.example", "page")  # first slot is now: no sleep
    pacer.wait("a.example", "page")  # second slot is 0.5 s later
    pacer.close()
    assert len(sleeps) == 1
    assert 0.4 < sleeps[0] <= 0.5


def test_kinds_and_hosts_have_separate_budgets(store: PostgresIndexStore, db_dsn: str) -> None:
    sleeps: list[float] = []
    pacer = PostgresPacer(db_dsn, {"page": 1.0, "image": 1.0}, sleep=sleeps.append)
    pacer.wait("a.example", "page")
    pacer.wait("a.example", "image")
    pacer.wait("b.example", "page")
    pacer.close()
    assert sleeps == []
