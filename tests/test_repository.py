# SPDX-License-Identifier: Apache-2.0
"""Versioned rate-card repository and query tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conftest import END, NOW, START, MemoryExecutor, make_rate_card
from meridian_storage import ResourceRef
from meridian_storage.plugins.cost import (
    CostOrder,
    CostQuery,
    CostResources,
    InvalidCost,
    MissingRateCard,
    OverlappingRateCard,
    PublicationState,
    StaleRateCardRevision,
)
from meridian_storage.plugins.usage import UsageResources, UsageScope, UsageWindow


def test_create_replay_replace_and_stale_revision(repository) -> None:
    draft = make_rate_card()
    created, replayed = repository.create_rate_card(draft)
    assert created == draft
    assert replayed is False
    assert repository.create_rate_card(draft) == (draft, True)
    changed = replace(draft, revision=2, unit_price=Decimal("0.003"))
    assert repository.replace_draft(changed, expected_revision=1) == changed
    assert repository.get_rate_card(draft.rate_card_id, 1) == changed
    assert repository.get_rate_card(draft.rate_card_id, 1, revision=1) == draft
    with pytest.raises(StaleRateCardRevision):
        repository.replace_draft(replace(changed, revision=3), expected_revision=1)
    with pytest.raises(MissingRateCard):
        repository.get_rate_card("missing", 1)


def test_lifecycle_is_append_only_and_delete_means_retire(repository, executor) -> None:
    draft, _ = repository.create_rate_card(make_rate_card())
    validated = repository.validate_rate_card(
        draft.rate_card_id,
        1,
        expected_revision=1,
        now=NOW,
    )
    published = repository.publish_rate_card(
        draft.rate_card_id,
        1,
        expected_revision=2,
        now=NOW,
    )
    retired = repository.delete_rate_card(
        draft.rate_card_id,
        1,
        expected_revision=3,
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert validated.state is PublicationState.VALIDATED
    assert published.state is PublicationState.PUBLISHED
    assert retired.state is PublicationState.RETIRED
    assert len(executor.records[repository.resources.rate_cards]) == 4
    with pytest.raises(InvalidCost, match="transition"):
        repository.retire_rate_card(draft.rate_card_id, 1, expected_revision=4)


def test_publish_rejects_overlapping_pricing_keys(repository) -> None:
    first, _ = repository.create_rate_card(make_rate_card())
    first = repository.validate_rate_card(first.rate_card_id, 1, expected_revision=1, now=NOW)
    repository.publish_rate_card(first.rate_card_id, 1, expected_revision=2, now=NOW)
    second, _ = repository.create_rate_card(make_rate_card(version=2))
    second = repository.validate_rate_card(second.rate_card_id, 2, expected_revision=1, now=NOW)
    with pytest.raises(OverlappingRateCard):
        repository.publish_rate_card(second.rate_card_id, 2, expected_revision=2, now=NOW)


def test_resolve_rate_card_requires_unique_full_coverage(repository) -> None:
    draft, _ = repository.create_rate_card(make_rate_card(matching_dimensions={"region": "us"}))
    validated = repository.validate_rate_card(draft.rate_card_id, 1, expected_revision=1, now=NOW)
    repository.publish_rate_card(validated.rate_card_id, 1, expected_revision=2, now=NOW)
    resolved = repository.resolve_rate_card(
        provider="example.cloud",
        product="requests",
        meter_id="api.requests",
        meter_version=1,
        window=UsageWindow(START, END),
        dimensions={"region": "us"},
    )
    assert resolved.rate_card_id == draft.rate_card_id
    with pytest.raises(MissingRateCard):
        repository.resolve_rate_card(
            provider="example.cloud",
            product="requests",
            meter_id="api.requests",
            meter_version=1,
            window=UsageWindow(START, END),
            dimensions={"region": "eu"},
        )


def test_cost_resources_enforce_usage_isolation() -> None:
    resources = CostResources()
    resources.assert_usage_isolation(UsageResources())
    manifest = resources.isolation_manifest()
    assert manifest["usageAccess"] == "released-public-api-only"
    assert manifest["distinctPhysicalPlacementRequired"] is True
    with pytest.raises(InvalidCost, match="overlap"):
        CostResources(rate_cards=UsageResources().aggregates).assert_usage_isolation(
            UsageResources()
        )
    with pytest.raises(InvalidCost, match="distinct"):
        CostResources(
            rate_cards=ResourceRef("structured", "cost", "same"),
            calculations=ResourceRef("structured", "cost", "same"),
        )


def test_cost_query_is_mapping_first_and_serialized(executor: MemoryExecutor) -> None:
    resource = ResourceRef("structured", "cost", "records")
    executor.seed(resource, {"costId": "cost-1", "currency": "USD", "amount": "1.00"})
    query = CostQuery(
        executor,
        resource,
        {"currency": {"in": ["USD", "EUR"]}, "amount": {"gte": "1.00"}},
        select=("costId", "currency"),
        order_by=(CostOrder("costId", "desc"),),
        limit=10,
    )
    assert query.expression.catalog == "structured"
    assert query.expression.method == "query"
    assert query.logical_plan.operation == "scan"
    assert query.fingerprint.startswith("sha256:")
    result = query.execute()
    assert result.items[0]["costId"] == "cost-1"
    assert result.provenance["adapter"] == "logical-memory-test"
    with pytest.raises(InvalidCost, match="direction"):
        CostOrder("costId", "sideways")
    with pytest.raises(InvalidCost, match="between"):
        CostQuery(executor, resource, limit=501)
    with pytest.raises(InvalidCost, match="unsupported"):
        CostQuery(executor, resource, {"costId": {"regex": "x"}})


def test_scoped_queries_derive_scope_and_time(repository) -> None:
    scope = UsageScope({"tenant": "acme"})
    calculation = repository.queries.calculations(scope, START, END)
    record = repository.queries.records(scope, START, END)
    assert calculation.where["scopeFingerprint"] == scope.fingerprint
    assert "createdAt" in calculation.where
    assert "occurredAt" in record.where
    with pytest.raises(InvalidCost, match="derived"):
        repository.queries.records(scope, START, END, where={"occurredAt": "x"})
    with pytest.raises(InvalidCost, match="half-open"):
        repository.queries.records(scope, END, START)
