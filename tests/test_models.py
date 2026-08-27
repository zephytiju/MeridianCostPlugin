# SPDX-License-Identifier: Apache-2.0
"""Immutable model and canonicalization tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conftest import END, NOW, START, make_aggregate, make_fingerprint, make_rate_card
from meridian_storage.plugins.cost import (
    CommitmentCredit,
    InvalidCost,
    InvalidTier,
    PricingModel,
    PricingTier,
    PublicationState,
    RateCardRef,
    RateCardV1,
    RoundingMode,
    RoundingPolicy,
    UnclosedUsage,
    UsageInputV1,
)
from meridian_storage.plugins.usage import UsageScope, UsageWindow


def test_rounding_policy_is_explicit_and_exact() -> None:
    policy = RoundingPolicy(2, 6, RoundingMode.HALF_UP, RoundingMode.HALF_EVEN)
    assert policy.intermediate(Decimal("1.2345678")) == Decimal("1.234568")
    assert policy.final(Decimal("1.225")) == Decimal("1.22")
    assert policy.quantum == Decimal("0.01")
    assert RoundingPolicy.from_mapping(policy.to_dict()) == policy
    with pytest.raises(InvalidCost, match="intermediate_scale"):
        RoundingPolicy(4, 2)
    with pytest.raises(InvalidCost, match="between"):
        RoundingPolicy(currency_scale=True)  # type: ignore[arg-type]


def test_rate_card_flat_round_trip_and_immutable_fields() -> None:
    card = make_rate_card(matching_dimensions={"region": "us-west"})
    restored = RateCardV1.from_mapping(card.to_dict())
    assert restored == card
    assert restored.fingerprint == card.fingerprint
    assert restored.ref == RateCardRef(card.rate_card_id, 1, card.pricing_fingerprint)
    assert restored.pricing_key.startswith("sha256:")
    assert restored.covers(UsageWindow(START, END))
    assert restored.matches_dimensions({"region": "us-west", "zone": "a"})
    assert not restored.matches_dimensions({"region": "eu"})
    with pytest.raises(TypeError):
        restored.provenance["source"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        restored.currency = "EUR"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("model", "changes"),
    [
        (
            PricingModel.VOLUME_TIER,
            {
                "unit_price": None,
                "tiers": (
                    PricingTier(Decimal(0), Decimal(100), Decimal("0.1")),
                    PricingTier(Decimal(100), None, Decimal("0.05")),
                ),
            },
        ),
        (
            PricingModel.GRADUATED_TIER,
            {
                "unit_price": None,
                "tiers": (
                    PricingTier(Decimal(0), Decimal(100), Decimal("0.1")),
                    PricingTier(Decimal(100), None, Decimal("0.05")),
                ),
            },
        ),
        (
            PricingModel.MINIMUM_CHARGE,
            {"minimum_charge": Decimal("10")},
        ),
        (
            PricingModel.COMMITMENT_CREDIT,
            {
                "commitment_credit": CommitmentCredit(
                    Decimal("5"),
                    "contract-123",
                    make_fingerprint("contract"),
                )
            },
        ),
    ],
)
def test_every_pricing_model_has_a_serialized_shape(
    model: PricingModel,
    changes: dict[str, object],
) -> None:
    card = make_rate_card(pricing_model=model, **changes)
    assert RateCardV1.from_mapping(card.to_dict()) == card


def test_tiers_are_contiguous_half_open_and_unbounded() -> None:
    first = PricingTier(Decimal(0), Decimal(10), Decimal(1))
    assert first.contains(Decimal("9.999"))
    assert not first.contains(Decimal(10))
    with pytest.raises(InvalidTier, match="half-open"):
        PricingTier(Decimal(1), Decimal(1), Decimal(1))
    with pytest.raises(InvalidTier, match="contiguous"):
        make_rate_card(
            pricing_model=PricingModel.VOLUME_TIER,
            unit_price=None,
            tiers=(first, PricingTier(Decimal(11), None, Decimal(1))),
        )
    with pytest.raises(InvalidTier, match="unbounded"):
        make_rate_card(
            pricing_model=PricingModel.GRADUATED_TIER,
            unit_price=None,
            tiers=(PricingTier(Decimal(0), Decimal(10), Decimal(1)),),
        )


def test_rate_card_rejects_invalid_shapes_and_intervals() -> None:
    with pytest.raises(InvalidCost, match="pricing fields"):
        make_rate_card(unit_price=None)
    with pytest.raises(InvalidCost, match="half-open"):
        make_rate_card(effective_end=START)
    with pytest.raises(InvalidCost, match="provenance"):
        make_rate_card(provenance={})
    with pytest.raises(InvalidCost, match="published_at"):
        make_rate_card(state=PublicationState.PUBLISHED)
    with pytest.raises(InvalidCost, match="cannot be negative"):
        make_rate_card(unit_price=Decimal("-0.1"))


def test_rate_card_lifecycle_snapshots_preserve_pricing() -> None:
    draft = make_rate_card()
    validated = draft.transition(PublicationState.VALIDATED, at=NOW)
    published = validated.transition(PublicationState.PUBLISHED, at=NOW)
    retired = published.transition(
        PublicationState.RETIRED,
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert (draft.revision, validated.revision, published.revision, retired.revision) == (
        1,
        2,
        3,
        4,
    )
    assert published.published_at == NOW == retired.published_at
    assert len({item.pricing_fingerprint for item in (draft, validated, published, retired)}) == 1
    with pytest.raises(InvalidCost, match="not permitted"):
        draft.transition(PublicationState.PUBLISHED, at=NOW)


def test_usage_input_is_closed_versioned_and_exact() -> None:
    first = make_aggregate(total=Decimal("1.25"))
    second = make_aggregate(
        aggregate_id="agg-002",
        total=Decimal("2.75"),
        source_fingerprint=make_fingerprint("source-2"),
    )
    value = UsageInputV1(
        first.scope,
        UsageWindow(START, END),
        first.meter_id,
        first.meter_version,
        "request",
        (second, first),
    )
    assert value.quantity == Decimal("4")
    assert value.aggregate_refs == ("agg-001@1", "agg-002@1")
    assert (
        value.fingerprint
        == UsageInputV1(
            value.scope,
            value.window,
            value.meter_id,
            value.meter_version,
            value.unit,
            tuple(reversed(value.aggregates)),
        ).fingerprint
    )
    with pytest.raises(UnclosedUsage):
        replace(value, aggregates=())
    with pytest.raises(UnclosedUsage):
        UsageInputV1(
            first.scope,
            UsageWindow(START, END),
            first.meter_id,
            first.meter_version,
            "request",
            (replace(first, watermark=START),),
        )
    with pytest.raises(InvalidCost, match="scope and meter"):
        UsageInputV1(
            UsageScope({"tenant": "other"}),
            UsageWindow(START, END),
            first.meter_id,
            1,
            "request",
            (first,),
        )


def test_float_money_is_rejected() -> None:
    with pytest.raises(InvalidCost, match="exact decimal"):
        make_rate_card(unit_price=0.1)
