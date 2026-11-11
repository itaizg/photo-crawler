"""Trigger parameters (contracts/dag-params.md, FR-005)."""

from types import ModuleType
from typing import Any

import pytest

from face_index import config
from face_index.pipeline import RunParams


@pytest.fixture(scope="module")
def params(dag_module: ModuleType) -> dict[str, Any]:
    return dag_module.PARAMS


def test_defaults_come_from_config(params: dict[str, Any]) -> None:
    assert params["depth"].value == config.DEFAULT_DEPTH
    assert params["page_cap"].value == config.DEFAULT_PAGE_CAP
    assert params["page_rate"].value == config.DEFAULT_PAGE_RATE
    assert params["image_rate"].value == config.DEFAULT_IMAGE_RATE


@pytest.mark.parametrize(
    ("name", "value"),
    [("depth", -1), ("depth", 11), ("page_cap", 0), ("page_rate", 0), ("image_rate", 25)],
)
def test_schema_rejects_invalid_values(params: dict[str, Any], name: str, value: object) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 - Airflow raises ParamValidationError
        params[name].resolve(value=value)


def test_null_page_cap_is_accepted(params: dict[str, Any]) -> None:
    assert params["page_cap"].resolve(value=None) is None


@pytest.mark.parametrize(
    "bad",
    [{"depth": 11}, {"page_cap": 0}, {"page_rate": 0}, {"image_rate": 25}, {"depth": "2"}],
)
def test_run_params_validate_independently_of_airflow(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        RunParams.from_mapping(bad)


def test_run_params_defaults() -> None:
    assert RunParams.from_mapping({}) == RunParams()
    assert RunParams.from_mapping({"page_cap": None}).page_cap is None
