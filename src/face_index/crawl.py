"""Breadth-first crawl of one site (FR-001, FR-002, FR-005a, research R5).

Only ports are used: a ``Fetcher`` for HTTP and, optionally, an ``IndexStore`` for the
3-attempt cap. Policy outcomes (robots.txt, scope) are skipped; failures are counted.
"""

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from face_index import config
from face_index.config import Site
from face_index.errors import Disallowed, InfrastructureError, ItemError
from face_index.extract import extract_page
from face_index.models import CrawledPage, Summary
from face_index.ports import Fetcher
from face_index.urls import in_scope, normalize_image_url, normalize_page_url

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CrawlResult:
    site: str
    pages: list[CrawledPage] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)

    def to_json(self) -> dict[str, object]:
        return {
            "site": self.site,
            "pages": [[p.url, p.depth, p.lead_ref, list(p.image_refs)] for p in self.pages],
            "summary": self.summary.to_json(),
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "CrawlResult":
        pages_raw = data.get("pages", [])
        summary_raw = data.get("summary", {})
        assert isinstance(pages_raw, list)
        assert isinstance(summary_raw, dict)
        pages = [
            CrawledPage(url=str(u), depth=int(d), lead_ref=lead, image_refs=tuple(refs))
            for u, d, lead, refs in pages_raw
        ]
        return cls(site=str(data["site"]), pages=pages, summary=Summary.from_json(summary_raw))


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
    result = CrawlResult(site=site.name)
    summary = result.summary
    seed = normalize_page_url(site.seed_url)
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    seen = {seed}  # queued or crawled, by requested URL
    crawled: set[str] = set()  # stored page identities (final URL after redirects)
    requests = 0  # every page request sent counts toward the cap: failed and non-HTML ones too

    while queue:
        if page_cap is not None and requests >= page_cap:
            summary.add(f"pages_left_by_cap.{site.name}", len(queue))
            break
        url, level = queue.popleft()
        if url in crawled:  # reached earlier as the target of a redirect
            continue
        if is_abandoned is not None and is_abandoned(url):
            log.info("link abandoned url=%s kind=page attempts=%d", url, config.MAX_ATTEMPTS)
            summary.add("abandoned.page")
            continue
        try:
            response = fetcher.get_page(url)
        except InfrastructureError:
            raise
        except Disallowed as exc:
            log.info("page skipped url=%s reason=%s", url, exc.reason)
            summary.add(f"skipped.page.{exc.reason}")
            continue
        except ItemError as exc:
            requests += 1
            log.warning("page failed url=%s stage=crawl reason=%s", url, exc.reason)
            summary.add("failures.crawl")
            if on_failure is not None:
                on_failure(url, exc.reason)
            continue
        except Exception as exc:  # unexpected per-item error: count it, keep crawling
            requests += 1
            log.warning("page failed url=%s stage=crawl reason=%s", url, type(exc).__name__)
            summary.add("failures.crawl")
            if on_failure is not None:
                on_failure(url, f"unexpected {type(exc).__name__}")
            continue

        requests += 1
        if on_success is not None:
            on_success(url)
        if response.text is None:
            log.info("page skipped url=%s reason=not_html", url)
            summary.add("skipped.page.not_html")
            continue

        # A redirected page is stored under its final URL, once.
        final = _normalized(normalize_page_url, response.url) or url
        if final in crawled:
            log.info("page skipped url=%s reason=duplicate_of %s", url, final)
            summary.add("skipped.page.duplicate")
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
        result.pages.append(CrawledPage(url=final, depth=level, image_refs=refs, lead_ref=lead))
        summary.add(f"pages_crawled.{site.name}")
        summary.add("images_found", len(refs))

        if level < depth:
            for link in extract.links:
                candidate = _normalized(normalize_page_url, link)
                if candidate is not None and candidate not in seen and in_scope(candidate, site):
                    seen.add(candidate)
                    queue.append((candidate, level + 1))
    return result


def _normalized(normalize: Callable[[str], str], url: str) -> str | None:
    """Normalize one extracted URL; a malformed one (bad port, bad authority) is skipped."""
    try:
        return normalize(url)
    except ValueError as exc:
        log.info("url skipped url=%r reason=malformed (%s)", url, exc)
        return None
