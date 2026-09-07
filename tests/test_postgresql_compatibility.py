# SPDX-License-Identifier: Apache-2.0
"""Registry dependency compatibility through real Meridian/PostgreSQL persistence."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from conftest import (
    END,
    NOW,
    START,
    make_aggregate,
    make_fingerprint,
    make_rate_card,
    make_usage_input,
)
from meridian_storage import OperationContext
from meridian_storage.plugins.cost import (
    CostCalculator,
    CostRecordV1,
    CostRepository,
    RepositoryUsageProvider,
    StaticUsageProvider,
)
from meridian_storage.plugins.usage import MeterV1, UsageRepository, UsageScope, UsageWindow
from postgres_backend import USAGE_RESOURCES


def assert_scoped_readback(cost, first):
    rows = (
        cost.queries.records(
            {"tenant": "acme"},
            END,
            END + timedelta(microseconds=1),
            where={"currency": {"eq": "USD"}},
        )
        .execute()
        .items
    )
    assert len(rows) == 1
    assert CostRecordV1.from_mapping(rows[0]).to_dict() == first.records[0].to_dict()
    assert not cost.queries.records({"tenant": "acme"}, START, END).execute().items
    assert (
        not cost.queries.records({"tenant": "other"}, END, END + timedelta(microseconds=1))
        .execute()
        .items
    )
    created = first.calculation.created_at
    calculations = (
        cost.queries.calculations({"tenant": "acme"}, created, created + timedelta(microseconds=1))
        .execute()
        .items
    )
    assert len(calculations) == 1
    assert calculations[0]["fingerprint"] == first.calculation.fingerprint


@pytest.mark.integration
@pytest.mark.parametrize("provider_kind", ["repository", "static"])
@pytest.mark.parametrize("total", ["12", "12.000000000000", "12.000000000000000000", "0", "2.500"])
def test_persisted_usage_calculation_replay_and_correction(postgres_backend, total, provider_kind):
    ctx = OperationContext("test:cost", tenant=uuid4().hex, scope={"runtime": "cost"})
    runtime = postgres_backend.start()
    aggregate = make_aggregate(total=Decimal(total))
    with runtime.context(ctx):
        usage = UsageRepository(runtime, USAGE_RESOURCES)
        cost = CostRepository(runtime)
        meter = MeterV1("api.requests", 1, "count", "request")
        assert usage.register_meter(meter) == (meter, False)
        assert usage.register_meter(meter) == (meter, True)
        assert usage.put_aggregate(aggregate) == (aggregate, False)
        draft, _ = cost.create_rate_card(make_rate_card())
        validated = cost.validate_rate_card(draft.rate_card_id, 1, expected_revision=1, now=NOW)
        card = cost.publish_rate_card(
            draft.rate_card_id, 1, expected_revision=validated.revision, now=NOW
        )
        stored, replayed = usage.put_aggregate(aggregate)
        assert replayed
        provider = (
            RepositoryUsageProvider(usage, page_size=1)
            if provider_kind == "repository"
            else StaticUsageProvider(make_usage_input(stored))
        )
        normalized = provider.fetch(card, UsageScope({"tenant": "acme"}), UsageWindow(START, END))
        assert normalized.aggregates[0].fingerprint == aggregate.fingerprint
        first = CostCalculator(provider, cost).calculate(
            scope={"tenant": "acme"},
            start=START,
            end=END,
            rate_card=card.ref,
            occurred_at=END,
        )
        assert not first.replayed
        assert first.calculation.usage_fingerprint == normalized.fingerprint
        assert first.calculation.aggregate_refs == (aggregate.version_id,)
        assert first.records[0].lineage["usageFingerprint"] == normalized.fingerprint
    runtime.close()
    fresh = postgres_backend.start()
    with fresh.context(ctx):
        usage, cost = UsageRepository(fresh, USAGE_RESOURCES), CostRepository(fresh)
        assert usage.put_aggregate(aggregate) == (aggregate, True)
        assert cost.get_rate_card(card.rate_card_id, 1).to_dict() == card.to_dict()
        assert_scoped_readback(cost, first)
        assert (
            cost.get_calculation(first.calculation.calculation_id, 1).to_dict()
            == first.calculation.to_dict()
        )
        assert (
            cost.get_cost_record(first.records[0].cost_id).to_dict() == first.records[0].to_dict()
        )
        for provider in (
            (usage, StaticUsageProvider(normalized))
            if provider_kind == "repository"
            else (StaticUsageProvider(normalized),)
        ):
            replay = CostCalculator(provider, cost).calculate(
                scope={"tenant": "acme"},
                start=START,
                end=END,
                rate_card=card.ref,
            )
            assert replay.replayed
            assert replay.calculation.to_dict() == first.calculation.to_dict()
            assert replay.records[0].to_dict() == first.records[0].to_dict()
        corrected = replace(
            aggregate,
            revision=2,
            total=aggregate.total + Decimal("1.5"),
            source_fingerprint=make_fingerprint("correction"),
            supersedes=aggregate.version_id,
        )
        usage.put_aggregate(corrected)
        stored, _ = usage.put_aggregate(corrected)
        next_provider = (
            usage
            if provider_kind == "repository"
            else StaticUsageProvider(make_usage_input(stored))
        )
        second = CostCalculator(next_provider, cost).calculate(
            scope={"tenant": "acme"},
            start=START,
            end=END,
            rate_card=card.ref,
            occurred_at=END,
        )
        assert second.calculation.revision == 2
        assert second.calculation.supersedes == first.calculation.version_id
        assert second.records[0].supersedes == first.records[0].cost_id
        assert second.calculation.usage_fingerprint != first.calculation.usage_fingerprint
        assert second.calculation.aggregate_refs == (corrected.version_id,)
        assert (
            cost.get_calculation(first.calculation.calculation_id, 1).to_dict()
            == first.calculation.to_dict()
        )
        assert (
            cost.get_cost_record(first.records[0].cost_id).to_dict() == first.records[0].to_dict()
        )
        assert (
            cost.get_calculation(second.calculation.calculation_id, 2).to_dict()
            == second.calculation.to_dict()
        )
