# SPDX-License-Identifier: Apache-2.0
"""Released meridian-plugin-usage public-interface integration tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from conftest import END, START, MemoryExecutor, make_aggregate, make_fingerprint, make_usage_input
from meridian_storage.plugins.cost import (
    InvalidCost,
    RepositoryUsageProvider,
    StaticUsageProvider,
    UsageDependencyFailure,
)
from meridian_storage.plugins.usage import MeterV1, UsageRepository, UsageScope, UsageWindow


@pytest.mark.integration
def test_repository_provider_reads_released_usage_api_only(
    executor: MemoryExecutor,
    published_card,
) -> None:
    usage = UsageRepository(executor)
    meter = MeterV1("api.requests", 1, "count", "request")
    executor.seed(usage.resources.meters, meter.to_dict())
    first = make_aggregate(total=Decimal(1000))
    second = replace(
        first,
        revision=2,
        total=Decimal(1250),
        source_fingerprint=make_fingerprint("usage-revision-2"),
        supersedes=first.version_id,
    )
    executor.seed(usage.resources.aggregates, first.to_dict())
    executor.seed(usage.resources.aggregates, second.to_dict())
    executor.expressions.clear()
    result = RepositoryUsageProvider(usage, page_size=1).fetch(
        published_card,
        UsageScope({"tenant": "acme"}),
        UsageWindow(START, END),
    )
    assert result.quantity == Decimal(1250)
    assert result.aggregate_refs == (second.version_id,)
    touched = {expression.arguments["resource"]["namespace"] for expression in executor.expressions}
    assert touched == {"usage"}


def test_repository_provider_wraps_usage_public_errors(
    executor: MemoryExecutor,
    published_card,
) -> None:
    with pytest.raises(UsageDependencyFailure, match="Usage public API failed"):
        RepositoryUsageProvider(UsageRepository(executor)).fetch(
            published_card,
            UsageScope({"tenant": "acme"}),
            UsageWindow(START, END),
        )


def test_static_provider_requires_exact_normalized_request(published_card) -> None:
    provider = StaticUsageProvider(make_usage_input())
    assert provider.fetch(
        published_card,
        UsageScope({"tenant": "acme"}),
        UsageWindow(START, END),
    ).quantity == Decimal(1000)
    with pytest.raises(InvalidCost, match="does not match"):
        provider.fetch(
            published_card,
            UsageScope({"tenant": "other"}),
            UsageWindow(START, END),
        )
    with pytest.raises(TypeError):
        StaticUsageProvider(object())  # type: ignore[arg-type]
