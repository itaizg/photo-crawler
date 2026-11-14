"""Runs every pipeline stage in DAG order, in-process, against the mini-site (research R13)."""

from collections.abc import Callable
from contextlib import closing
from typing import Any

from face_index import pipeline
from face_index.config import Site
from face_index.errors import StageFailed
from face_index.http import HttpFetcher
from face_index.models import Record, RequestKind
from face_index.normalize import PillowNormalizer
from face_index.pacing import PostgresPacer
from face_index.pipeline import Deps, RunParams
from face_index.ports import FaceEmbedder
from face_index.store import PostgresIndexStore
from face_index.urls import in_scope
from tests.fakes import FakeResolver, MockSite
from tests.fixtures.minisite import minisite

FAST: dict[RequestKind, float] = {
    "page": 1000.0,
    "image": 1000.0,
}  # pacing adds no meaningful delay


class RunOutcome(Record):
    summary: dict[str, int]
    crawl: dict[str, dict[str, object]]
    failed_stages: tuple[str, ...] = ()


def run_once(
    dsn: str,
    site: MockSite,
    embedder: FaceEmbedder,
    run_id: str,
    params: RunParams | None = None,
) -> RunOutcome:
    params = params or RunParams(depth=1, page_cap=None)
    store = PostgresIndexStore(dsn)
    pacer = PostgresPacer(dsn, FAST)
    normalizer = PillowNormalizer()

    def deps_for(scope_site: Site | None) -> Deps:
        def scope(url: str) -> bool:
            return scope_site is None or in_scope(url, scope_site)

        fetcher = HttpFetcher(
            pacer=pacer, resolver=FakeResolver(), transport=site.transport(), scope=scope
        )
        return Deps(store=store, fetcher=fetcher, normalizer=normalizer, embedder=embedder)

    failed: list[str] = []

    def stage(name: str, run: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any] | None:
        """Like an Airflow task: a stage whose items all failed has no result, the run goes on."""
        try:
            return run(*args)
        except StageFailed:
            failed.append(name)
            return None

    with closing(pacer), closing(store):
        profile_id = pipeline.init_schema(deps_for(None))
        summaries: list[dict[str, int] | None] = []
        crawls: dict[str, dict[str, object]] = {}
        for s in minisite.SITES:
            deps = deps_for(s)
            crawled = stage(f"crawl_{s.name}", pipeline.crawl, s, params, deps, run_id)
            if crawled is None:  # download is upstream_failed in Airflow
                summaries.append(None)
                continue
            crawls[s.name] = crawled
            summaries.append(
                stage(f"download_{s.name}", pipeline.download, s, crawled, deps, run_id)
            )
        deps = deps_for(None)
        summaries.extend(
            stage("detect", pipeline.detect, batch, profile_id, deps, run_id)
            for batch in pipeline.plan_batches(deps)
        )
        done = [part for part in summaries if part is not None]
        return RunOutcome(
            summary=pipeline.summarize(done, deps), crawl=crawls, failed_stages=tuple(failed)
        )
