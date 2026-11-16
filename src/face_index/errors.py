"""Exception types that decide how a failure is handled (research R12, R11a).

- ``InfrastructureError``: the database or the face model is unavailable. It must fail the
  Airflow task so Airflow retries it; it is never counted against an item.
- ``ItemError``: one page or image failed. It is logged, counted, and counts as one attempt
  toward the 3-attempt cap.
- ``Disallowed``: a policy outcome (robots.txt, out of scope, plain-http image). Logged and
  skipped, but it is neither a failure nor an attempt.
- ``Gone``: a ``Disallowed`` for a page or image the server says does not exist (HTTP 404 or
  410). A dead link on the site is not the crawler's failure, so it is skipped the same way.
- ``StageFailed``: a task failed as a whole. Raised after the task's work is done, so the
  failures are recorded and Airflow marks (and retries) the task as failed.
- ``ItemsFailed``: a ``StageFailed`` for a crawl or download task in which at least one page or
  image failed. It carries the task's result, so the DAG can publish it before the task fails
  and the rest of the run still works with what succeeded. The DAG fails such a task without a
  retry: the next run tries the failed items again.
"""

from collections.abc import Mapping


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


class Gone(Disallowed):
    """The server answered 404 or 410: a dead link. Not a failure, and not a plain skip either.

    It is logged, counted, and recorded so later runs do not request it again (FR-023b). The
    request was sent, so ``sent`` is always True and it counts toward the page cap.
    """

    def __init__(self, url: str, status: int) -> None:
        super().__init__("not_found", url, sent=True)
        self.status = status


class StageFailed(Exception):
    """A task failed as a whole: Airflow marks it failed and retries it."""


class ItemsFailed(StageFailed):
    """At least one page or image of a crawl or download task failed (FR-023).

    ``result`` is what the task would have returned: every item was still attempted.
    """

    def __init__(self, message: str, result: Mapping[str, object]) -> None:
        super().__init__(message)
        self.result = result
