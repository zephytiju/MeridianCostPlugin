# SPDX-License-Identifier: Apache-2.0
"""Released-file and deterministic conformance tests."""

from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.contract
def test_released_contract_instance_validates() -> None:
    schema = json.loads((ROOT / "contracts" / "cost-plugin.v1.json").read_text())
    instance = json.loads((ROOT / "contracts" / "conformance" / "plugin-contract.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(instance)
    assert instance["repository"] == "zephytiju/MeridianCostPlugin"
    assert instance["catalogs"]["owned"] == []
    assert instance["dependencies"]["usage"] == "meridian-plugin-usage==1.0.2"


@pytest.mark.contract
def test_wheel_compatibility_resource_is_present() -> None:
    path = resources.files("meridian_storage.plugins.cost").joinpath("compatibility.json")
    compatibility = json.loads(path.read_text(encoding="utf-8"))
    assert compatibility["repository"] == "zephytiju/MeridianCostPlugin"
    assert compatibility["boundaries"]["usageStorageAccess"] is False
    assert compatibility["catalogs"] == {"owned": [], "used": ["evidence", "structured"]}


@pytest.mark.conformance
def test_conformance_report_is_deterministic() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts" / "verify_contracts.py"))
    verify = cast(Callable[[], dict[str, object]], namespace["verify"])
    first = json.dumps(verify(), indent=2, sort_keys=True)
    second = json.dumps(verify(), indent=2, sort_keys=True)
    assert first == second
    report = json.loads(first)
    assert report["passed"] is True
    assert report["vectors"]["price"]["finalAmount"] == "15.5"
    assert len(report["vectors"]["schemas"]) == 3


@pytest.mark.contract
def test_source_has_no_superseded_cost_identifiers() -> None:
    tracked_roots = [ROOT / "src", ROOT / "contracts", ROOT / "docs", ROOT / ".github"]
    contents = "\n".join(
        path.read_text(encoding="utf-8")
        for root in tracked_roots
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert "MeridianPluginCost" not in contents
    assert "meridian-plugin-cost" not in contents


@pytest.mark.contract
def test_release_recovery_preserves_deterministic_attestation() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "actions/attest-sbom@" not in workflow
    assert 'document["serialNumber"]' in workflow
    assert '--source-commit "${{ steps.source.outputs.commit }}"' in workflow
    assert "github.event_name == 'push' || github.event_name == 'workflow_dispatch'" in workflow
    assert "skip-existing: true" in workflow
