"""DAG tests run without a scheduler (constitution IV): they import the DAG file and inspect it."""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

DAG_FILE = Path(__file__).resolve().parents[2] / "dags" / "image_indexer.py"


@pytest.fixture(scope="session")
def dag_module(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    os.environ.setdefault("AIRFLOW_HOME", str(tmp_path_factory.mktemp("airflow_home")))
    os.environ.setdefault("AIRFLOW__CORE__LOAD_EXAMPLES", "false")
    pytest.importorskip("airflow.sdk")
    spec = importlib.util.spec_from_file_location("image_indexer_dag_file", DAG_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
