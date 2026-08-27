# SPDX-License-Identifier: Apache-2.0
"""Stable Cost V1 error taxonomy using Meridian's public failure model."""

from __future__ import annotations

from typing import Any, cast

from meridian_storage import ConflictError, NotFoundError, TransientError, ValidationError


class InvalidCost(ValidationError):
    """A Cost model, query, or operation is invalid."""

    def __init__(self, message: str, *, requirement: str = "cost.valid", **details: object) -> None:
        resource_ref = details.pop("resource_ref", None)
        if details:
            raise TypeError(f"unsupported Cost error details: {sorted(details)!r}")
        self.requirement = requirement
        super().__init__(
            "MERIDIAN_COST_INVALID",
            message,
            resource_ref=cast(str | None, resource_ref),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class MissingRateCard(NotFoundError):
    def __init__(self, reference: str) -> None:
        self.requirement = "cost.rate-card.exists"
        super().__init__(
            "MERIDIAN_COST_RATE_CARD_NOT_FOUND",
            f"Cost rate card {reference!r} was not found",
            resource_ref=reference,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class OverlappingRateCard(ConflictError):
    def __init__(self, reference: str, conflict: str) -> None:
        self.requirement = "cost.rate-card.interval.non-overlap"
        super().__init__(
            "MERIDIAN_COST_RATE_CARD_OVERLAP",
            f"Rate card {reference!r} overlaps published rate card {conflict!r}",
            resource_ref=reference,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class UnclosedUsage(InvalidCost):
    def __init__(self, message: str = "Usage input is not closed for the pricing interval") -> None:
        super().__init__(message, requirement="cost.usage.closed")


class UnitMismatch(InvalidCost):
    def __init__(self, unit: str, expected: str) -> None:
        super().__init__(
            f"Usage unit {unit!r} does not match rate-card meter unit {expected!r}",
            requirement="cost.unit.compatible",
        )


class CurrencyMismatch(InvalidCost):
    def __init__(self, currency: str, expected: str) -> None:
        super().__init__(
            f"Currency {currency!r} does not match rate-card currency {expected!r}",
            requirement="cost.currency.compatible",
        )


class InvalidTier(InvalidCost):
    def __init__(self, message: str) -> None:
        super().__init__(message, requirement="cost.tiers.valid")


class DecimalOverflow(InvalidCost):
    def __init__(self, message: str) -> None:
        super().__init__(message, requirement="cost.decimal.exact")


class StaleRateCardRevision(ConflictError):
    def __init__(self, reference: str) -> None:
        self.requirement = "cost.rate-card.revision.cas"
        super().__init__(
            "MERIDIAN_COST_RATE_CARD_STALE_REVISION",
            f"Rate card {reference!r} changed concurrently",
            resource_ref=reference,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class CostConflict(ConflictError):
    def __init__(self, identity: str, *, kind: str = "record") -> None:
        self.requirement = "cost.immutable.identity"
        super().__init__(
            "MERIDIAN_COST_IMMUTABLE_CONFLICT",
            f"Cost {kind} identity {identity!r} already has different immutable content",
            resource_ref=identity,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class UsageDependencyFailure(TransientError):
    def __init__(self, message: str, *, resource_ref: str | None = None) -> None:
        self.requirement = "cost.usage.dependency"
        super().__init__(
            "MERIDIAN_COST_USAGE_DEPENDENCY",
            message,
            resource_ref=resource_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["requirement"] = self.requirement
        return payload


class InvalidCostResult(InvalidCost):
    def __init__(self, message: str) -> None:
        super().__init__(message, requirement="cost.result.shape")


__all__ = [
    "CostConflict",
    "CurrencyMismatch",
    "DecimalOverflow",
    "InvalidCost",
    "InvalidCostResult",
    "InvalidTier",
    "MissingRateCard",
    "OverlappingRateCard",
    "StaleRateCardRevision",
    "UnclosedUsage",
    "UnitMismatch",
    "UsageDependencyFailure",
]
