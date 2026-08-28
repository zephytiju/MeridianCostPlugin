# SPDX-License-Identifier: Apache-2.0
"""Validate released Cost contracts and deterministic V1 golden vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from meridian_storage.plugins.cost import (
    AllocationSpecV1,
    AllocationWeightV1,
    CostPluginFactory,
    CostSchemaProvider,
    PricingModel,
    PricingTier,
    RateCardV1,
    allocate_amount,
    calculate_price,
)

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def conformance_vectors() -> dict[str, object]:
    source = "sha256:" + hashlib.sha256(b"allocation-source-v1").hexdigest()
    card = RateCardV1(
        rate_card_id="golden.compute",
        version=1,
        revision=1,
        provider="example.cloud",
        product="compute",
        meter_id="compute.seconds",
        meter_version=1,
        currency="USD",
        effective_start=datetime(2026, 1, 1, tzinfo=UTC),
        effective_end=datetime(2027, 1, 1, tzinfo=UTC),
        pricing_model=PricingModel.GRADUATED_TIER,
        tiers=(
            PricingTier(Decimal(0), Decimal(100), Decimal("0.10")),
            PricingTier(Decimal(100), Decimal(200), Decimal("0.05")),
            PricingTier(Decimal(200), None, Decimal("0.01")),
        ),
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        provenance={"source": "golden-rate-sheet-v1"},
    )
    price = calculate_price(card, Decimal(250))
    allocation = AllocationSpecV1(
        ("team",),
        (
            AllocationWeightV1({"team": "alpha"}, Decimal(2), source),
            AllocationWeightV1({"team": "beta"}, Decimal(1), source),
        ),
        "golden.teams.v1",
    )
    shares = allocate_amount(price.final_amount, allocation, scale=2)
    bundle = CostSchemaProvider().load()
    return {
        "allocationFingerprint": allocation.fingerprint,
        "allocationShares": [item.to_dict() for item in shares],
        "bundleFingerprint": bundle.fingerprint,
        "price": price.to_dict(),
        "priceFingerprint": price.fingerprint,
        "pricingFingerprint": card.pricing_fingerprint,
        "rateCardFingerprint": card.fingerprint,
        "schemas": {item.ref.canonical: item.fingerprint for item in bundle.schemas},
    }


def verify() -> dict[str, object]:
    schema_path = ROOT / "contracts" / "cost-plugin.v1.json"
    instance_path = ROOT / "contracts" / "conformance" / "plugin-contract.json"
    golden_path = ROOT / "contracts" / "conformance" / "golden" / "fingerprints.json"
    compatibility_path = (
        ROOT / "src" / "meridian_storage" / "plugins" / "cost" / "compatibility.json"
    )
    schema = _load(schema_path)
    instance = _load(instance_path)
    compatibility = _load(compatibility_path)
    golden = _load(golden_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(instance)
    if version("meridian-storage-plugin-cost") != "1.0.1":
        raise AssertionError("installed Cost distribution version differs from released contract")
    if version("meridian-plugin-usage") != "1.0.2":
        raise AssertionError("released Usage dependency must be exactly 1.0.2")
    manifest = CostPluginFactory().manifest()
    if manifest.extensions["repository"] != instance["repository"]:
        raise AssertionError("plugin manifest repository differs from the canonical contract")
    if compatibility["repository"] != instance["repository"]:
        raise AssertionError("compatibility repository differs from the canonical contract")
    if compatibility["catalogs"] != instance["catalogs"]:
        raise AssertionError("compatibility Catalog use differs from the plugin contract")
    plugins = {item.name: item for item in entry_points(group="meridian_storage.plugins")}
    schemas = {item.name: item for item in entry_points(group="meridian_storage.schemas")}
    if plugins["cost"].load() is not CostPluginFactory:
        raise AssertionError("Cost plugin entry point is not discoverable")
    if schemas["cost"].load() is not CostSchemaProvider:
        raise AssertionError("Cost schema entry point is not discoverable")
    vectors = conformance_vectors()
    if golden != {
        "$comment": "SPDX-License-Identifier: Apache-2.0",
        "formatVersion": "meridian.cost.golden.v1",
        "vectors": vectors,
    }:
        raise AssertionError("deterministic Cost vectors differ from released golden evidence")
    return {
        "catalogsOwned": [],
        "compatibilitySha256": _sha256(compatibility_path),
        "contractSha256": _sha256(instance_path),
        "formatVersion": "meridian.cost.conformance-report.v1",
        "goldenSha256": _sha256(golden_path),
        "package": "meridian-storage-plugin-cost",
        "passed": True,
        "repository": "zephytiju/MeridianCostPlugin",
        "usageDependency": "meridian-plugin-usage==1.0.2",
        "vectors": vectors,
        "version": "1.0.1",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-vectors", action="store_true")
    arguments = parser.parse_args()
    if arguments.print_vectors:
        print(json.dumps(conformance_vectors(), indent=2, sort_keys=True))
        return
    report = verify()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
