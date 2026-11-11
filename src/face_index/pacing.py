"""Per-host request pacing (FR-003, research R4).

A pacer enforces a minimum interval of ``1 / rate`` between request starts for each
``(host, kind)``. ``PostgresPacer`` shares that budget across every task through one row per
``(host, kind)`` in the ``host_pace`` table.
"""

import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

import psycopg

from face_index.errors import InfrastructureError
from face_index.models import RequestKind

_RESERVE_SQL = """
INSERT INTO host_pace (host, kind, next_slot)
VALUES (%(host)s, %(kind)s, clock_timestamp() + %(interval)s)
ON CONFLICT (host, kind) DO UPDATE
  SET next_slot = greatest(host_pace.next_slot, clock_timestamp()) + %(interval)s
RETURNING next_slot - %(interval)s AS my_slot, clock_timestamp() AS now
"""


def _interval(rates: Mapping[RequestKind, float], kind: RequestKind) -> float:
    rate = rates[kind]
    if rate <= 0:
        raise ValueError(f"rate for {kind} must be positive, got {rate}")
    return 1.0 / rate


class InMemoryPacer:
    """Single-process pacer; used in unit tests with an injected clock."""

    def __init__(
        self,
        rates: Mapping[RequestKind, float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rates = dict(rates)
        self._clock = clock
        self._sleep = sleep
        self._next: dict[tuple[str, RequestKind], float] = {}
        self._lock = threading.Lock()

    def reserve(self, host: str, kind: RequestKind) -> float:
        with self._lock:
            now = self._clock()
            slot = max(self._next.get((host, kind), now), now)
            self._next[(host, kind)] = slot + _interval(self._rates, kind)
            return slot

    def wait(self, host: str, kind: RequestKind) -> None:
        delay = self.reserve(host, kind) - self._clock()
        if delay > 0:
            self._sleep(delay)


class PostgresPacer:
    """Pacer shared by every task through the ``host_pace`` table."""

    def __init__(
        self,
        dsn: str,
        rates: Mapping[RequestKind, float],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._dsn = dsn
        self._rates = dict(rates)
        self._sleep = sleep
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def reserve(self, host: str, kind: RequestKind) -> tuple[datetime, datetime]:
        """Reserve the next slot; returns ``(slot, database_now)`` without sleeping."""
        interval = timedelta(seconds=_interval(self._rates, kind))
        try:
            row = (
                self._connection()
                .execute(_RESERVE_SQL, {"host": host, "kind": kind, "interval": interval})
                .fetchone()
            )
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise InfrastructureError(f"pacer database unavailable: {type(exc).__name__}") from exc
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise InfrastructureError("pacer reservation returned no row")
        slot, now = row
        return slot, now

    def wait(self, host: str, kind: RequestKind) -> None:
        slot, now = self.reserve(host, kind)
        delay = (slot - now).total_seconds()
        if delay > 0:
            self._sleep(delay)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
