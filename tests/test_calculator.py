# SPDX-License-Identifier: Apache-2.0
"""End-to-end calculation, persistence, replay, and evidence tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import (
    END,
    START,
    MemoryExecutor,
    make_aggregate,
    make_fingerprint,
    make_usage_input,
)
from meridian_storage.plugins.cost import (
    AllocationSpecV1,
    AllocationWeightV1,
    CalculationV1,
    CostCalculator,
    CostRecordV1,
    InvalidCost,
    MeridianEvidenceSink,
    StaticUsageProvider,
)
from meridian_storage.plugins.usage import UsageScope


@pytest.mark.integration
def test_calculation_persists_exact_records_and_replays(
    repository,
    published_card,
    executor: MemoryExecutor,
) -> None:
    evidence = MeridianEvidenceSink(executor)
    calculator = CostCalculator(
        StaticUsageProvider(make_usage_input()),
        repository,
        evidence=evidence,
    )
    result = calculator.calculate(
        scope={"tenant": "acme"},
        start=START,
        end=END,
        rate_card=published_card.ref,
        occurred_at=END,
    )
    assert result.replayed is False
    assert result.calculation.final_amount == Decimal("2.50")
    assert result.records[0].final_amount == Decimal("2.50")
    assert result.records[0].lineage["usageFingerprint"] == result.calculation.usage_fingerprint
    assert (
        repository.get_calculation(
            result.calculation.calculation_id,
            result.calculation.revision,
        )
        == result.calculation
    )
    assert repository.get_cost_record(result.records[0].cost_id) == result.records[0]
    assert executor.transactions == 1
    assert len(executor.evidence) == 2

    replay = calculator.calculate(
        scope=UsageScope({"tenant": "acme"}),
        start=START,
        end=END,
        rate_card=published_card,
    )
    assert replay.replayed is True
    assert replay.calculation == result.calculation
    assert replay.records == result.records
    assert executor.transactions == 1
    assert len(executor.evidence) == 4


def test_changed_usage_creates_superseding_calculation_revision(
    repository,
    published_card,
) -> None:
    first = CostCalculator(StaticUsageProvider(make_usage_input()), repository).calculate(
        scope={"tenant": "acme"},
        start=START,
        end=END,
        rate_card=published_card.ref,
    )
    changed_input = make_usage_input(
        make_aggregate(
            aggregate_id="agg-002",
            total=Decimal("2000"),
            source_fingerprint=make_fingerprint("changed-usage"),
        )
    )
    second = CostCalculator(StaticUsageProvider(changed_input), repository).calculate(
        scope={"tenant": "acme"},
        start=START,
        end=END,
        rate_card=published_card.ref,
    )
    assert second.calculation.calculation_id == first.calculation.calculation_id
    assert second.calculation.revision == 2
    assert second.calculation.supersedes == first.calculation.version_id
    assert second.records[0].supersedes == first.records[0].cost_id
    assert second.calculation.final_amount == Decimal("5.00")


def test_weighted_allocation_preserves_calculation_total(repository, published_card) -> None:
    source = make_fingerprint("finops-allocation")
    allocation = AllocationSpecV1(
        ("team",),
        (
            AllocationWeightV1({"team": "alpha"}, Decimal(2), source),
            AllocationWeightV1({"team": "beta"}, Decimal(1), source),
        ),
        "finops.teams.v1",
    )
    result = CostCalculator(StaticUsageProvider(make_usage_input()), repository).calculate(
        scope={"tenant": "acme"},
        start=START,
        end=END,
        rate_card=published_card.ref,
        allocation=allocation,
    )
    assert len(result.records) == 2
    assert sum((item.final_amount for item in result.records), Decimal(0)) == Decimal("2.50")
    assert {item.allocation_dimensions["team"] for item in result.records} == {
        "alpha",
        "beta",
    }
    assert CalculationV1.from_mapping(result.calculation.to_dict()) == result.calculation
    assert all(CostRecordV1.from_mapping(item.to_dict()) == item for item in result.records)


def test_calculator_fails_closed_for_bad_inputs(repository, published_card) -> None:
    calculator = CostCalculator(StaticUsageProvider(make_usage_input()), repository)
    with pytest.raises(InvalidCost, match="precede"):
        calculator.calculate(
            scope={"tenant": "acme"},
            start=START,
            end=END,
            rate_card=published_card.ref,
            occurred_at=START,
        )
    with pytest.raises(InvalidCost, match="does not match"):
        calculator.calculate(
            scope={"tenant": "other"},
            start=START,
            end=END,
            rate_card=published_card.ref,
        )
    with pytest.raises(TypeError):
        calculator.calculate(
            scope={"tenant": "acme"},
            start=START,
            end=END,
            rate_card=object(),  # type: ignore[arg-type]
        )


def test_calculator_version_is_part_of_identity(repository, published_card) -> None:
    first = CostCalculator(
        StaticUsageProvider(make_usage_input()),
        repository,
        calculator_version="calculator.v1",
    ).calculate(scope={"tenant": "acme"}, start=START, end=END, rate_card=published_card.ref)
    second = CostCalculator(
        StaticUsageProvider(make_usage_input()),
        repository,
        calculator_version="calculator.v2",
    ).calculate(scope={"tenant": "acme"}, start=START, end=END, rate_card=published_card.ref)
    assert first.calculation.calculation_identity != second.calculation.calculation_identity
    assert second.calculation.revision == 2
