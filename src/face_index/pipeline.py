"""Pipeline stages called by the DAG's thin task wrappers (research R3, R11, R11a, R12).

This module depends only on ports and plain data. Production adapters are built in exactly one
place, ``build_default_deps``; tests pass fakes or a Postgres store on a test database.

Failure handling per item: ``InfrastructureError`` propagates (Airflow retries the task);
``Disallowed`` is a logged policy skip; any other exception is logged with its URL, counted, and
recorded as one attempt toward the 3-attempt cap.
"""

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from face_index import config
from face_index.config import Site
from face_index.crawl import CrawlResult, crawl_site
from face_index.errors import Disallowed, InfrastructureError, ItemError
from face_index.models import CrawledPage, Skipped, Summary
from face_index.ports import FaceEmbedder, Fetcher, ImageNormalizer, IndexStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunParams:
    depth: int = config.DEFAULT_DEPTH
    page_cap: int | None = config.DEFAULT_PAGE_CAP
    page_rate: float = config.DEFAULT_PAGE_RATE
    image_rate: float = config.DEFAULT_IMAGE_RATE

    @classmethod
    def from_mapping(cls, params: Mapping[str, object]) -> "RunParams":
        def number(name: str, default: float) -> float:
            value = params.get(name, default)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{name} must be a number")
            return float(value)

        depth = int(number("depth", config.DEFAULT_DEPTH))
        page_cap = (
            None
            if params.get("page_cap", config.DEFAULT_PAGE_CAP) is None
            else int(number("page_cap", config.DEFAULT_PAGE_CAP or 0))
        )
        result = cls(
            depth=depth,
            page_cap=page_cap,
            page_rate=number("page_rate", config.DEFAULT_PAGE_RATE),
            image_rate=number("image_rate", config.DEFAULT_IMAGE_RATE),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not 0 <= self.depth <= config.MAX_DEPTH:
            raise ValueError(f"depth must be between 0 and {config.MAX_DEPTH}")
        if self.page_cap is not None and self.page_cap < 1:
            raise ValueError("page_cap must be at least 1 or None")
        if not config.MIN_RATE <= self.page_rate <= config.MAX_PAGE_RATE:
            raise ValueError(f"page_rate must be in [{config.MIN_RATE}, {config.MAX_PAGE_RATE}]")
        if not config.MIN_RATE <= self.image_rate <= config.MAX_IMAGE_RATE:
            raise ValueError(f"image_rate must be in [{config.MIN_RATE}, {config.MAX_IMAGE_RATE}]")


@dataclass(frozen=True, slots=True)
class Deps:
    store: IndexStore
    fetcher: Fetcher
    normalizer: ImageNormalizer
    embedder: FaceEmbedder


def build_default_deps(dsn: str, params: RunParams, site: Site | None = None) -> Deps:
    """The only place production adapters are constructed."""
    from face_index.faces import DeepFaceEmbedder
    from face_index.http import HttpFetcher
    from face_index.normalize import PillowNormalizer
    from face_index.pacing import PostgresPacer
    from face_index.store import PostgresIndexStore
    from face_index.urls import in_scope

    pacer = PostgresPacer(dsn, {"page": params.page_rate, "image": params.image_rate})
    scope = (lambda url: in_scope(url, site)) if site is not None else None
    return Deps(
        store=PostgresIndexStore(dsn),
        fetcher=HttpFetcher(pacer=pacer, scope=scope),
        normalizer=PillowNormalizer(),
        embedder=DeepFaceEmbedder(),
    )


# -- stages -------------------------------------------------------------------------------------


def init_schema(deps: Deps) -> int:
    profile_id = deps.store.init_schema()
    log.info("schema ready model_profile_id=%d", profile_id)
    return profile_id


def crawl(site: Site, params: RunParams, deps: Deps, run_id: str) -> dict[str, object]:
    store = deps.store
    result = crawl_site(
        site,
        deps.fetcher,
        params.depth,
        params.page_cap,
        is_abandoned=lambda url: store.is_abandoned(url, "page", config.MAX_ATTEMPTS),
        on_failure=lambda url, reason: store.record_failure(url, "page", reason, run_id),
        on_success=lambda url: store.record_success(url, "page"),
    )
    payload = result.to_json()
    size = len(json.dumps(payload))
    log.info("crawl result site=%s pages=%d xcom_bytes=%d", site.name, len(result.pages), size)
    if size > config.MAX_XCOM_BYTES:
        raise ValueError(
            f"crawl result is {size} bytes (limit {config.MAX_XCOM_BYTES}); "
            "lower the depth or set a page_cap"
        )
    return payload


def download(
    site: Site, crawl_payload: Mapping[str, object], deps: Deps, run_id: str
) -> dict[str, int]:
    result = CrawlResult.from_json(dict(crawl_payload))
    summary = Summary()
    summary.merge(result.summary)
    failed: set[str] = set()  # image URLs that already failed in this task: not asked again
    for page in result.pages:
        try:
            page_id = deps.store.upsert_page(page.url, site.name, page.depth)
            # A new lead replaces the old one atomically when it is linked (store._link), so the
            # old lead is cleared up front only when the page no longer declares one.
            if page.lead_ref is None:
                deps.store.reset_lead(page_id)
        except InfrastructureError:
            raise
        except Exception as exc:  # this page cannot be recorded; keep the run going
            log.warning("page failed url=%s stage=download reason=%s", page.url, exc)
            summary.add("failures.download")
            continue
        lead_linked = False
        for ref in page.image_refs:
            linked = _index_ref(ref, page, page_id, deps, run_id, summary, failed)
            lead_linked |= linked and ref == page.lead_ref
        if page.lead_ref is not None and not lead_linked:
            try:  # the declared lead could not be stored this time: do not keep a stale lead
                deps.store.reset_lead(page_id)
            except InfrastructureError:
                raise
            except Exception as exc:
                log.warning("page failed url=%s stage=lead reason=%s", page.url, exc)
        _report_outdated(page.url, page_id, deps, summary)
    return summary.to_json()


def _report_outdated(url: str, page_id: int, deps: Deps, summary: Summary) -> None:
    """Log the page's links this crawl did not see; search ignores them (FR-018, R4)."""
    try:
        outdated = deps.store.outdated_links(page_id)
    except InfrastructureError:
        raise
    except Exception as exc:
        log.warning("page failed url=%s stage=outdated_links reason=%s", url, exc)
        return
    if outdated:
        log.info("outdated links page=%s count=%d", url, outdated)
        summary.add("links.outdated", outdated)


def _index_ref(
    ref: str,
    page: CrawledPage,
    page_id: int,
    deps: Deps,
    run_id: str,
    summary: Summary,
    failed: set[str],
) -> bool:
    """Index one image reference; returns True when the page was linked to an image."""
    store = deps.store
    is_lead = ref == page.lead_ref
    if ref in failed:
        summary.add("skipped.failed_earlier_in_run")
        return False
    try:
        known = store.find_image_by_url(ref)
        if known is not None:
            store.link(page_id, known, is_lead)
            summary.add("reused_by_url")
            return True
        if store.is_abandoned(ref, "image", config.MAX_ATTEMPTS):
            log.info("link abandoned url=%s kind=image attempts=%d", ref, config.MAX_ATTEMPTS)
            summary.add("abandoned.image")
            return False
        response = deps.fetcher.get_image(ref)
        summary.add("downloaded")
        sha256 = hashlib.sha256(response.body).digest()
        same_content = store.find_image_by_sha256(sha256)
        if same_content is not None:
            store.add_url_and_link(same_content, ref, page_id, is_lead)
            store.record_success(ref, "image")
            summary.add("reused_by_fingerprint")
            return True
        normalized = deps.normalizer.normalize(response.body, response.content_type)
        _, created = store.stage_image(normalized, ref, page_id, is_lead)
        store.record_success(ref, "image")
        if isinstance(normalized, Skipped):
            log.info("image skipped url=%s reason=%s", ref, normalized.reason)
            summary.add(f"skipped.{normalized.reason}")
        elif created:
            summary.add("staged")
        else:
            summary.add("reused_by_fingerprint")
        return True
    except InfrastructureError:
        raise
    except Disallowed as exc:
        log.info("image skipped url=%s reason=%s", ref, exc.reason)
        summary.add(f"skipped.{exc.reason}")
    except ItemError as exc:
        failed.add(ref)
        _image_failed(ref, exc.reason, deps, run_id, summary)
    except Exception as exc:  # unexpected per-item error: count it, keep the run going
        failed.add(ref)
        _image_failed(ref, f"unexpected {type(exc).__name__}: {exc}", deps, run_id, summary)
    return False


def _image_failed(ref: str, reason: str, deps: Deps, run_id: str, summary: Summary) -> None:
    log.warning("image failed url=%s stage=download reason=%s", ref, reason)
    summary.add("failures.download")
    deps.store.record_failure(ref, "image", reason, run_id)


def plan_batches(deps: Deps) -> list[list[int]]:
    batches = deps.store.pending_batches(config.DETECT_BATCH_SIZE, config.MAX_ATTEMPTS)
    if len(batches) > config.MAX_DETECT_BATCHES:
        later = sum(len(b) for b in batches[config.MAX_DETECT_BATCHES :])
        log.info("detection deferred images=%d (left for the next run)", later)
        batches = batches[: config.MAX_DETECT_BATCHES]
    abandoned = deps.store.abandoned_pending(config.MAX_ATTEMPTS)
    if abandoned:
        log.info("detection abandoned images=%d attempts=%d", abandoned, config.MAX_ATTEMPTS)
    log.info("planned batches=%d images=%d", len(batches), sum(len(b) for b in batches))
    return batches


def detect(ids: Sequence[int], profile_id: int, deps: Deps, run_id: str) -> dict[str, int]:
    summary = Summary()
    for image_id in ids:
        try:
            faces = deps.store.process_pending(
                image_id, lambda pending: deps.embedder.embed(pending.jpeg), profile_id
            )
        except InfrastructureError:
            raise
        except Exception as exc:
            reason = exc.reason if isinstance(exc, ItemError) else f"unexpected {exc!r}"
            log.warning("image failed id=%d stage=detect reason=%s", image_id, reason)
            summary.add("failures.detect")
            deps.store.record_detect_failure(image_id, run_id)
            continue
        if faces is None:
            # Already stored, by an earlier try of this task whose result Airflow lost: count
            # what it stored so the run summary stays complete.
            faces = deps.store.stored_faces(image_id)
            if faces is None:  # locked by another worker, which counts it
                summary.add("detect.already_done")
                continue
            summary.add("detect.recovered")
        if faces == 0:
            summary.add("images_no_faces")
        else:
            summary.add("images_indexed")
            summary.add("faces_stored", faces)
    return summary.to_json()


SUMMARY_ROWS = (
    ("pages crawled", "pages_crawled."),
    ("images found", "images_found"),
    ("downloaded", "downloaded"),
    ("reused by URL", "reused_by_url"),
    ("reused by fingerprint", "reused_by_fingerprint"),
    ("staged for detection", "staged"),
    ("skipped", "skipped."),
    ("newly indexed (with faces)", "images_indexed"),
    ("analyzed, no faces", "images_no_faces"),
    ("faces stored", "faces_stored"),
    ("failures", "failures."),
    ("abandoned (3 attempts)", "abandoned."),
    ("outdated page links (ignored by search)", "links.outdated"),
)


def summarize(summaries: Sequence[Mapping[str, int]], deps: Deps | None = None) -> dict[str, int]:
    total = Summary()
    for part in summaries:
        total.merge(Summary.from_json(dict(part)))
    if deps is not None:
        abandoned = deps.store.abandoned_pending(config.MAX_ATTEMPTS)
        if abandoned:
            total.add("abandoned.detect", abandoned)
    counts = total.to_json()
    lines = ["run summary", f"{'metric':<34}{'count':>8}"]
    for label, key in SUMMARY_ROWS:
        if key.endswith("."):
            matched = {k: v for k, v in counts.items() if k.startswith(key)}
            lines.append(f"{label:<34}{sum(matched.values()):>8}")
            lines.extend(f"  {k[len(key) :]:<32}{v:>8}" for k, v in matched.items())
        else:
            lines.append(f"{label:<34}{counts.get(key, 0):>8}")
    log.info("\n".join(lines))
    return counts
