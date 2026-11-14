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
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import ConfigDict, Field, SkipValidation

from face_index import config
from face_index.config import Site
from face_index.crawl import CrawlResult, crawl_site
from face_index.errors import Disallowed, InfrastructureError, ItemError
from face_index.models import CrawledPage, Record, Skipped, Summary
from face_index.ports import FaceEmbedder, Fetcher, ImageNormalizer, IndexStore

log = logging.getLogger(__name__)


class RunParams(Record):
    """One run's settings: the config defaults, overridden by DAG params (constitution IX).

    Strict, so a string or a boolean is rejected instead of being coerced. Built from the DAG's
    params with ``RunParams.model_validate``; a bad value raises ``ValidationError``, which is
    a ``ValueError`` that names the field.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    depth: Annotated[int, Field(ge=0, le=config.MAX_DEPTH)] = config.DEFAULT_DEPTH
    page_cap: Annotated[int, Field(ge=1)] | None = config.DEFAULT_PAGE_CAP
    page_rate: Annotated[float, Field(ge=config.MIN_RATE, le=config.MAX_PAGE_RATE)] = (
        config.DEFAULT_PAGE_RATE
    )
    image_rate: Annotated[float, Field(ge=config.MIN_RATE, le=config.MAX_IMAGE_RATE)] = (
        config.DEFAULT_IMAGE_RATE
    )


class Deps(Record):
    """The adapters a stage uses, typed by their ports. ty checks that they conform."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    store: SkipValidation[IndexStore]
    fetcher: SkipValidation[Fetcher]
    normalizer: SkipValidation[ImageNormalizer]
    embedder: SkipValidation[FaceEmbedder]


class _RefOutcome(Record):
    """What indexing one image reference did: the counters to add, and whether it linked."""

    counts: tuple[str, ...]
    linked: bool = False
    failed: bool = False  # this URL failed; it is not requested again in the same task


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
    result = CrawlResult.from_json(crawl_payload)
    counts: Counter[str] = Counter(result.summary.to_json())
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
        except Exception as exc:  # noqa: BLE001 - per-item isolation point: count, keep going
            log.warning("page failed url=%s stage=download reason=%s", page.url, exc)
            counts["failures.download"] += 1
            continue
        lead_linked = False
        for ref in page.image_refs:
            if ref in failed:
                counts["skipped.failed_earlier_in_run"] += 1
                continue
            outcome = _index_ref(ref, page, page_id, deps, run_id)
            counts.update(outcome.counts)
            if outcome.failed:
                failed.add(ref)
            lead_linked |= outcome.linked and ref == page.lead_ref
        if page.lead_ref is not None and not lead_linked:
            try:  # the declared lead could not be stored this time: do not keep a stale lead
                deps.store.reset_lead(page_id)
            except InfrastructureError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-item isolation point: log, keep going
                log.warning("page failed url=%s stage=lead reason=%s", page.url, exc)
        if outdated := _outdated_links(page.url, page_id, deps):
            counts["links.outdated"] += outdated
    return Summary.of(counts).to_json()


def _outdated_links(url: str, page_id: int, deps: Deps) -> int:
    """Log and count the page's links this crawl did not see; search ignores them (FR-018, R4)."""
    try:
        outdated = deps.store.outdated_links(page_id)
    except InfrastructureError:
        raise
    except Exception as exc:  # noqa: BLE001 - per-item isolation point: log, keep going
        log.warning("page failed url=%s stage=outdated_links reason=%s", url, exc)
        return 0
    if outdated:
        log.info("outdated links page=%s count=%d", url, outdated)
    return outdated


def _index_ref(ref: str, page: CrawledPage, page_id: int, deps: Deps, run_id: str) -> _RefOutcome:
    """Index one image reference and report what it did."""
    store = deps.store
    is_lead = ref == page.lead_ref
    done: list[str] = []  # counted so far; kept when a later step of this image fails
    try:
        known = store.find_image_by_url(ref)
        if known is not None:
            store.link(page_id, known, is_lead)
            return _RefOutcome(counts=("reused_by_url",), linked=True)
        if store.is_abandoned(ref, "image", config.MAX_ATTEMPTS):
            log.info("link abandoned url=%s kind=image attempts=%d", ref, config.MAX_ATTEMPTS)
            return _RefOutcome(counts=("abandoned.image",))
        response = deps.fetcher.get_image(ref)
        done.append("downloaded")
        sha256 = hashlib.sha256(response.body).digest()
        same_content = store.find_image_by_sha256(sha256)
        if same_content is not None:
            store.add_url_and_link(same_content, ref, page_id, is_lead)
            store.record_success(ref, "image")
            return _RefOutcome(counts=(*done, "reused_by_fingerprint"), linked=True)
        normalized = deps.normalizer.normalize(response.body, response.content_type)
        _, created = store.stage_image(normalized, ref, page_id, is_lead)
        store.record_success(ref, "image")
    except InfrastructureError:
        raise
    except Disallowed as exc:
        log.info("image skipped url=%s reason=%s", ref, exc.reason)
        return _RefOutcome(counts=(*done, f"skipped.{exc.reason}"))
    except ItemError as exc:
        return _image_failed(ref, exc.reason, deps, run_id, done)
    except Exception as exc:  # noqa: BLE001 - per-item isolation point: count, keep going
        return _image_failed(ref, f"unexpected {type(exc).__name__}: {exc}", deps, run_id, done)
    if isinstance(normalized, Skipped):
        log.info("image skipped url=%s reason=%s", ref, normalized.reason)
        outcome = f"skipped.{normalized.reason}"
    else:
        outcome = "staged" if created else "reused_by_fingerprint"
    return _RefOutcome(counts=(*done, outcome), linked=True)


def _image_failed(
    ref: str, reason: str, deps: Deps, run_id: str, done: Sequence[str]
) -> _RefOutcome:
    log.warning("image failed url=%s stage=download reason=%s", ref, reason)
    deps.store.record_failure(ref, "image", reason, run_id)
    return _RefOutcome(counts=(*done, "failures.download"), failed=True)


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
    counts: Counter[str] = Counter()
    for image_id in ids:
        try:
            faces = deps.store.process_pending(
                image_id, lambda pending: deps.embedder.embed(pending.jpeg), profile_id
            )
        except InfrastructureError:
            raise
        except Exception as exc:  # noqa: BLE001 - per-item isolation point: count, keep going
            reason = exc.reason if isinstance(exc, ItemError) else f"unexpected {exc!r}"
            log.warning("image failed id=%d stage=detect reason=%s", image_id, reason)
            counts["failures.detect"] += 1
            deps.store.record_detect_failure(image_id, run_id)
            continue
        if faces is None:
            # Already stored, by an earlier try of this task whose result Airflow lost: count
            # what it stored so the run summary stays complete.
            faces = deps.store.stored_faces(image_id)
            if faces is None:  # locked by another worker, which counts it
                counts["detect.already_done"] += 1
                continue
            counts["detect.recovered"] += 1
        if faces == 0:
            counts["images_no_faces"] += 1
        else:
            counts["images_indexed"] += 1
            counts["faces_stored"] += faces
    return Summary.of(counts).to_json()


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
    total = Counter[str]()
    for part in summaries:
        total.update(part)  # unlike ``+``, ``update`` keeps zero counts
    if deps is not None and (abandoned := deps.store.abandoned_pending(config.MAX_ATTEMPTS)):
        total["abandoned.detect"] += abandoned
    counts = Summary.of(total).to_json()
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
