# SPDX-License-Identifier: Apache-2.0
"""Usage input providers constrained to the released public Usage API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from meridian_storage import MeridianError
from meridian_storage.plugins.usage import (
    UsageAggregateV1,
    UsageRepository,
    UsageScope,
    UsageWindow,
)

from .errors import InvalidCost, UsageDependencyFailure
from .models import RateCardV1, UsageInputV1


class UsageInputProvider(Protocol):
    """Common normalized interface for in-process and authorized service integrations."""

    def fetch(
        self,
        card: RateCardV1,
        scope: UsageScope,
        window: UsageWindow,
    ) -> UsageInputV1: ...


@dataclass(frozen=True, slots=True)
class RepositoryUsageProvider:
    """Fetch aggregates through UsageRepository without opening its executor or storage."""

    repository: UsageRepository
    page_size: int = 500

    def __post_init__(self) -> None:
        if not isinstance(self.repository, UsageRepository):
            raise TypeError("repository must be the released UsageRepository")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 500
        ):
            raise InvalidCost("Usage page_size must be between 1 and 500")

    def fetch(
        self,
        card: RateCardV1,
        scope: UsageScope,
        window: UsageWindow,
    ) -> UsageInputV1:
        if not isinstance(card, RateCardV1) or not isinstance(window, UsageWindow):
            raise TypeError("card and window must be Cost/Usage public models")
        selected_scope = UsageScope.parse(scope)
        try:
            meter = self.repository.get_meter(card.meter_id, card.meter_version)
            query = self.repository.queries.aggregates(
                selected_scope,
                window.start,
                window.end,
                where={"meterId": card.meter_id, "meterVersion": card.meter_version},
            )
            cursor: str | None = None
            aggregates: list[UsageAggregateV1] = []
            while True:
                result = query.page(limit=self.page_size, cursor=cursor).execute()
                aggregates.extend(UsageAggregateV1.from_mapping(item) for item in result.items)
                cursor = result.cursor
                if cursor is None:
                    break
        except MeridianError as exc:
            raise UsageDependencyFailure(
                f"Usage public API failed before Cost calculation: {exc.code}",
                resource_ref=f"{card.meter_id}@{card.meter_version}",
            ) from exc

        latest: dict[str, UsageAggregateV1] = {}
        for aggregate in aggregates:
            if not card.matches_dimensions(aggregate.dimensions):
                continue
            current = latest.get(aggregate.aggregate_id)
            if current is None or aggregate.revision > current.revision:
                latest[aggregate.aggregate_id] = aggregate
        return UsageInputV1(
            selected_scope,
            window,
            card.meter_id,
            card.meter_version,
            meter.canonical_unit,
            tuple(latest.values()),
        )


class StaticUsageProvider:
    """Contract fixture and service-boundary adapter target for an already normalized input."""

    def __init__(self, value: UsageInputV1) -> None:
        if not isinstance(value, UsageInputV1):
            raise TypeError("value must be UsageInputV1")
        self._value = value

    def fetch(
        self,
        card: RateCardV1,
        scope: UsageScope,
        window: UsageWindow,
    ) -> UsageInputV1:
        if (
            self._value.scope != UsageScope.parse(scope)
            or self._value.window != window
            or self._value.meter_id != card.meter_id
            or self._value.meter_version != card.meter_version
        ):
            raise InvalidCost("static Usage input does not match the calculation request")
        return self._value


def usage_provider(value: UsageInputProvider | UsageRepository) -> UsageInputProvider:
    if isinstance(value, UsageRepository):
        return RepositoryUsageProvider(value)
    if not callable(getattr(value, "fetch", None)):
        raise TypeError("usage must be UsageRepository or UsageInputProvider")
    return value


__all__ = [
    "RepositoryUsageProvider",
    "StaticUsageProvider",
    "UsageInputProvider",
    "usage_provider",
]
