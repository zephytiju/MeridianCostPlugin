# SPDX-License-Identifier: Apache-2.0
"""Deterministic allocation tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import make_fingerprint
from meridian_storage.plugins.cost import (
    AllocationSpecV1,
    AllocationWeightV1,
    InvalidCost,
    allocate_amount,
)


def _spec() -> AllocationSpecV1:
    source = make_fingerprint("allocation-source")
    return AllocationSpecV1(
        ("team",),
        (
            AllocationWeightV1({"team": "alpha"}, Decimal(1), source),
            AllocationWeightV1({"team": "beta"}, Decimal(1), source),
            AllocationWeightV1({"team": "gamma"}, Decimal(1), source),
        ),
        "teams.v1",
    )


def test_largest_remainder_is_exact_and_order_independent() -> None:
    spec = _spec()
    values = allocate_amount(Decimal("10.00"), spec, scale=2)
    assert sum((item.amount for item in values), Decimal(0)) == Decimal("10.00")
    assert sorted(item.amount for item in values) == [
        Decimal("3.33"),
        Decimal("3.33"),
        Decimal("3.34"),
    ]
    reversed_spec = AllocationSpecV1(spec.dimensions, tuple(reversed(spec.weights)), spec.name)
    assert allocate_amount(Decimal("10.00"), reversed_spec, scale=2) == values
    assert len({item.key_fingerprint: item for item in values}) == 3


def test_allocation_preserves_negative_and_fractional_values() -> None:
    values = allocate_amount(Decimal("-0.01"), _spec(), scale=2)
    assert sum((item.amount for item in values), Decimal(0)) == Decimal("-0.01")
    assert AllocationSpecV1.unallocated().weights[0].dimensions == {}


def test_allocation_validates_shape_and_fingerprints() -> None:
    source = make_fingerprint("source")
    with pytest.raises(InvalidCost, match="positive total"):
        AllocationSpecV1(
            ("team",),
            (AllocationWeightV1({"team": "a"}, Decimal(0), source),),
        )
    with pytest.raises(InvalidCost, match="exactly"):
        AllocationSpecV1(
            ("team",),
            (AllocationWeightV1({}, Decimal(1), source),),
        )
    with pytest.raises(InvalidCost, match="unique"):
        AllocationSpecV1(
            ("team",),
            (
                AllocationWeightV1({"team": "a"}, Decimal(1), source),
                AllocationWeightV1({"team": "a"}, Decimal(1), source),
            ),
        )
    with pytest.raises(InvalidCost, match="between"):
        allocate_amount(Decimal(1), AllocationSpecV1.unallocated(), scale=19)
