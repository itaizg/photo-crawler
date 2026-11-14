"""Breadth-first crawl of one site (FR-001, FR-002, FR-005a, research R5).

Only ports are used: a ``Fetcher`` for HTTP and, optionally, an ``IndexStore`` for the
3-attempt cap. Policy outcomes (robots.txt, scope) are skipped; failures are counted.
"""

import logging
from collections import Counter, deque
from collections.abc import Callable, Mapping
from typing import Annotated

from pydantic import BeforeValidator

from face_index import config
from face_index.config import Site
from face_index.errors import Disallowed, InfrastructureError, ItemError
from face_index.extract import extract_page
from face_index.models import CrawledPage, Record, Summary
from face_index.ports import Fetcher
from face_index.urls import in_scope, normalize_image_url, normalize_page_url

log = logging.getLogger(__name__)

# One crawled page in the XCom payload: [url, depth, lead_ref, image_refs]. A list, not an
# object, keeps large crawls under the XCom size limit.
type PageRow = tuple[str, int, str | None, tuple[str, ...]]


class _Payload(Record):
    """The XCom shape of a ``CrawlResult``, validated once when the download task reads it."""

    site: str
    pages: tuple[PageRow, ...] = ()
    summary: Annotated[Summary, BeforeValidator(Summary.of)] = Summary()


class CrawlResult(Record):
    site: str
    pages: tuple[CrawledPage, ...] = ()
    summary: Summary = Summary()

    def to_json(self) -> dict[str, object]:
        return {
            "site": self.site,
            "pages": [[p.url, p.depth, p.lead_ref, list(p.image_refs)] for p in self.pages],
            "summary": self.summary.to_json(),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "CrawlResult":
        payload = _Payload.model_validate(data)
        pages = tuple(
            CrawledPage(url=url, depth=depth, lead_ref=lead, image_refs=refs)
            for url, depth, lead, refs in payload.pages
        )
        return cls(site=payload.site, pages=pages, summary=payload.summary)


AttemptGate = Callable[[str], bool]  # url -> True if the link is abandoned
FailureHook = Callable[[str, str], None]  # (url, reason) -> record a failed attempt
SuccessHook = Callable[[str], None]


def crawl_site(
    site: Site,
    fetcher: Fetcher,
    depth: int,
    page_cap: int | None,
    *,
    is_abandoned: AttemptGate | None = None,
    on_failure: FailureHook | None = None,
    on_success: SuccessHook | None = None,
) -> CrawlResult:
    pages: list[CrawledPage] = []
    counts: Counter[str] = Counter()
    seed = normalize_page_url(site.seed_url)
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    seen = {seed}  # queued or crawled, by requested URL
    crawled: set[str] = set()  # stored page identities (final URL after redirects)
    requests = 0  # every page request sent counts toward the cap: failed and non-HTML ones too

    while queue:
        if page_cap is not None and requests >= page_cap:
            counts[f"pages_left_by_cap.{site.name}"] += len(queue)
            break
        url, level = queue.popleft()
        if url in crawled:  # reached earlier as the target of a redirect
            continue
        if is_abandoned is not None and is_abandoned(url):
            log.info("link abandoned url=%s kind=page attempts=%d", url, config.MAX_ATTEMPTS)
            counts["abandoned.page"] += 1
            continue
        try:
            response = fetcher.get_page(url)
        except InfrastructureError:
            raise
        except Disallowed as exc:
            log.info("page skipped url=%s reason=%s", url, exc.reason)
            counts[f"skipped.page.{exc.reason}"] += 1
            continue
        except ItemError as exc:
            requests += 1
            log.warning("page failed url=%s stage=crawl reason=%s", url, exc.reason)
            counts["failures.crawl"] += 1
            if on_failure is not None:
                on_failure(url, exc.reason)
            continue
        except Exception as exc:  # noqa: BLE001 - per-item isolation point: count, keep crawling
            requests += 1
            log.warning("page failed url=%s stage=crawl reason=%s", url, type(exc).__name__)
            counts["failures.crawl"] += 1
            if on_failure is not None:
                on_failure(url, f"unexpected {type(exc).__name__}")
            continue

        requests += 1
        if on_success is not None:
            on_success(url)
        if response.text is None:
            log.info("page skipped url=%s reason=not_html", url)
            counts["skipped.page.not_html"] += 1
            continue

        # A redirected page is stored under its final URL, once.
        final = _normalized(normalize_page_url, response.url) or url
        if final in crawled:
            log.info("page skipped url=%s reason=duplicate_of %s", url, final)
            counts["skipped.page.duplicate"] += 1
            continue
        crawled.update({url, final})
        seen.add(final)

        extract = extract_page(response.text, response.url)
        refs = tuple(
            dict.fromkeys(
                ref
                for ref in (_normalized(normalize_image_url, r) for r in extract.image_refs)
                if ref is not None
            )
        )
        lead = _normalized(normalize_image_url, extract.lead_ref) if extract.lead_ref else None
        pages.append(CrawledPage(url=final, depth=level, image_refs=refs, lead_ref=lead))
        counts[f"pages_crawled.{site.name}"] += 1
        counts["images_found"] += len(refs)

        if level < depth:
            for link in extract.links:
                candidate = _normalized(normalize_page_url, link)
                if candidate is not None and candidate not in seen and in_scope(candidate, site):
                    seen.add(candidate)
                    queue.append((candidate, level + 1))
    return CrawlResult(site=site.name, pages=tuple(pages), summary=Summary.of(counts))


def _normalized(normalize: Callable[[str], str], url: str) -> str | None:
    """Normalize one extracted URL; a malformed one (bad port, bad authority) is skipped."""
    try:
        return normalize(url)
    except ValueError as exc:
        log.info("url skipped url=%r reason=malformed (%s)", url, exc)
        return None
