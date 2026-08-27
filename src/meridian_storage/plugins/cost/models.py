# SPDX-License-Identifier: Apache-2.0
"""Immutable generic rate-card, usage-input, calculation, and cost models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    localcontext,
)
from enum import StrEnum
from itertools import pairwise
from typing import cast

from meridian_storage.plugins.usage import UsageAggregateV1, UsageScope, UsageWindow

from ._canonical import (
    currency_code,
    decimal_text,
    decimal_value,
    fingerprint,
    fit_decimal,
    immutable_json_mapping,
    iso_datetime,
    logical_name,
    parse_datetime,
    require_fingerprint,
    string_map,
    token,
    utc_datetime,
)
from .errors import InvalidCost, InvalidTier, UnclosedUsage

_SCHEMA_VERSION = "1.0.0"


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidCost(f"{name} must be a positive integer")
    return value


class PublicationState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    RETIRED = "retired"


class PricingModel(StrEnum):
    FLAT_UNIT = "flat_unit"
    VOLUME_TIER = "volume_tier"
    GRADUATED_TIER = "graduated_tier"
    MINIMUM_CHARGE = "minimum_charge"
    COMMITMENT_CREDIT = "commitment_credit"


class RoundingMode(StrEnum):
    HALF_EVEN = "half_even"
    HALF_UP = "half_up"
    DOWN = "down"
    UP = "up"

    @property
    def decimal_mode(self) -> str:
        return {
            RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
            RoundingMode.HALF_UP: ROUND_HALF_UP,
            RoundingMode.DOWN: ROUND_DOWN,
            RoundingMode.UP: ROUND_UP,
        }[self]


@dataclass(frozen=True, slots=True)
class RoundingPolicy:
    """Explicit intermediate and final Decimal rounding stages."""

    currency_scale: int = 2
    intermediate_scale: int = 12
    intermediate_mode: RoundingMode = RoundingMode.HALF_EVEN
    final_mode: RoundingMode = RoundingMode.HALF_EVEN

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.currency_scale, "currency_scale", 9),
            (self.intermediate_scale, "intermediate_scale", 18),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise InvalidCost(f"{name} must be between 0 and {maximum}")
        if self.intermediate_scale < self.currency_scale:
            raise InvalidCost("intermediate_scale cannot be less than currency_scale")
        if not isinstance(self.intermediate_mode, RoundingMode) or not isinstance(
            self.final_mode, RoundingMode
        ):
            raise InvalidCost("rounding modes must be closed RoundingMode values")

    @staticmethod
    def _quantize(value: Decimal, scale: int, mode: RoundingMode) -> Decimal:
        selected = decimal_value(value)
        quantum = Decimal(1).scaleb(-scale)
        with localcontext() as context:
            context.prec = 100
            return fit_decimal(selected.quantize(quantum, rounding=mode.decimal_mode))

    def intermediate(self, value: Decimal) -> Decimal:
        return self._quantize(value, self.intermediate_scale, self.intermediate_mode)

    def final(self, value: Decimal) -> Decimal:
        return self._quantize(value, self.currency_scale, self.final_mode)

    @property
    def quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.currency_scale)

    def to_dict(self) -> dict[str, object]:
        return {
            "currencyScale": self.currency_scale,
            "intermediateScale": self.intermediate_scale,
            "intermediateMode": self.intermediate_mode.value,
            "finalMode": self.final_mode.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RoundingPolicy:
        return cls(
            currency_scale=cast(int, value.get("currencyScale", 2)),
            intermediate_scale=cast(int, value.get("intermediateScale", 12)),
            intermediate_mode=RoundingMode(
                cast(str, value.get("intermediateMode", RoundingMode.HALF_EVEN.value))
            ),
            final_mode=RoundingMode(
                cast(str, value.get("finalMode", RoundingMode.HALF_EVEN.value))
            ),
        )


@dataclass(frozen=True, slots=True)
class RateCardRef:
    rate_card_id: str
    version: int
    pricing_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate_card_id", token(self.rate_card_id, "rate_card_id"))
        object.__setattr__(self, "version", _positive_integer(self.version, "rate-card version"))
        object.__setattr__(
            self,
            "pricing_fingerprint",
            require_fingerprint(self.pricing_fingerprint, "pricing_fingerprint"),
        )

    @property
    def identity(self) -> str:
        return f"{self.rate_card_id}@{self.version}"

    def to_dict(self) -> dict[str, object]:
        return {
            "rateCardId": self.rate_card_id,
            "rateCardVersion": self.version,
            "pricingFingerprint": self.pricing_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RateCardRef:
        return cls(
            cast(str, value["rateCardId"]),
            cast(int, value["rateCardVersion"]),
            cast(str, value["pricingFingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class PricingTier:
    start: Decimal
    end: Decimal | None
    unit_price: Decimal

    def __post_init__(self) -> None:
        start = decimal_value(self.start, "tier start")
        end = None if self.end is None else decimal_value(self.end, "tier end")
        unit_price = decimal_value(self.unit_price, "tier unit_price")
        if start < 0 or (end is not None and end <= start):
            raise InvalidTier("tier intervals must be non-negative, non-empty, and half-open")
        if unit_price < 0:
            raise InvalidTier("tier unit prices cannot be negative")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "unit_price", unit_price)

    def contains(self, quantity: Decimal) -> bool:
        selected = decimal_value(quantity, "quantity")
        return self.start <= selected and (self.end is None or selected < self.end)

    def to_dict(self) -> dict[str, object]:
        return {
            "start": decimal_text(self.start),
            "end": None if self.end is None else decimal_text(self.end),
            "unitPrice": decimal_text(self.unit_price),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> PricingTier:
        return cls(
            decimal_value(value["start"], "tier start"),
            None if value.get("end") is None else decimal_value(value["end"], "tier end"),
            decimal_value(value["unitPrice"], "tier unit_price"),
        )


@dataclass(frozen=True, slots=True)
class CommitmentCredit:
    amount: Decimal
    reference: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        amount = decimal_value(self.amount, "commitment credit")
        if amount < 0:
            raise InvalidCost("commitment credit cannot be negative")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "reference", token(self.reference, "commitment reference"))
        object.__setattr__(
            self,
            "source_fingerprint",
            require_fingerprint(self.source_fingerprint, "commitment source_fingerprint"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "amount": decimal_text(self.amount),
            "reference": self.reference,
            "sourceFingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CommitmentCredit:
        return cls(
            decimal_value(value["amount"], "commitment credit"),
            cast(str, value["reference"]),
            cast(str, value["sourceFingerprint"]),
        )


def _validate_tiers(values: object) -> tuple[PricingTier, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes | bytearray):
        raise InvalidTier("tiers must be a sequence")
    tiers = tuple(values)
    if not tiers or len(tiers) > 100 or any(not isinstance(item, PricingTier) for item in tiers):
        raise InvalidTier("tiered pricing requires 1 to 100 PricingTier values")
    selected = cast(tuple[PricingTier, ...], tiers)
    if selected[0].start != 0:
        raise InvalidTier("tiers must start at quantity zero")
    for previous, current in pairwise(selected):
        if previous.end is None or previous.end != current.start:
            raise InvalidTier("tiers must be contiguous and strictly ordered")
    if selected[-1].end is not None:
        raise InvalidTier("the final tier must be unbounded")
    return selected


@dataclass(frozen=True, slots=True)
class RateCardV1:
    """One immutable lifecycle snapshot of a versioned pricing contract."""

    rate_card_id: str
    version: int
    revision: int
    provider: str
    product: str
    meter_id: str
    meter_version: int
    currency: str
    effective_start: datetime
    effective_end: datetime
    pricing_model: PricingModel
    created_at: datetime
    unit_price: Decimal | None = None
    tiers: tuple[PricingTier, ...] = ()
    minimum_charge: Decimal | None = None
    commitment_credit: CommitmentCredit | None = None
    matching_dimensions: Mapping[str, str] = field(default_factory=dict)
    rounding: RoundingPolicy = field(default_factory=RoundingPolicy)
    provenance: Mapping[str, str] = field(default_factory=dict)
    state: PublicationState = PublicationState.DRAFT
    published_at: datetime | None = None
    supersedes: RateCardRef | None = None
    schema_version: str = field(default=_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate_card_id", token(self.rate_card_id, "rate_card_id"))
        object.__setattr__(self, "version", _positive_integer(self.version, "rate-card version"))
        object.__setattr__(self, "revision", _positive_integer(self.revision, "rate-card revision"))
        object.__setattr__(self, "provider", logical_name(self.provider, "provider"))
        object.__setattr__(self, "product", logical_name(self.product, "product"))
        object.__setattr__(self, "meter_id", logical_name(self.meter_id, "meter_id"))
        object.__setattr__(
            self, "meter_version", _positive_integer(self.meter_version, "meter version")
        )
        object.__setattr__(self, "currency", currency_code(self.currency))
        start = utc_datetime(self.effective_start, "effective_start")
        end = utc_datetime(self.effective_end, "effective_end")
        if start >= end:
            raise InvalidCost("rate-card effective intervals must be non-empty and half-open")
        object.__setattr__(self, "effective_start", start)
        object.__setattr__(self, "effective_end", end)
        if not isinstance(self.pricing_model, PricingModel):
            raise InvalidCost("pricing_model must be a closed PricingModel value")
        object.__setattr__(self, "created_at", utc_datetime(self.created_at, "created_at"))
        unit_price = (
            None if self.unit_price is None else decimal_value(self.unit_price, "unit_price")
        )
        if unit_price is not None and unit_price < 0:
            raise InvalidCost("unit_price cannot be negative")
        object.__setattr__(self, "unit_price", unit_price)
        tiers = tuple(self.tiers)
        if tiers and any(not isinstance(item, PricingTier) for item in tiers):
            raise InvalidTier("tiers must contain PricingTier values")
        object.__setattr__(self, "tiers", tiers)
        minimum = (
            None
            if self.minimum_charge is None
            else decimal_value(self.minimum_charge, "minimum_charge")
        )
        if minimum is not None and minimum < 0:
            raise InvalidCost("minimum_charge cannot be negative")
        object.__setattr__(self, "minimum_charge", minimum)
        if self.commitment_credit is not None and not isinstance(
            self.commitment_credit, CommitmentCredit
        ):
            raise InvalidCost("commitment_credit must be CommitmentCredit")
        object.__setattr__(
            self,
            "matching_dimensions",
            string_map(self.matching_dimensions, "matching_dimensions", maximum_entries=16),
        )
        if not isinstance(self.rounding, RoundingPolicy):
            raise InvalidCost("rounding must be RoundingPolicy")
        provenance = string_map(self.provenance, "provenance", maximum_entries=16)
        if "source" not in provenance:
            raise InvalidCost("rate-card provenance must contain source")
        object.__setattr__(self, "provenance", provenance)
        self._validate_lifecycle_state()
        if self.supersedes is not None and not isinstance(self.supersedes, RateCardRef):
            raise InvalidCost("supersedes must be a RateCardRef")
        self._validate_pricing_shape()

    def _validate_lifecycle_state(self) -> None:
        if not isinstance(self.state, PublicationState):
            raise InvalidCost("state must be PublicationState")
        published_at = (
            None if self.published_at is None else utc_datetime(self.published_at, "published_at")
        )
        if self.state in {PublicationState.PUBLISHED, PublicationState.RETIRED}:
            if published_at is None:
                raise InvalidCost("published and retired rate cards require published_at")
        elif published_at is not None:
            raise InvalidCost("draft and validated rate cards cannot have published_at")
        object.__setattr__(self, "published_at", published_at)

    def _validate_pricing_shape(self) -> None:
        if self.pricing_model is PricingModel.FLAT_UNIT:
            valid = (
                self.unit_price is not None
                and not self.tiers
                and self.minimum_charge is None
                and self.commitment_credit is None
            )
        elif self.pricing_model in {PricingModel.VOLUME_TIER, PricingModel.GRADUATED_TIER}:
            _validate_tiers(self.tiers)
            valid = (
                self.unit_price is None
                and self.minimum_charge is None
                and self.commitment_credit is None
            )
        elif self.pricing_model is PricingModel.MINIMUM_CHARGE:
            valid = (
                self.unit_price is not None
                and self.minimum_charge is not None
                and not self.tiers
                and self.commitment_credit is None
            )
        else:
            valid = (
                self.unit_price is not None
                and self.commitment_credit is not None
                and not self.tiers
                and self.minimum_charge is None
            )
        if not valid:
            raise InvalidCost(f"pricing fields do not match {self.pricing_model.value}")

    @property
    def identity(self) -> str:
        return f"{self.rate_card_id}@{self.version}"

    @property
    def snapshot_id(self) -> str:
        return f"{self.identity}#{self.revision}"

    @property
    def pricing_key(self) -> str:
        return fingerprint(
            {
                "provider": self.provider,
                "product": self.product,
                "meterId": self.meter_id,
                "meterVersion": self.meter_version,
                "matchingDimensions": dict(self.matching_dimensions),
            }
        )

    def _pricing_dict(self) -> dict[str, object]:
        return {
            "rateCardId": self.rate_card_id,
            "rateCardVersion": self.version,
            "provider": self.provider,
            "product": self.product,
            "meterId": self.meter_id,
            "meterVersion": self.meter_version,
            "currency": self.currency,
            "effectiveStart": iso_datetime(self.effective_start),
            "effectiveEnd": iso_datetime(self.effective_end),
            "pricingModel": self.pricing_model.value,
            "unitPrice": None if self.unit_price is None else decimal_text(self.unit_price),
            "tiers": [item.to_dict() for item in self.tiers],
            "minimumCharge": (
                None if self.minimum_charge is None else decimal_text(self.minimum_charge)
            ),
            "commitmentCredit": (
                None if self.commitment_credit is None else self.commitment_credit.to_dict()
            ),
            "matchingDimensions": dict(self.matching_dimensions),
            "rounding": self.rounding.to_dict(),
            "provenance": dict(self.provenance),
            "supersedes": None if self.supersedes is None else self.supersedes.to_dict(),
        }

    @property
    def pricing_fingerprint(self) -> str:
        return fingerprint(self._pricing_dict())

    @property
    def ref(self) -> RateCardRef:
        return RateCardRef(self.rate_card_id, self.version, self.pricing_fingerprint)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict(include_fingerprint=False))

    def covers(self, window: UsageWindow) -> bool:
        return self.effective_start <= window.start and window.end <= self.effective_end

    def matches_dimensions(self, dimensions: Mapping[str, str]) -> bool:
        return all(dimensions.get(key) == value for key, value in self.matching_dimensions.items())

    def transition(self, state: PublicationState, *, at: datetime) -> RateCardV1:
        allowed = {
            PublicationState.DRAFT: PublicationState.VALIDATED,
            PublicationState.VALIDATED: PublicationState.PUBLISHED,
            PublicationState.PUBLISHED: PublicationState.RETIRED,
        }
        if allowed.get(self.state) is not state:
            raise InvalidCost(
                f"rate-card transition {self.state.value} -> {state.value} is not permitted",
                requirement="cost.rate-card.lifecycle",
            )
        published_at = self.published_at
        if state is PublicationState.PUBLISHED:
            published_at = utc_datetime(at, "published_at")
        return replace(
            self,
            revision=self.revision + 1,
            state=state,
            published_at=published_at,
        )

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        result = {
            "schemaVersion": self.schema_version,
            "rateCardRevisionId": self.snapshot_id,
            "rateCardRevision": self.revision,
            **self._pricing_dict(),
            "pricingKey": self.pricing_key,
            "pricingFingerprint": self.pricing_fingerprint,
            "state": self.state.value,
            "createdAt": iso_datetime(self.created_at),
            "publishedAt": (None if self.published_at is None else iso_datetime(self.published_at)),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RateCardV1:
        raw_tiers = value.get("tiers", ())
        if not isinstance(raw_tiers, Sequence) or isinstance(raw_tiers, str | bytes | bytearray):
            raise InvalidCost("rate-card tiers have invalid shape")
        tiers = tuple(
            PricingTier.from_mapping(cast(Mapping[str, object], item)) for item in raw_tiers
        )
        raw_dimensions = value.get("matchingDimensions", {})
        raw_rounding = value.get("rounding", {})
        raw_provenance = value.get("provenance", {})
        if not all(
            isinstance(item, Mapping) for item in (raw_dimensions, raw_rounding, raw_provenance)
        ):
            raise InvalidCost("rate-card mapping fields have invalid shape")
        raw_credit = value.get("commitmentCredit")
        raw_supersedes = value.get("supersedes")
        return cls(
            rate_card_id=cast(str, value["rateCardId"]),
            version=cast(int, value["rateCardVersion"]),
            revision=cast(int, value["rateCardRevision"]),
            provider=cast(str, value["provider"]),
            product=cast(str, value["product"]),
            meter_id=cast(str, value["meterId"]),
            meter_version=cast(int, value["meterVersion"]),
            currency=cast(str, value["currency"]),
            effective_start=parse_datetime(value["effectiveStart"], "effective_start"),
            effective_end=parse_datetime(value["effectiveEnd"], "effective_end"),
            pricing_model=PricingModel(cast(str, value["pricingModel"])),
            created_at=parse_datetime(value["createdAt"], "created_at"),
            unit_price=(
                None if value.get("unitPrice") is None else decimal_value(value["unitPrice"])
            ),
            tiers=tiers,
            minimum_charge=(
                None
                if value.get("minimumCharge") is None
                else decimal_value(value["minimumCharge"])
            ),
            commitment_credit=(
                None
                if raw_credit is None
                else CommitmentCredit.from_mapping(cast(Mapping[str, object], raw_credit))
            ),
            matching_dimensions=cast(Mapping[str, str], raw_dimensions),
            rounding=RoundingPolicy.from_mapping(cast(Mapping[str, object], raw_rounding)),
            provenance=cast(Mapping[str, str], raw_provenance),
            state=PublicationState(cast(str, value["state"])),
            published_at=(
                None
                if value.get("publishedAt") is None
                else parse_datetime(value["publishedAt"], "published_at")
            ),
            supersedes=(
                None
                if raw_supersedes is None
                else RateCardRef.from_mapping(cast(Mapping[str, object], raw_supersedes))
            ),
        )


@dataclass(frozen=True, slots=True)
class CostAdjustmentV1:
    code: str
    amount: Decimal
    reason: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", logical_name(self.code, "adjustment code"))
        object.__setattr__(self, "amount", decimal_value(self.amount, "adjustment amount"))
        object.__setattr__(self, "reason", token(self.reason, "adjustment reason"))
        object.__setattr__(
            self,
            "source_fingerprint",
            require_fingerprint(self.source_fingerprint, "adjustment source_fingerprint"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "amount": decimal_text(self.amount),
            "reason": self.reason,
            "sourceFingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CostAdjustmentV1:
        return cls(
            cast(str, value["code"]),
            decimal_value(value["amount"], "adjustment amount"),
            cast(str, value["reason"]),
            cast(str, value["sourceFingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class UsageInputV1:
    """Closed, exact aggregate set obtained only through the Usage public API."""

    scope: UsageScope
    window: UsageWindow
    meter_id: str
    meter_version: int
    unit: str
    aggregates: tuple[UsageAggregateV1, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", UsageScope.parse(self.scope))
        if not isinstance(self.window, UsageWindow):
            raise InvalidCost("usage input window must be UsageWindow")
        object.__setattr__(self, "meter_id", logical_name(self.meter_id, "meter_id"))
        object.__setattr__(
            self, "meter_version", _positive_integer(self.meter_version, "meter version")
        )
        object.__setattr__(self, "unit", logical_name(self.unit, "unit"))
        aggregates = tuple(self.aggregates)
        if not aggregates or any(not isinstance(item, UsageAggregateV1) for item in aggregates):
            raise UnclosedUsage("closed Usage input requires at least one UsageAggregateV1")
        selected = tuple(sorted(aggregates, key=lambda item: item.version_id))
        if len({item.aggregate_id for item in selected}) != len(selected):
            raise UnclosedUsage("Usage input must select exactly one revision per aggregate")
        for aggregate in selected:
            if (
                aggregate.scope != self.scope
                or aggregate.meter_id != self.meter_id
                or aggregate.meter_version != self.meter_version
            ):
                raise InvalidCost("Usage aggregates do not share the requested scope and meter")
            if aggregate.window.start < self.window.start or aggregate.window.end > self.window.end:
                raise InvalidCost("Usage aggregate falls outside the pricing interval")
            if aggregate.watermark < self.window.end:
                raise UnclosedUsage()
        object.__setattr__(self, "aggregates", selected)

    @property
    def quantity(self) -> Decimal:
        return fit_decimal(sum((item.total for item in self.aggregates), Decimal(0)))

    @property
    def aggregate_refs(self) -> tuple[str, ...]:
        return tuple(item.version_id for item in self.aggregates)

    @property
    def fingerprint(self) -> str:
        return fingerprint(
            {
                "scopeFingerprint": self.scope.fingerprint,
                "window": self.window.to_dict(),
                "meterId": self.meter_id,
                "meterVersion": self.meter_version,
                "unit": self.unit,
                "aggregates": [
                    {
                        "ref": item.version_id,
                        "fingerprint": item.fingerprint,
                        "sourceFingerprint": item.source_fingerprint,
                    }
                    for item in self.aggregates
                ],
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.to_dict(),
            "scopeFingerprint": self.scope.fingerprint,
            "window": self.window.to_dict(),
            "meterId": self.meter_id,
            "meterVersion": self.meter_version,
            "unit": self.unit,
            "quantity": decimal_text(self.quantity),
            "aggregateRefs": list(self.aggregate_refs),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CalculationV1:
    calculation_id: str
    revision: int
    calculation_identity: str
    scope: UsageScope
    window: UsageWindow
    usage_fingerprint: str
    aggregate_refs: tuple[str, ...]
    rate_card: RateCardRef
    allocation_fingerprint: str
    calculator_version: str
    quantity: Decimal
    unit: str
    currency: str
    pre_adjustment_amount: Decimal
    adjustment_total: Decimal
    final_amount: Decimal
    explanation: Mapping[str, object]
    created_at: datetime
    record_ids: tuple[str, ...]
    supersedes: str | None = None
    schema_version: str = field(default=_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "calculation_id", token(self.calculation_id, "calculation_id"))
        object.__setattr__(
            self, "revision", _positive_integer(self.revision, "calculation revision")
        )
        object.__setattr__(
            self,
            "calculation_identity",
            require_fingerprint(self.calculation_identity, "calculation_identity"),
        )
        object.__setattr__(self, "scope", UsageScope.parse(self.scope))
        if not isinstance(self.window, UsageWindow):
            raise InvalidCost("calculation window must be UsageWindow")
        object.__setattr__(
            self,
            "usage_fingerprint",
            require_fingerprint(self.usage_fingerprint, "usage_fingerprint"),
        )
        aggregate_refs = tuple(token(item, "aggregate reference") for item in self.aggregate_refs)
        if not aggregate_refs or len(set(aggregate_refs)) != len(aggregate_refs):
            raise InvalidCost("calculation aggregate references must be non-empty and unique")
        object.__setattr__(self, "aggregate_refs", tuple(sorted(aggregate_refs)))
        if not isinstance(self.rate_card, RateCardRef):
            raise InvalidCost("calculation rate_card must be RateCardRef")
        object.__setattr__(
            self,
            "allocation_fingerprint",
            require_fingerprint(self.allocation_fingerprint, "allocation_fingerprint"),
        )
        object.__setattr__(
            self, "calculator_version", token(self.calculator_version, "calculator_version")
        )
        quantity = decimal_value(self.quantity, "calculation quantity")
        if quantity < 0:
            raise InvalidCost("calculation quantity cannot be negative")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit", logical_name(self.unit, "unit"))
        object.__setattr__(self, "currency", currency_code(self.currency))
        pre = decimal_value(self.pre_adjustment_amount, "pre_adjustment_amount")
        adjustment = decimal_value(self.adjustment_total, "adjustment_total")
        final = decimal_value(self.final_amount, "final_amount")
        if pre + adjustment != final or final < 0:
            raise InvalidCost("calculation monetary totals must be exact and non-negative")
        object.__setattr__(self, "pre_adjustment_amount", pre)
        object.__setattr__(self, "adjustment_total", adjustment)
        object.__setattr__(self, "final_amount", final)
        object.__setattr__(
            self, "explanation", immutable_json_mapping(self.explanation, "explanation")
        )
        object.__setattr__(self, "created_at", utc_datetime(self.created_at, "created_at"))
        record_ids = tuple(token(item, "record id") for item in self.record_ids)
        if not record_ids or len(set(record_ids)) != len(record_ids):
            raise InvalidCost("calculation record ids must be non-empty and unique")
        object.__setattr__(self, "record_ids", tuple(sorted(record_ids)))
        if self.supersedes is not None:
            object.__setattr__(self, "supersedes", token(self.supersedes, "supersedes"))

    @property
    def version_id(self) -> str:
        return f"{self.calculation_id}@{self.revision}"

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "calculationId": self.calculation_id,
            "calculationRevision": self.revision,
            "calculationVersionId": self.version_id,
            "calculationIdentity": self.calculation_identity,
            "scope": self.scope.to_dict(),
            "scopeFingerprint": self.scope.fingerprint,
            "windowStart": iso_datetime(self.window.start),
            "windowEnd": iso_datetime(self.window.end),
            "usageFingerprint": self.usage_fingerprint,
            "aggregateRefs": list(self.aggregate_refs),
            "rateCard": self.rate_card.to_dict(),
            "allocationFingerprint": self.allocation_fingerprint,
            "calculatorVersion": self.calculator_version,
            "quantity": decimal_text(self.quantity),
            "unit": self.unit,
            "currency": self.currency,
            "preAdjustmentAmount": decimal_text(self.pre_adjustment_amount),
            "adjustmentTotal": decimal_text(self.adjustment_total),
            "finalAmount": decimal_text(self.final_amount),
            "explanation": dict(self.explanation),
            "createdAt": iso_datetime(self.created_at),
            "recordIds": list(self.record_ids),
            "supersedes": self.supersedes,
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CalculationV1:
        raw_scope = value["scope"]
        raw_rate_card = value["rateCard"]
        raw_explanation = value["explanation"]
        if not all(
            isinstance(item, Mapping) for item in (raw_scope, raw_rate_card, raw_explanation)
        ):
            raise InvalidCost("calculation mapping fields have invalid shape")
        return cls(
            calculation_id=cast(str, value["calculationId"]),
            revision=cast(int, value["calculationRevision"]),
            calculation_identity=cast(str, value["calculationIdentity"]),
            scope=UsageScope(cast(Mapping[str, str], raw_scope)),
            window=UsageWindow(
                parse_datetime(value["windowStart"], "window start"),
                parse_datetime(value["windowEnd"], "window end"),
            ),
            usage_fingerprint=cast(str, value["usageFingerprint"]),
            aggregate_refs=tuple(cast(Sequence[str], value["aggregateRefs"])),
            rate_card=RateCardRef.from_mapping(cast(Mapping[str, object], raw_rate_card)),
            allocation_fingerprint=cast(str, value["allocationFingerprint"]),
            calculator_version=cast(str, value["calculatorVersion"]),
            quantity=decimal_value(value["quantity"]),
            unit=cast(str, value["unit"]),
            currency=cast(str, value["currency"]),
            pre_adjustment_amount=decimal_value(value["preAdjustmentAmount"]),
            adjustment_total=decimal_value(value["adjustmentTotal"]),
            final_amount=decimal_value(value["finalAmount"]),
            explanation=cast(Mapping[str, object], raw_explanation),
            created_at=parse_datetime(value["createdAt"], "created_at"),
            record_ids=tuple(cast(Sequence[str], value["recordIds"])),
            supersedes=cast(str | None, value.get("supersedes")),
        )


@dataclass(frozen=True, slots=True)
class CostRecordV1:
    cost_id: str
    calculation_id: str
    calculation_revision: int
    scope: UsageScope
    window: UsageWindow
    usage_fingerprint: str
    aggregate_refs: tuple[str, ...]
    rate_card: RateCardRef
    quantity: Decimal
    unit: str
    currency: str
    pre_adjustment_amount: Decimal
    adjustments: tuple[CostAdjustmentV1, ...]
    final_amount: Decimal
    allocation_dimensions: Mapping[str, str]
    allocation_fingerprint: str
    occurred_at: datetime
    lineage: Mapping[str, object]
    supersedes: str | None = None
    schema_version: str = field(default=_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cost_id", token(self.cost_id, "cost_id"))
        object.__setattr__(self, "calculation_id", token(self.calculation_id, "calculation_id"))
        object.__setattr__(
            self,
            "calculation_revision",
            _positive_integer(self.calculation_revision, "calculation revision"),
        )
        object.__setattr__(self, "scope", UsageScope.parse(self.scope))
        if not isinstance(self.window, UsageWindow):
            raise InvalidCost("cost record window must be UsageWindow")
        object.__setattr__(
            self,
            "usage_fingerprint",
            require_fingerprint(self.usage_fingerprint, "usage_fingerprint"),
        )
        refs = tuple(token(item, "aggregate reference") for item in self.aggregate_refs)
        if not refs or len(set(refs)) != len(refs):
            raise InvalidCost("cost record aggregate refs must be non-empty and unique")
        object.__setattr__(self, "aggregate_refs", tuple(sorted(refs)))
        if not isinstance(self.rate_card, RateCardRef):
            raise InvalidCost("cost record rate_card must be RateCardRef")
        quantity = decimal_value(self.quantity, "cost quantity")
        if quantity < 0:
            raise InvalidCost("cost quantity cannot be negative")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit", logical_name(self.unit, "unit"))
        object.__setattr__(self, "currency", currency_code(self.currency))
        pre = decimal_value(self.pre_adjustment_amount, "pre_adjustment_amount")
        adjustments = tuple(self.adjustments)
        if any(not isinstance(item, CostAdjustmentV1) for item in adjustments):
            raise InvalidCost("adjustments must contain CostAdjustmentV1 values")
        adjustment_total = sum((item.amount for item in adjustments), Decimal(0))
        final = decimal_value(self.final_amount, "final_amount")
        if pre + adjustment_total != final or final < 0:
            raise InvalidCost("cost record monetary totals must be exact and non-negative")
        object.__setattr__(self, "pre_adjustment_amount", pre)
        object.__setattr__(self, "adjustments", adjustments)
        object.__setattr__(self, "final_amount", final)
        object.__setattr__(
            self,
            "allocation_dimensions",
            string_map(self.allocation_dimensions, "allocation_dimensions", maximum_entries=16),
        )
        object.__setattr__(
            self,
            "allocation_fingerprint",
            require_fingerprint(self.allocation_fingerprint, "allocation_fingerprint"),
        )
        object.__setattr__(self, "occurred_at", utc_datetime(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "lineage", immutable_json_mapping(self.lineage, "lineage"))
        if self.supersedes is not None:
            object.__setattr__(self, "supersedes", token(self.supersedes, "supersedes"))

    @property
    def calculation_version_id(self) -> str:
        return f"{self.calculation_id}@{self.calculation_revision}"

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "costId": self.cost_id,
            "calculationId": self.calculation_id,
            "calculationRevision": self.calculation_revision,
            "calculationVersionId": self.calculation_version_id,
            "scope": self.scope.to_dict(),
            "scopeFingerprint": self.scope.fingerprint,
            "windowStart": iso_datetime(self.window.start),
            "windowEnd": iso_datetime(self.window.end),
            "usageFingerprint": self.usage_fingerprint,
            "aggregateRefs": list(self.aggregate_refs),
            "rateCard": self.rate_card.to_dict(),
            "quantity": decimal_text(self.quantity),
            "unit": self.unit,
            "currency": self.currency,
            "preAdjustmentAmount": decimal_text(self.pre_adjustment_amount),
            "adjustments": [item.to_dict() for item in self.adjustments],
            "finalAmount": decimal_text(self.final_amount),
            "allocationDimensions": dict(self.allocation_dimensions),
            "allocationFingerprint": self.allocation_fingerprint,
            "occurredAt": iso_datetime(self.occurred_at),
            "supersedes": self.supersedes,
            "lineage": dict(self.lineage),
        }
        if include_fingerprint:
            result["fingerprint"] = self.fingerprint
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CostRecordV1:
        raw_scope = value["scope"]
        raw_card = value["rateCard"]
        raw_adjustments = value["adjustments"]
        raw_dimensions = value["allocationDimensions"]
        raw_lineage = value["lineage"]
        if not isinstance(raw_adjustments, Sequence) or isinstance(
            raw_adjustments, str | bytes | bytearray
        ):
            raise InvalidCost("cost record adjustments have invalid shape")
        if not all(
            isinstance(item, Mapping) for item in (raw_scope, raw_card, raw_dimensions, raw_lineage)
        ):
            raise InvalidCost("cost record mapping fields have invalid shape")
        return cls(
            cost_id=cast(str, value["costId"]),
            calculation_id=cast(str, value["calculationId"]),
            calculation_revision=cast(int, value["calculationRevision"]),
            scope=UsageScope(cast(Mapping[str, str], raw_scope)),
            window=UsageWindow(
                parse_datetime(value["windowStart"], "window start"),
                parse_datetime(value["windowEnd"], "window end"),
            ),
            usage_fingerprint=cast(str, value["usageFingerprint"]),
            aggregate_refs=tuple(cast(Sequence[str], value["aggregateRefs"])),
            rate_card=RateCardRef.from_mapping(cast(Mapping[str, object], raw_card)),
            quantity=decimal_value(value["quantity"]),
            unit=cast(str, value["unit"]),
            currency=cast(str, value["currency"]),
            pre_adjustment_amount=decimal_value(value["preAdjustmentAmount"]),
            adjustments=tuple(
                CostAdjustmentV1.from_mapping(cast(Mapping[str, object], item))
                for item in raw_adjustments
            ),
            final_amount=decimal_value(value["finalAmount"]),
            allocation_dimensions=cast(Mapping[str, str], raw_dimensions),
            allocation_fingerprint=cast(str, value["allocationFingerprint"]),
            occurred_at=parse_datetime(value["occurredAt"], "occurred_at"),
            lineage=cast(Mapping[str, object], raw_lineage),
            supersedes=cast(str | None, value.get("supersedes")),
        )


@dataclass(frozen=True, slots=True)
class CostCalculationResult:
    calculation: CalculationV1
    records: tuple[CostRecordV1, ...]
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.calculation, CalculationV1):
            raise InvalidCost("result calculation must be CalculationV1")
        records = tuple(self.records)
        if not records or any(not isinstance(item, CostRecordV1) for item in records):
            raise InvalidCost("result records must contain CostRecordV1 values")
        if tuple(sorted(item.cost_id for item in records)) != self.calculation.record_ids:
            raise InvalidCost("result records differ from calculation record ids")
        object.__setattr__(self, "records", records)


__all__ = [
    "CalculationV1",
    "CommitmentCredit",
    "CostAdjustmentV1",
    "CostCalculationResult",
    "CostRecordV1",
    "PricingModel",
    "PricingTier",
    "PublicationState",
    "RateCardRef",
    "RateCardV1",
    "RoundingMode",
    "RoundingPolicy",
    "UsageInputV1",
]
