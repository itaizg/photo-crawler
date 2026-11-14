"""Exception types that decide how a failure is handled (research R12, R11a).

- ``InfrastructureError``: the database or the face model is unavailable. It must fail the
  Airflow task so Airflow retries it; it is never counted against an item.
- ``ItemError``: one page or image failed. It is logged, counted, and counts as one attempt
  toward the 3-attempt cap.
- ``Disallowed``: a policy outcome (robots.txt, out of scope). Logged and skipped, but it is
  neither a failure nor an attempt.
"""


class InfrastructureError(Exception):
    """A shared dependency (database, face model) is unavailable."""


class ItemError(Exception):
    """One page or image failed; the run continues."""

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(f"{reason} url={url}" if url else reason)
        self.reason = reason
        self.url = url


class FetchRefused(ItemError):
    """A request was refused before it was sent (non-public address, too large, bad scheme)."""


class Disallowed(Exception):
    """A request is not allowed by policy (robots.txt, crawl scope). Not a failure."""

    def __init__(self, reason: str, url: str) -> None:
        super().__init__(f"{reason} url={url}")
        self.reason = reason
        self.url = url
