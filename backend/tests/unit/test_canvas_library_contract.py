"""Run the same records as BeefTV's unchanged parseAssetRecord implementation."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_library import CanvasLibraryDocument

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "canvas"
        / "tests"
        / "fixtures"
        / "asset-contract.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_source_asset_contract(case):
    record = deepcopy({**FIXTURE["common"], **case["input"]})
    if not case["valid"]:
        with pytest.raises(ValidationError):
            CanvasLibraryDocument.model_validate(record)
        return
    asset = CanvasLibraryDocument.model_validate(record)
    assert asset.data == case["expected_data"]
    canonical = asset.model_dump(mode="json", by_alias=True)
    for key, value in case.get("expected_fields", {}).items():
        assert canonical[key] == value
    # The service validates a second time before writing. Normalization is stable.
    assert CanvasLibraryDocument.model_validate(asset.model_dump()).data == asset.data


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_media_numbers_are_rejected(value):
    record = deepcopy({**FIXTURE["common"], **FIXTURE["cases"][2]["input"]})
    record["data"]["width"] = value
    with pytest.raises(ValidationError):
        CanvasLibraryDocument.model_validate(record)


@pytest.mark.parametrize(
    "key",
    ["resource:", "resource:0", "resource:1oops", "resource:18446744073709551616"],
)
def test_host_resource_identity_is_validated_before_database_lookup(key):
    record = deepcopy({**FIXTURE["common"], **FIXTURE["cases"][2]["input"]})
    record["data"]["storageKey"] = key
    with pytest.raises(ValidationError):
        CanvasLibraryDocument.model_validate(record)


def test_url_only_media_remains_an_explicit_unimplemented_import_boundary():
    record = deepcopy({**FIXTURE["common"], **FIXTURE["cases"][2]["input"]})
    record["data"].pop("storageKey")
    record["data"]["dataUrl"] = "https://media.example.test/image.png"
    with pytest.raises(ValidationError, match="durable resource"):
        CanvasLibraryDocument.model_validate(record)
