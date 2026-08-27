# SPDX-License-Identifier: Apache-2.0
"""Exact pricing conformance tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import make_fingerprint, make_rate_card
from meridian_storage.plugins.cost import (
    CommitmentCredit,
    InvalidCost,
    PricingModel,
    PricingTier,
    calculate_price,
)


def test_flat_and_minimum_charge_pricing() -> None:
    flat = calculate_price(make_rate_card(unit_price=Decimal("0.0025")), Decimal(1000))
    assert flat.pre_adjustment_amount == Decimal("2.500000000000")
    assert flat.adjustment_total == Decimal(0)
    assert flat.final_amount == Decimal("2.50")
    minimum = calculate_price(
        make_rate_card(
            pricing_model=PricingModel.MINIMUM_CHARGE,
            unit_price=Decimal("0.01"),
            minimum_charge=Decimal("25"),
        ),
        Decimal(1000),
    )
    assert minimum.pre_adjustment_amount == Decimal("10.000000000000")
    assert minimum.adjustment_total == Decimal("15.000000000000")
    assert minimum.final_amount == Decimal("25.00")
    assert minimum.adjustments[0].code == "minimum_charge"


def test_volume_tier_boundary_uses_one_rate() -> None:
    card = make_rate_card(
        pricing_model=PricingModel.VOLUME_TIER,
        unit_price=None,
        tiers=(
            PricingTier(Decimal(0), Decimal(100), Decimal("0.10")),
            PricingTier(Decimal(100), None, Decimal("0.05")),
        ),
    )
    below = calculate_price(card, Decimal("99.999"))
    boundary = calculate_price(card, Decimal(100))
    assert below.components[0].tier == 0
    assert boundary.components[0].tier == 1
    assert boundary.final_amount == Decimal("5.00")


def test_graduated_tiers_charge_only_each_slice() -> None:
    card = make_rate_card(
        pricing_model=PricingModel.GRADUATED_TIER,
        unit_price=None,
        tiers=(
            PricingTier(Decimal(0), Decimal(100), Decimal("0.10")),
            PricingTier(Decimal(100), Decimal(200), Decimal("0.05")),
            PricingTier(Decimal(200), None, Decimal("0.01")),
        ),
    )
    result = calculate_price(card, Decimal(250))
    assert tuple(item.quantity for item in result.components) == (
        Decimal(100),
        Decimal(100),
        Decimal(50),
    )
    assert result.final_amount == Decimal("15.50")
    assert result.fingerprint.startswith("sha256:")


def test_commitment_credit_is_capped_at_zero() -> None:
    card = make_rate_card(
        pricing_model=PricingModel.COMMITMENT_CREDIT,
        unit_price=Decimal("0.01"),
        commitment_credit=CommitmentCredit(
            Decimal(25),
            "contract-a",
            make_fingerprint("contract-a"),
        ),
    )
    result = calculate_price(card, Decimal(1000))
    assert result.pre_adjustment_amount == Decimal("10.000000000000")
    assert result.adjustments[0].amount == Decimal("-10.000000000000")
    assert result.final_amount == Decimal("0.00")


def test_price_rejects_negative_quantity_and_non_card() -> None:
    with pytest.raises(InvalidCost, match="negative"):
        calculate_price(make_rate_card(), Decimal(-1))
    with pytest.raises(TypeError):
        calculate_price(object(), Decimal(1))  # type: ignore[arg-type]
