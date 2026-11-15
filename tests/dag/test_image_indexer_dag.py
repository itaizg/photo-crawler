from types import ModuleType
from typing import Any

import pytest

from face_index import config
from face_index.errors import ItemsFailed, StageFailed


@pytest.fixture(scope="module")
def dag(dag_module: ModuleType) -> Any:
    return dag_module.image_indexer_dag


def test_dag_settings(dag: Any) -> None:
    assert dag.dag_id == "image_indexer"
    assert dag.schedule is None or str(dag.timetable.summary).lower() in {"none", "never"}
    assert dag.max_active_runs == 1
    assert dag.catchup is False


def test_tasks_and_wiring_match_research_r3(dag: Any) -> None:
    ids = set(dag.task_ids)
    expected = {"init_schema", "plan_batches", "detect", "summarize"}
    for site in config.ENABLED_SITES:
        expected |= {f"crawl_{site.name}", f"download_{site.name}"}
    assert ids == expected
    for site in config.ENABLED_SITES:
        crawl = dag.get_task(f"crawl_{site.name}")
        download = dag.get_task(f"download_{site.name}")
        assert "init_schema" in crawl.upstream_task_ids
        assert crawl.task_id in download.upstream_task_ids
        assert download.task_id in dag.get_task("plan_batches").upstream_task_ids
    assert "plan_batches" in dag.get_task("detect").upstream_task_ids
    assert {"detect"} <= dag.get_task("summarize").upstream_task_ids


def test_disabled_sites_get_no_tasks(dag: Any) -> None:
    disabled = [site for site in config.SITES if not site.enabled]
    assert [site.name for site in disabled] == ["people"]
    for site in disabled:
        assert f"crawl_{site.name}" not in dag.task_ids
        assert f"download_{site.name}" not in dag.task_ids


def test_pools_keep_site_steps_apart_and_bound_face_work(dag: Any) -> None:
    for site in config.ENABLED_SITES:
        assert dag.get_task(f"crawl_{site.name}").pool == f"http_{site.name}"
        assert dag.get_task(f"download_{site.name}").pool == f"http_{site.name}"
    assert dag.get_task("detect").pool == "face_cpu"


def test_retries_and_summary_trigger_rule(dag: Any) -> None:
    assert all(task.retries == 1 for task in dag.tasks)
    assert str(dag.get_task("summarize").trigger_rule) in {"all_done", "TriggerRule.ALL_DONE"}


def test_steps_after_a_failed_request_still_run(dag: Any) -> None:
    """A crawl or download marked failed for a failed request must not stop the run (FR-023)."""
    after = ["plan_batches", *(f"download_{site.name}" for site in config.ENABLED_SITES)]
    for task_id in after:
        assert str(dag.get_task(task_id).trigger_rule) in {"all_done", "TriggerRule.ALL_DONE"}


def test_a_stage_with_failed_items_publishes_its_result_then_fails(
    dag: Any, dag_module: ModuleType, monkeypatch: Any
) -> None:
    pushed: dict[str, object] = {}

    class Ti:
        def xcom_push(self, key: str, value: object) -> None:
            pushed[key] = value

    def failing_crawl(*args: object) -> dict[str, object]:
        raise ItemsFailed("crawl of cnn: 1 page request(s) failed", {"site": "cnn", "pages": []})

    monkeypatch.setattr(
        dag_module,
        "get_current_context",
        lambda: {"params": {}, "run_id": "manual__x", "ti": Ti()},
    )
    monkeypatch.setattr(dag_module, "_deps", lambda params, site_name=None: None)
    monkeypatch.setattr(dag_module.pipeline, "crawl", failing_crawl)
    with pytest.raises(ItemsFailed):
        dag.get_task("crawl_cnn").python_callable("cnn")
    assert pushed == {"return_value": {"site": "cnn", "pages": []}}


def test_download_fails_when_the_crawl_left_no_result(dag: Any) -> None:
    with pytest.raises(StageFailed, match="crawl of cnn produced no result"):
        dag.get_task("download_cnn").python_callable("cnn", None)


def test_task_wrappers_forward_run_id(dag: Any, dag_module: ModuleType, monkeypatch: Any) -> None:
    seen: dict[str, object] = {}

    def fake_download(site: object, payload: object, deps: object, run_id: str) -> dict[str, int]:
        seen["run_id"] = run_id
        return {}

    monkeypatch.setattr(
        dag_module, "get_current_context", lambda: {"params": {}, "run_id": "manual__x"}
    )
    monkeypatch.setattr(dag_module, "_deps", lambda params, site_name=None: None)
    monkeypatch.setattr(dag_module.pipeline, "download", fake_download)
    dag.get_task("download_cnn").python_callable("cnn", {"site": "cnn", "pages": []})
    assert seen["run_id"] == "manual__x"
