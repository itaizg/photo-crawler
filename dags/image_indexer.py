"""Image indexer: crawl the enabled sites, store every visible face for image-based search.

```
init_schema ─┬─ crawl_cnn    ── download_cnn    ─┐
             └─ crawl_people ── download_people ─┴─ plan_batches ── detect (mapped) ── summarize
```

Each site in `config.ENABLED_SITES` gets its own crawl and download pair. People is configured
but disabled (it refuses the crawler with HTTP 402), so today only the CNN pair exists.

A crawl or download task in which a page or image request failed is marked failed, but it
first publishes its result, and the tasks after it run whatever it did (`all_done`): the run
ends failed and still indexes everything that succeeded (FR-023).

Trigger with optional params `depth`, `page_cap` (null = no cap), `page_rate`, `image_rate`.
Every task is a thin wrapper around a `face_index.pipeline` function (research R3).
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Final

from airflow.sdk import DAG, Param, dag, get_current_context, task

from face_index import config, pipeline
from face_index.errors import ItemsFailed, StageFailed

PARAMS: Final[Mapping[str, Param]] = {
    "depth": Param(
        config.DEFAULT_DEPTH,
        type="integer",
        minimum=0,
        maximum=config.MAX_DEPTH,
        description="Link hops from each home page; 0 = home pages only.",
    ),
    "page_cap": Param(
        config.DEFAULT_PAGE_CAP,
        type=["integer", "null"],
        minimum=1,
        description="Maximum pages fetched per site; null = no cap.",
    ),
    "page_rate": Param(
        config.DEFAULT_PAGE_RATE,
        type="number",
        minimum=config.MIN_RATE,
        maximum=config.MAX_PAGE_RATE,
        description="Page requests per second, per host.",
    ),
    "image_rate": Param(
        config.DEFAULT_IMAGE_RATE,
        type="number",
        minimum=config.MIN_RATE,
        maximum=config.MAX_IMAGE_RATE,
        description="Image requests per second, per host.",
    ),
}


def _context() -> tuple[pipeline.RunParams, str]:
    context = get_current_context()
    params = pipeline.RunParams.model_validate(dict(context["params"]))
    return params, str(context["run_id"])


def _deps(params: pipeline.RunParams, site_name: str | None = None) -> pipeline.Deps:
    site = config.site_by_name(site_name) if site_name else None
    return pipeline.build_default_deps(config.database_dsn(), params, site)


def _publish_then_fail[T: Mapping[str, object]](stage: Callable[[], T]) -> T:
    """Run a stage; when items failed, publish its result, then fail the task.

    A failed task pushes no return value of its own, so the result is pushed here. The tasks
    downstream read it as usual and work with everything that did succeed.
    """
    try:
        return stage()
    except ItemsFailed as exc:
        get_current_context()["ti"].xcom_push(key="return_value", value=dict(exc.result))
        raise


@dag(
    dag_id="image_indexer",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    params=PARAMS,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["photo-crawler"],
    doc_md=__doc__,
)
def image_indexer() -> None:
    @task
    def init_schema() -> int:
        params, _ = _context()
        return pipeline.init_schema(_deps(params))

    @task
    def crawl(site_name: str) -> dict[str, object]:
        params, run_id = _context()
        site = config.site_by_name(site_name)
        return _publish_then_fail(
            lambda: pipeline.crawl(site, params, _deps(params, site_name), run_id)
        )

    # all_done: a crawl whose pages partly failed is marked failed but still has a result.
    @task(trigger_rule="all_done")
    def download(site_name: str, crawl_payload: dict[str, object] | None) -> dict[str, int]:
        if crawl_payload is None:
            raise StageFailed(f"crawl of {site_name} produced no result; see the failed task")
        params, run_id = _context()
        site = config.site_by_name(site_name)
        return _publish_then_fail(
            lambda: pipeline.download(site, crawl_payload, _deps(params, site_name), run_id)
        )

    # all_done: images staged by a download that was marked failed are still detected.
    @task(trigger_rule="all_done")
    def plan_batches() -> list[list[int]]:
        params, _ = _context()
        return pipeline.plan_batches(_deps(params))

    @task(pool="face_cpu")
    def detect(ids: list[int], profile_id: int) -> dict[str, int]:
        params, run_id = _context()
        return pipeline.detect(ids, profile_id, _deps(params), run_id)

    # all_done: the summary is logged even when a stage failed; pipeline.summarize then fails
    # this task too, so the run is still marked failed. It is the DAG's only leaf task.
    @task(trigger_rule="all_done")
    def summarize(
        downloads: list[dict[str, int] | None], detections: list[dict[str, int] | None]
    ) -> dict[str, int]:
        params, _ = _context()
        return _publish_then_fail(
            lambda: pipeline.summarize([*downloads, *detections], _deps(params))
        )

    profile_id = init_schema()
    downloads = []
    for site in config.ENABLED_SITES:
        pool = f"http_{site.name}"
        crawled = crawl.override(task_id=f"crawl_{site.name}", pool=pool)(site.name)
        profile_id >> crawled
        downloads.append(
            download.override(task_id=f"download_{site.name}", pool=pool)(
                site.name,
                crawled,  # ty: ignore[invalid-argument-type]  # XComArg resolves at runtime
            )
        )
    batches = plan_batches()
    downloads >> batches
    detections = detect.partial(profile_id=profile_id).expand(ids=batches)
    summarize(downloads, detections)  # ty: ignore[invalid-argument-type]  # XComArgs at runtime


image_indexer_dag: Final[DAG] = image_indexer()
