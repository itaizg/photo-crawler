"""Runs every pipeline stage in DAG order, in-process, against the mini-site (research R13)."""

from dataclasses import dataclass

from face_index import pipeline
from face_index.config import Site
from face_index.http import HttpFetcher
from face_index.models import RequestKind
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


@dataclass
class RunOutcome:
    summary: dict[str, int]
    crawl: dict[str, dict[str, object]]


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

    try:
        profile_id = pipeline.init_schema(deps_for(None))
        summaries: list[dict[str, int]] = []
        crawls: dict[str, dict[str, object]] = {}
        for s in minisite.SITES:
            deps = deps_for(s)
            crawls[s.name] = pipeline.crawl(s, params, deps, run_id)
            summaries.append(pipeline.download(s, crawls[s.name], deps, run_id))
        deps = deps_for(None)
        for batch in pipeline.plan_batches(deps):
            summaries.append(pipeline.detect(batch, profile_id, deps, run_id))
        return RunOutcome(pipeline.summarize(summaries, deps), crawls)
    finally:
        pacer.close()
        store.close()
