from types import ModuleType
from typing import Any

import pytest

from face_index import config


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
    for site in config.SITES:
        expected |= {f"crawl_{site.name}", f"download_{site.name}"}
    assert ids == expected
    for site in config.SITES:
        crawl = dag.get_task(f"crawl_{site.name}")
        download = dag.get_task(f"download_{site.name}")
        assert "init_schema" in crawl.upstream_task_ids
        assert crawl.task_id in download.upstream_task_ids
        assert download.task_id in dag.get_task("plan_batches").upstream_task_ids
    assert "plan_batches" in dag.get_task("detect").upstream_task_ids
    assert {"detect"} <= dag.get_task("summarize").upstream_task_ids


def test_pools_keep_site_steps_apart_and_bound_face_work(dag: Any) -> None:
    for site in config.SITES:
        assert dag.get_task(f"crawl_{site.name}").pool == f"http_{site.name}"
        assert dag.get_task(f"download_{site.name}").pool == f"http_{site.name}"
    assert dag.get_task("detect").pool == "face_cpu"


def test_retries_and_summary_trigger_rule(dag: Any) -> None:
    assert all(task.retries == 1 for task in dag.tasks)
    assert str(dag.get_task("summarize").trigger_rule) in {"none_failed", "TriggerRule.NONE_FAILED"}


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
