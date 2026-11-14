"""Exception types that decide how a failure is handled (research R12, R11a).

- ``InfrastructureError``: the database or the face model is unavailable. It must fail the
  Airflow task so Airflow retries it; it is never counted against an item.
- ``ItemError``: one page or image failed. It is logged, counted, and counts as one attempt
  toward the 3-attempt cap.
- ``Disallowed``: a policy outcome (robots.txt, out of scope, plain-http image). Logged and
  skipped, but it is neither a failure nor an attempt.
- ``StageFailed``: every item a task attempted failed. Raised after the task's work is done, so
  the failures are recorded and Airflow marks (and retries) the task as failed.
"""


class InfrastructureError(Exception):
    """A shared dependency (database, face model) is unavailable."""


class IndexNotReady(Exception):
    """The schema, the current model profile row, or indexed faces are missing (search, R5)."""


class ItemError(Exception):
    """One page or image failed; the run continues."""

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(f"{reason} url={url}" if url else reason)
        self.reason = reason
        self.url = url


class FetchRefused(ItemError):
    """A request was refused before it was sent (non-public address, too large, bad scheme)."""


class Disallowed(Exception):
    """A request is not allowed by policy (robots.txt, crawl scope). Not a failure.

    ``sent`` is True when an earlier hop of the same fetch (a redirect) was already sent.
    """

    def __init__(self, reason: str, url: str, *, sent: bool = False) -> None:
        super().__init__(f"{reason} url={url}")
        self.reason = reason
        self.url = url
        self.sent = sent


class StageFailed(Exception):
    """Every item a task attempted failed: the task fails so Airflow retries it."""
