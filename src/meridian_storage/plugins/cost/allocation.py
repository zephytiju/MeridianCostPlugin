# SPDX-License-Identifier: Apache-2.0
"""Fingerprint-bound allocation with a deterministic largest-remainder rule."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext
from types import MappingProxyType

from ._canonical import (
    decimal_text,
    decimal_value,
    fingerprint,
    fit_decimal,
    logical_name,
    require_fingerprint,
    string_map,
)
from .errors import InvalidCost


@dataclass(frozen=True, slots=True)
class AllocationWeightV1:
    dimensions: Mapping[str, str]
    weight: Decimal
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dimensions",
            string_map(self.dimensions, "allocation dimensions", maximum_entries=16),
        )
        weight = decimal_value(self.weight, "allocation weight")
        if weight < 0:
            raise InvalidCost("allocation weights cannot be negative")
        object.__setattr__(self, "weight", weight)
        object.__setattr__(
            self,
            "source_fingerprint",
            require_fingerprint(self.source_fingerprint, "allocation source_fingerprint"),
        )

    @property
    def key_fingerprint(self) -> str:
        return fingerprint(dict(self.dimensions))

    def to_dict(self) -> dict[str, object]:
        return {
            "dimensions": dict(self.dimensions),
            "weight": decimal_text(self.weight),
            "sourceFingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class AllocationSpecV1:
    dimensions: tuple[str, ...]
    weights: tuple[AllocationWeightV1, ...]
    name: str = "allocation.v1"

    def __post_init__(self) -> None:
        dimensions = tuple(logical_name(item, "allocation dimension") for item in self.dimensions)
        if len(set(dimensions)) != len(dimensions) or len(dimensions) > 16:
            raise InvalidCost("allocation dimensions must be unique and bounded")
        object.__setattr__(self, "dimensions", dimensions)
        weights = tuple(self.weights)
        if (
            not weights
            or len(weights) > 1000
            or any(not isinstance(item, AllocationWeightV1) for item in weights)
        ):
            raise InvalidCost("allocation requires 1 to 1000 AllocationWeightV1 values")
        selected = weights
        expected = set(dimensions)
        if any(set(item.dimensions) != expected for item in selected):
            raise InvalidCost("each allocation key must contain exactly the declared dimensions")
        if len({item.key_fingerprint for item in selected}) != len(selected):
            raise InvalidCost("allocation keys must be unique")
        if sum((item.weight for item in selected), Decimal(0)) <= 0:
            raise InvalidCost("allocation weights must have a positive total")
        object.__setattr__(
            self,
            "weights",
            tuple(sorted(selected, key=lambda item: item.key_fingerprint)),
        )
        object.__setattr__(self, "name", logical_name(self.name, "allocation name"))

    @classmethod
    def unallocated(cls) -> AllocationSpecV1:
        source = fingerprint({"allocation": "unallocated.v1"})
        return cls((), (AllocationWeightV1({}, Decimal(1), source),), "unallocated.v1")

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dimensions": list(self.dimensions),
            "weights": [item.to_dict() for item in self.weights],
        }


@dataclass(frozen=True, slots=True)
class AllocatedAmountV1:
    dimensions: Mapping[str, str]
    amount: Decimal
    weight: Decimal
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "dimensions", MappingProxyType(dict(sorted(self.dimensions.items())))
        )

    @property
    def key_fingerprint(self) -> str:
        return fingerprint(dict(self.dimensions))

    def to_dict(self) -> dict[str, object]:
        return {
            "dimensions": dict(self.dimensions),
            "amount": decimal_text(self.amount),
            "weight": decimal_text(self.weight),
            "sourceFingerprint": self.source_fingerprint,
        }


def _scale_quantum(scale: int) -> Decimal:
    if isinstance(scale, bool) or not isinstance(scale, int) or not 0 <= scale <= 18:
        raise InvalidCost("allocation scale must be between 0 and 18")
    return Decimal(1).scaleb(-scale)


def allocate_amount(
    total: Decimal,
    spec: AllocationSpecV1,
    *,
    scale: int,
) -> tuple[AllocatedAmountV1, ...]:
    """Allocate an exactly scaled value; ties use canonical dimension fingerprints."""

    if not isinstance(spec, AllocationSpecV1):
        raise TypeError("spec must be AllocationSpecV1")
    selected = decimal_value(total, "allocation total")
    quantum = _scale_quantum(scale)
    with localcontext() as context:
        context.prec = 100
        aligned = selected.quantize(quantum)
        if aligned != selected:
            raise InvalidCost("allocation total must already match the requested scale")
        sign = Decimal(-1) if selected < 0 else Decimal(1)
        unit_count = int((abs(selected) / quantum).to_integral_exact())
        weight_total = sum((item.weight for item in spec.weights), Decimal(0))
        raw_units = [Decimal(unit_count) * item.weight / weight_total for item in spec.weights]
        base_units = [int(item.to_integral_value(rounding=ROUND_FLOOR)) for item in raw_units]
        remaining = unit_count - sum(base_units)
        order = sorted(
            range(len(spec.weights)),
            key=lambda index: (
                -(raw_units[index] - Decimal(base_units[index])),
                spec.weights[index].key_fingerprint,
            ),
        )
        for index in order[:remaining]:
            base_units[index] += 1
        amounts = [fit_decimal(sign * Decimal(units) * quantum) for units in base_units]
    result = tuple(
        AllocatedAmountV1(
            item.dimensions,
            amount,
            item.weight,
            item.source_fingerprint,
        )
        for item, amount in zip(spec.weights, amounts, strict=True)
    )
    if sum((item.amount for item in result), Decimal(0)) != selected:
        raise AssertionError("largest-remainder allocation changed the total")
    return result


def allocation_map(
    values: Sequence[AllocatedAmountV1],
) -> Mapping[str, AllocatedAmountV1]:
    return MappingProxyType({item.key_fingerprint: item for item in values})


__all__ = [
    "AllocatedAmountV1",
    "AllocationSpecV1",
    "AllocationWeightV1",
    "allocate_amount",
    "allocation_map",
]
