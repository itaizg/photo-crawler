from face_index.models import RequestKind
from face_index.pacing import InMemoryPacer
from tests.fakes import FakeClock


def _starts(
    pacer: InMemoryPacer, clock: FakeClock, host: str, kind: RequestKind, n: int
) -> list[float]:
    starts = []
    for _ in range(n):
        pacer.wait(host, kind)
        starts.append(clock.now)
    return starts


def _max_in_any_window(starts: list[float], window: float = 1.0) -> int:
    return max(sum(1 for t in starts if s <= t < s + window) for s in starts)


def test_never_more_than_rate_per_second() -> None:
    clock = FakeClock()
    pacer = InMemoryPacer({"page": 1.0, "image": 5.0}, clock=clock, sleep=clock.sleep)
    assert _max_in_any_window(_starts(pacer, clock, "a.com", "image", 30)) <= 5
    assert _max_in_any_window(_starts(pacer, clock, "a.com", "page", 10)) <= 1


def test_page_and_image_budgets_are_independent() -> None:
    clock = FakeClock()
    pacer = InMemoryPacer({"page": 1.0, "image": 5.0}, clock=clock, sleep=clock.sleep)
    pacer.wait("people.com", "page")
    pacer.wait("people.com", "image")
    assert clock.sleeps == []  # the image request did not wait for the page budget


def test_different_hosts_do_not_block_each_other() -> None:
    clock = FakeClock()
    pacer = InMemoryPacer({"page": 1.0, "image": 1.0}, clock=clock, sleep=clock.sleep)
    pacer.wait("a.com", "page")
    pacer.wait("b.com", "page")
    assert clock.sleeps == []
    pacer.wait("a.com", "page")
    assert clock.sleeps == [1.0]
