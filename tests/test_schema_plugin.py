# SPDX-License-Identifier: Apache-2.0
"""Schema, discovery, compatibility, and boundary contract tests."""

from __future__ import annotations

from importlib.metadata import entry_points, version

import pytest

from meridian_storage.plugins.cost import (
    CostPluginFactory,
    CostSchemaProvider,
    InvalidCost,
    calculation_schema,
    cost_record_schema,
    cost_schemas,
    rate_card_schema,
)


def test_schema_provider_publishes_three_cost_resources() -> None:
    provider = CostSchemaProvider()
    bundle = provider.load()
    assert provider.provider_id == "cost"
    assert provider.provider_contract_version == "1.0.0"
    assert bundle.provider_id == "cost"
    assert len(bundle.schemas) == 3
    assert len(bundle.resources) == 3
    assert {item.ref.name for item in bundle.schemas} == {
        "rate_cards",
        "calculations",
        "records",
    }
    assert {item.ref.catalog for item in bundle.schemas} == {"structured"}
    assert bundle.extensions["distribution"] == "meridian-plugin-cost"
    assert bundle.extensions["usageDependency"] == "meridian-plugin-usage==1.0.2"
    assert bundle.fingerprint.startswith("sha256:")


def test_individual_schema_helpers_are_deterministic() -> None:
    schemas = cost_schemas()
    assert schemas == (
        rate_card_schema(),
        calculation_schema(),
        cost_record_schema(),
    )
    assert all(item.fingerprint.startswith("sha256:") for item in schemas)
    assert all(item.consistency == "strong" for item in schemas)


def test_plugin_manifest_preserves_canonical_repository_and_boundaries() -> None:
    factory = CostPluginFactory()
    manifest = factory.manifest()
    assert manifest.plugin_id == "cost"
    assert manifest.plugin_version == "1.0.1"
    assert manifest.extensions["repository"] == "zephytiju/MeridianCostPlugin"
    assert manifest.extensions["distribution"] == "meridian-plugin-cost"
    assert manifest.extensions["catalog"] == "structured"
    assert manifest.extensions["service"] == "false"
    assert manifest.extensions["engineAuthority"] == "false"
    assert manifest.extensions["nativeQuery"] == "false"
    with pytest.raises(InvalidCost, match="ready Meridian"):
        factory.create(object())  # type: ignore[arg-type]


def test_installed_distribution_and_entry_points_are_exact() -> None:
    assert version("meridian-plugin-cost") == "1.0.1"
    assert version("meridian-plugin-usage") == "1.0.2"
    plugins = {item.name: item for item in entry_points(group="meridian_storage.plugins")}
    schemas = {item.name: item for item in entry_points(group="meridian_storage.schemas")}
    assert plugins["cost"].load() is CostPluginFactory
    assert schemas["cost"].load() is CostSchemaProvider
