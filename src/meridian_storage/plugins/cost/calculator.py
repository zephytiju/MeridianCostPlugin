# SPDX-License-Identifier: Apache-2.0
"""Versioned, idempotent Cost calculation over closed public Usage inputs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from meridian_storage.plugins.usage import UsageRepository, UsageScope, UsageWindow

from ._canonical import fingerprint, require_fingerprint, utc_datetime
from .allocation import AllocationSpecV1, allocate_amount, allocation_map
from .errors import InvalidCost, MissingRateCard, UnitMismatch
from .evidence import CostEvidenceSink
from .models import (
    CalculationV1,
    CostAdjustmentV1,
    CostCalculationResult,
    CostRecordV1,
    PublicationState,
    RateCardRef,
    RateCardV1,
    UsageInputV1,
)
from .pricing import PriceResultV1, calculate_price
from .repository import CostRepository
from .usage import UsageInputProvider, usage_provider

_CALCULATOR_VERSION = "cost-calculator.v1"


def _token_from_fingerprint(prefix: str, value: str) -> str:
    selected = require_fingerprint(value, "identity fingerprint")
    return f"{prefix}-{selected.removeprefix('sha256:')[:40]}"


class CostCalculator:
    """Pure calculation orchestration plus immutable Cost persistence."""

    def __init__(
        self,
        usage: UsageInputProvider | UsageRepository,
        cost: CostRepository,
        *,
        evidence: CostEvidenceSink | None = None,
        calculator_version: str = _CALCULATOR_VERSION,
    ) -> None:
        self.usage = usage_provider(usage)
        if not isinstance(cost, CostRepository):
            raise TypeError("cost must be CostRepository")
        self.cost = cost
        if evidence is not None and not callable(getattr(evidence, "emit", None)):
            raise TypeError("evidence must implement CostEvidenceSink")
        self.evidence = evidence
        self.calculator_version = calculator_version

    def _card(self, value: RateCardRef | RateCardV1, window: UsageWindow) -> RateCardV1:
        if isinstance(value, RateCardV1):
            card = self.cost.get_rate_card(value.rate_card_id, value.version)
            if card.pricing_fingerprint != value.pricing_fingerprint:
                raise InvalidCost(
                    "supplied rate card differs from the latest persisted lifecycle snapshot",
                    requirement="cost.rate-card.exact",
                )
        elif isinstance(value, RateCardRef):
            card = self.cost.get_rate_card(value.rate_card_id, value.version)
            if card.pricing_fingerprint != value.pricing_fingerprint:
                raise MissingRateCard(value.identity)
        else:
            raise TypeError("rate_card must be RateCardRef or RateCardV1")
        if card.state is not PublicationState.PUBLISHED:
            raise InvalidCost(
                "only a currently published rate card can price production Usage",
                requirement="cost.rate-card.published",
            )
        if not card.covers(window):
            raise InvalidCost(
                "rate card does not cover the complete calculation interval",
                requirement="cost.rate-card.coverage",
            )
        return card

    def calculate(
        self,
        *,
        scope: UsageScope | Mapping[object, object],
        start: datetime,
        end: datetime,
        rate_card: RateCardRef | RateCardV1,
        allocation: AllocationSpecV1 | None = None,
        occurred_at: datetime | None = None,
    ) -> CostCalculationResult:
        selected_scope = UsageScope.parse(scope)
        window = UsageWindow(
            utc_datetime(start, "calculation start"),
            utc_datetime(end, "calculation end"),
        )
        card = self._card(rate_card, window)
        usage_input = self.usage.fetch(card, selected_scope, window)
        if usage_input.meter_id != card.meter_id or usage_input.meter_version != card.meter_version:
            raise UnitMismatch(usage_input.unit, f"{card.meter_id}@{card.meter_version}")
        selected_allocation = allocation or AllocationSpecV1.unallocated()
        if not isinstance(selected_allocation, AllocationSpecV1):
            raise TypeError("allocation must be AllocationSpecV1")
        selected_occurred_at = utc_datetime(occurred_at or window.end, "occurred_at")
        if selected_occurred_at < window.end:
            raise InvalidCost("occurred_at cannot precede the closed calculation interval")

        series_fingerprint = fingerprint(
            {
                "scopeFingerprint": selected_scope.fingerprint,
                "window": window.to_dict(),
                "rateCardId": card.rate_card_id,
                "allocationName": selected_allocation.name,
                "allocationDimensions": list(selected_allocation.dimensions),
            }
        )
        calculation_id = _token_from_fingerprint("calc", series_fingerprint)
        calculation_identity = fingerprint(
            {
                "usageFingerprint": usage_input.fingerprint,
                "rateCardFingerprint": card.pricing_fingerprint,
                "window": window.to_dict(),
                "allocationFingerprint": selected_allocation.fingerprint,
                "calculatorVersion": self.calculator_version,
            }
        )
        existing = self.cost.find_calculation(calculation_identity)
        if existing is not None:
            records = self.cost.records_for_calculation(existing.calculation_id, existing.revision)
            result = CostCalculationResult(existing, records, True)
            if self.evidence is not None:
                self.evidence.emit(existing, records)
            return result

        price = calculate_price(card, usage_input.quantity)
        previous = self.cost.latest_calculation(calculation_id)
        revision = 1 if previous is None else previous.revision + 1
        previous_records = (
            ()
            if previous is None
            else self.cost.records_for_calculation(previous.calculation_id, previous.revision)
        )
        previous_by_allocation = {item.allocation_fingerprint: item for item in previous_records}
        records = self._records(
            calculation_id,
            revision,
            usage_input,
            card,
            price,
            selected_allocation,
            selected_occurred_at,
            previous_by_allocation,
        )
        explanation = {
            "formatVersion": "meridian.cost.explanation.v1",
            "inputs": usage_input.to_dict(),
            "rateCard": {
                **card.ref.to_dict(),
                "pricingKey": card.pricing_key,
                "rounding": card.rounding.to_dict(),
            },
            "pricing": price.to_dict(),
            "allocation": selected_allocation.to_dict(),
            "outputs": {
                "recordIds": [item.cost_id for item in records],
                "finalAmount": str(price.final_amount),
            },
        }
        calculation = CalculationV1(
            calculation_id=calculation_id,
            revision=revision,
            calculation_identity=calculation_identity,
            scope=selected_scope,
            window=window,
            usage_fingerprint=usage_input.fingerprint,
            aggregate_refs=usage_input.aggregate_refs,
            rate_card=card.ref,
            allocation_fingerprint=selected_allocation.fingerprint,
            calculator_version=self.calculator_version,
            quantity=usage_input.quantity,
            unit=usage_input.unit,
            currency=card.currency,
            pre_adjustment_amount=price.pre_adjustment_amount,
            adjustment_total=price.adjustment_total,
            final_amount=price.final_amount,
            explanation=explanation,
            created_at=selected_occurred_at,
            record_ids=tuple(sorted(item.cost_id for item in records)),
            supersedes=None if previous is None else previous.version_id,
        )
        persisted, persisted_records = self.cost.persist_calculation(calculation, records)
        if self.evidence is not None:
            self.evidence.emit(persisted, persisted_records)
        return CostCalculationResult(persisted, persisted_records)

    def _records(
        self,
        calculation_id: str,
        revision: int,
        usage_input: UsageInputV1,
        card: RateCardV1,
        price: PriceResultV1,
        allocation: AllocationSpecV1,
        occurred_at: datetime,
        previous: Mapping[str, CostRecordV1],
    ) -> tuple[CostRecordV1, ...]:
        final_shares = allocation_map(
            allocate_amount(price.final_amount, allocation, scale=card.rounding.currency_scale)
        )
        pre_shares = allocation_map(
            allocate_amount(
                price.pre_adjustment_amount,
                allocation,
                scale=card.rounding.currency_scale,
            )
        )
        quantity_shares = allocation_map(
            allocate_amount(usage_input.quantity, allocation, scale=18)
        )
        records: list[CostRecordV1] = []
        for entry in allocation.weights:
            key = entry.key_fingerprint
            record_allocation_fingerprint = fingerprint(
                {"spec": allocation.fingerprint, "key": key}
            )
            pre = pre_shares[key].amount
            final = final_shares[key].amount
            difference = final - pre
            adjustments: tuple[CostAdjustmentV1, ...]
            if len(allocation.weights) == 1:
                adjustments = price.adjustments
            elif difference:
                adjustments = (
                    CostAdjustmentV1(
                        "allocated_adjustments",
                        difference,
                        "pricing-adjustments",
                        price.fingerprint,
                    ),
                )
            else:
                adjustments = ()
            cost_identity = fingerprint(
                {
                    "calculation": f"{calculation_id}@{revision}",
                    "allocation": record_allocation_fingerprint,
                }
            )
            cost_id = _token_from_fingerprint("cost", cost_identity)
            prior = previous.get(record_allocation_fingerprint)
            records.append(
                CostRecordV1(
                    cost_id=cost_id,
                    calculation_id=calculation_id,
                    calculation_revision=revision,
                    scope=usage_input.scope,
                    window=usage_input.window,
                    usage_fingerprint=usage_input.fingerprint,
                    aggregate_refs=usage_input.aggregate_refs,
                    rate_card=card.ref,
                    quantity=quantity_shares[key].amount,
                    unit=usage_input.unit,
                    currency=card.currency,
                    pre_adjustment_amount=pre,
                    adjustments=adjustments,
                    final_amount=final,
                    allocation_dimensions=entry.dimensions,
                    allocation_fingerprint=record_allocation_fingerprint,
                    occurred_at=occurred_at,
                    supersedes=None if prior is None else prior.cost_id,
                    lineage={
                        "usageFingerprint": usage_input.fingerprint,
                        "aggregateRefs": list(usage_input.aggregate_refs),
                        "rateCardFingerprint": card.pricing_fingerprint,
                        "calculatorVersion": self.calculator_version,
                    },
                )
            )
        return tuple(sorted(records, key=lambda item: item.cost_id))


__all__ = ["CostCalculator"]
