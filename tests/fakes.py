"""Test doubles for the ports, shared by unit, integration and smoke tests."""

import hashlib
from collections.abc import Callable, Mapping, Sequence

import httpx

from face_index.errors import ItemError
from face_index.models import Box, DetectedFace, PageMatch, Record, RequestKind

PUBLIC_IP = "93.184.216.34"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResolver:
    """Maps host names to IPs; unknown hosts resolve to a public test address."""

    def __init__(self, overrides: Mapping[str, list[str]] | None = None) -> None:
        self._overrides = dict(overrides or {})
        self.calls: list[str] = []

    def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        return self._overrides.get(host, [PUBLIC_IP])


class RecordingPacer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RequestKind]] = []

    def wait(self, host: str, kind: RequestKind) -> None:
        self.calls.append((host, kind))


class Reply(Record):
    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] = {}
    error: type[httpx.TransportError] | None = None  # e.g. httpx.ReadTimeout


class MockSite:
    """An ``httpx.MockTransport`` serving a dict of ``url -> Reply``.

    Requests arrive pinned to an IP, so the original URL is rebuilt from the ``Host`` header.
    Every request is logged in ``requests`` as ``(original_url, request)``.
    """

    def __init__(self, routes: Mapping[str, Reply] | None = None) -> None:
        self.routes: dict[str, Reply] = dict(routes or {})
        self.requests: list[tuple[str, httpx.Request]] = []

    def original_url(self, request: httpx.Request) -> str:
        host = request.headers.get("host", request.url.host)
        url = request.url.copy_with(host=host.split(":")[0])
        return str(url)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = self.original_url(request)
        self.requests.append((url, request))
        reply = self.routes.get(url)
        if reply is None:
            return httpx.Response(404, request=request)
        if reply.error is not None:
            raise reply.error("simulated", request=request)
        return httpx.Response(
            reply.status, content=reply.body, headers=reply.headers, request=request
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def requested(self, url: str) -> bool:
        return any(u == url for u, _ in self.requests)


def html_reply(body: str) -> Reply:
    return Reply(
        status=200, body=body.encode(), headers={"content-type": "text/html; charset=utf-8"}
    )


def robots_reply(text: str = "User-agent: *\nAllow: /\n") -> Reply:
    return Reply(status=200, body=text.encode(), headers={"content-type": "text/plain"})


class FakeEmbedder:
    """Deterministic faces: the number of faces is looked up by the JPEG's sha256 prefix.

    Unknown images get ``default_faces`` faces. ``fail_on`` holds sha256 hex prefixes that raise.
    """

    def __init__(
        self,
        faces_by_prefix: Mapping[str, int] | None = None,
        default_faces: int = 0,
        fail_on: set[str] | None = None,
        lookup: Callable[[bytes], str] | None = None,
    ) -> None:
        self._faces = dict(faces_by_prefix or {})
        self._default = default_faces
        self._fail_on = fail_on or set()
        self._lookup = lookup or (lambda jpeg: hashlib.sha256(jpeg).hexdigest())
        self.calls = 0

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        self.calls += 1
        key = self._lookup(jpeg)
        if any(key.startswith(prefix) for prefix in self._fail_on):
            raise ItemError("simulated detection failure")
        count = next((n for p, n in self._faces.items() if key.startswith(p)), self._default)
        seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        return [
            DetectedFace(
                box=Box(x=4 + 20 * i, y=4, w=16, h=16),
                confidence=0.99,
                embedding=tuple(float(((seed >> (j % 24)) + i + j) % 7 + 1) for j in range(512)),
            )
            for i in range(count)
        ]


class FakeSearchIndex:
    """In-memory ``SearchIndex``: canned pages, filtered by the threshold like the real query.

    ``pages`` holds the candidate pages for every face; ``pages_by_face`` overrides that with a
    function of ``(embedding, max_distance)``. Every ``matching_pages`` call is recorded.
    """

    def __init__(
        self,
        profiles: Mapping[str, int] | None = None,
        face_count: int = 10,
        pages: Sequence[PageMatch] = (),
        pages_by_face: Callable[[Sequence[float], float], list[PageMatch]] | None = None,
        raise_on_connect: Exception | None = None,
    ) -> None:
        self._profiles = dict(profiles) if profiles is not None else None
        self._face_count = face_count
        self._pages = list(pages)
        self._pages_by_face = pages_by_face
        self._raise = raise_on_connect
        self.calls: list[tuple[tuple[float, ...], float, int]] = []
        self.closed = False

    def profile_id(self, key: str) -> int | None:
        if self._raise is not None:
            raise self._raise
        if self._profiles is None:
            return 1  # any key is known
        return self._profiles.get(key)

    def indexed_face_count(self, profile_id: int) -> int:
        return self._face_count

    def matching_pages(
        self, embedding: Sequence[float], max_distance: float, profile_id: int
    ) -> list[PageMatch]:
        self.calls.append((tuple(embedding), max_distance, profile_id))
        if self._pages_by_face is not None:
            return self._pages_by_face(embedding, max_distance)
        return [p for p in self._pages if p.best_distance < max_distance]

    def close(self) -> None:
        self.closed = True
