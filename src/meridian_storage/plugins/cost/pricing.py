# SPDX-License-Identifier: Apache-2.0
"""Closed, typed, deterministic Cost V1 pricing algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from ._canonical import decimal_text, decimal_value, fingerprint, fit_decimal
from .errors import InvalidCost, InvalidTier
from .models import CostAdjustmentV1, PricingModel, RateCardV1


@dataclass(frozen=True, slots=True)
class TierChargeV1:
    tier: int
    start: Decimal
    end: Decimal | None
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "start": decimal_text(self.start),
            "end": None if self.end is None else decimal_text(self.end),
            "quantity": decimal_text(self.quantity),
            "unitPrice": decimal_text(self.unit_price),
            "amount": decimal_text(self.amount),
        }


@dataclass(frozen=True, slots=True)
class PriceResultV1:
    model: PricingModel
    quantity: Decimal
    currency: str
    components: tuple[TierChargeV1, ...]
    intermediate_amount: Decimal
    pre_adjustment_amount: Decimal
    adjustments: tuple[CostAdjustmentV1, ...]
    final_amount: Decimal
    rate_card_fingerprint: str

    def __post_init__(self) -> None:
        adjustment_total = sum((item.amount for item in self.adjustments), Decimal(0))
        if self.pre_adjustment_amount + adjustment_total != self.final_amount:
            raise InvalidCost("price result totals must be exact")
        if self.final_amount < 0:
            raise InvalidCost("price result cannot be negative")

    @property
    def adjustment_total(self) -> Decimal:
        return sum((item.amount for item in self.adjustments), Decimal(0))

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model.value,
            "quantity": decimal_text(self.quantity),
            "currency": self.currency,
            "components": [item.to_dict() for item in self.components],
            "intermediateAmount": decimal_text(self.intermediate_amount),
            "preAdjustmentAmount": decimal_text(self.pre_adjustment_amount),
            "adjustments": [item.to_dict() for item in self.adjustments],
            "adjustmentTotal": decimal_text(self.adjustment_total),
            "finalAmount": decimal_text(self.final_amount),
            "rateCardFingerprint": self.rate_card_fingerprint,
        }


def _multiply(quantity: Decimal, unit_price: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 100
        return fit_decimal(quantity * unit_price, "price multiplication")


def _flat_component(card: RateCardV1, quantity: Decimal) -> tuple[TierChargeV1, ...]:
    if card.unit_price is None:
        raise AssertionError("validated flat pricing has no unit price")
    amount = card.rounding.intermediate(_multiply(quantity, card.unit_price))
    return (TierChargeV1(0, Decimal(0), None, quantity, card.unit_price, amount),)


def _volume_component(card: RateCardV1, quantity: Decimal) -> tuple[TierChargeV1, ...]:
    tier = next((item for item in card.tiers if item.contains(quantity)), None)
    if tier is None:
        raise InvalidTier("volume quantity is not covered by the rate-card tiers")
    amount = card.rounding.intermediate(_multiply(quantity, tier.unit_price))
    return (
        TierChargeV1(
            card.tiers.index(tier), tier.start, tier.end, quantity, tier.unit_price, amount
        ),
    )


def _graduated_components(card: RateCardV1, quantity: Decimal) -> tuple[TierChargeV1, ...]:
    components: list[TierChargeV1] = []
    for index, tier in enumerate(card.tiers):
        if quantity <= tier.start:
            break
        upper = quantity if tier.end is None else min(quantity, tier.end)
        consumed = upper - tier.start
        if consumed <= 0:
            continue
        amount = card.rounding.intermediate(_multiply(consumed, tier.unit_price))
        components.append(
            TierChargeV1(index, tier.start, tier.end, consumed, tier.unit_price, amount)
        )
    if quantity > 0 and not components:
        raise InvalidTier("graduated quantity is not covered by the rate-card tiers")
    return tuple(components)


def calculate_price(card: RateCardV1, quantity: Decimal) -> PriceResultV1:
    """Apply one closed pricing model without floating point or stored code."""

    if not isinstance(card, RateCardV1):
        raise TypeError("card must be RateCardV1")
    selected_quantity = decimal_value(quantity, "quantity")
    if selected_quantity < 0:
        raise InvalidCost("pricing quantity cannot be negative")

    if card.pricing_model in {
        PricingModel.FLAT_UNIT,
        PricingModel.MINIMUM_CHARGE,
        PricingModel.COMMITMENT_CREDIT,
    }:
        components = _flat_component(card, selected_quantity)
    elif card.pricing_model is PricingModel.VOLUME_TIER:
        components = _volume_component(card, selected_quantity)
    else:
        components = _graduated_components(card, selected_quantity)

    intermediate = fit_decimal(
        sum((item.amount for item in components), Decimal(0)), "intermediate amount"
    )
    pre_adjustment = card.rounding.final(intermediate)
    desired = pre_adjustment
    adjustment_code: str | None = None
    adjustment_reason: str | None = None

    if card.pricing_model is PricingModel.MINIMUM_CHARGE:
        if card.minimum_charge is None:
            raise AssertionError("validated minimum pricing has no minimum charge")
        desired = max(pre_adjustment, card.rounding.final(card.minimum_charge))
        adjustment_code = "minimum_charge"
        adjustment_reason = "minimum-charge"
    elif card.pricing_model is PricingModel.COMMITMENT_CREDIT:
        if card.commitment_credit is None:
            raise AssertionError("validated commitment pricing has no credit")
        desired = card.rounding.final(max(intermediate - card.commitment_credit.amount, Decimal(0)))
        adjustment_code = "commitment_credit"
        adjustment_reason = "commitment-credit"

    adjustments: tuple[CostAdjustmentV1, ...] = ()
    difference = fit_decimal(desired - pre_adjustment, "pricing adjustment")
    if difference:
        adjustments = (
            CostAdjustmentV1(
                adjustment_code or "currency_rounding",
                difference,
                adjustment_reason or "currency-rounding",
                card.pricing_fingerprint,
            ),
        )
    return PriceResultV1(
        model=card.pricing_model,
        quantity=selected_quantity,
        currency=card.currency,
        components=components,
        intermediate_amount=intermediate,
        pre_adjustment_amount=pre_adjustment,
        adjustments=adjustments,
        final_amount=desired,
        rate_card_fingerprint=card.pricing_fingerprint,
    )


__all__ = ["PriceResultV1", "TierChargeV1", "calculate_price"]
