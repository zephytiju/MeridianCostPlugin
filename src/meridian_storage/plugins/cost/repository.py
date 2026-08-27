# SPDX-License-Identifier: Apache-2.0
"""Meridian-backed immutable Cost repositories and rate-card lifecycle APIs."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, cast

from meridian_storage import (
    ConflictError,
    Expression,
    NotFoundError,
    OperationResult,
    ResourceRef,
    bind_context,
    current_context,
)
from meridian_storage.plugins.usage import UsageResources, UsageWindow
from meridian_storage.semantics import StructuredCatalogSurface

from ._canonical import fingerprint, logical_name, token, utc_datetime
from .errors import (
    CostConflict,
    InvalidCost,
    InvalidCostResult,
    MissingRateCard,
    OverlappingRateCard,
    StaleRateCardRevision,
)
from .models import (
    CalculationV1,
    CostRecordV1,
    PublicationState,
    RateCardV1,
)
from .query import CostQueries, CostQuery, MeridianExecutor


class TransactionalExecutor(MeridianExecutor, Protocol):
    def transaction(self, resource: ResourceRef) -> AbstractContextManager[object]: ...


@dataclass(frozen=True, slots=True)
class CostResources:
    """Cost-owned logical Resources whose deployment placement is IaC-owned."""

    rate_cards: ResourceRef = field(
        default_factory=lambda: ResourceRef("structured", "cost", "rate_cards")
    )
    calculations: ResourceRef = field(
        default_factory=lambda: ResourceRef("structured", "cost", "calculations")
    )
    records: ResourceRef = field(
        default_factory=lambda: ResourceRef("structured", "cost", "records")
    )

    def __post_init__(self) -> None:
        refs: list[ResourceRef] = []
        for name in ("rate_cards", "calculations", "records"):
            try:
                selected = ResourceRef.parse(getattr(self, name), catalog="structured")
            except (TypeError, ValueError) as exc:
                raise InvalidCost(f"{name} must be a logical structured Resource") from exc
            object.__setattr__(self, name, selected)
            refs.append(selected)
        if len(set(refs)) != len(refs):
            raise InvalidCost("Cost logical Resources must be distinct")

    def assert_usage_isolation(self, usage: UsageResources) -> None:
        if not isinstance(usage, UsageResources):
            raise TypeError("usage must be released UsageResources")
        usage_refs = {
            usage.meters,
            usage.events,
            usage.aggregates,
            usage.batches,
            usage.checkpoints,
            usage.claims,
        }
        overlap = usage_refs & {self.rate_cards, self.calculations, self.records}
        if overlap:
            raise InvalidCost(
                f"Cost and Usage logical Resources overlap: {sorted(map(str, overlap))!r}",
                requirement="cost.storage.isolated",
            )

    def isolation_manifest(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "costResources": [
                    self.rate_cards.to_dict(),
                    self.calculations.to_dict(),
                    self.records.to_dict(),
                ],
                "usageAccess": "released-public-api-only",
                "distinctPhysicalPlacementRequired": True,
                "placementAuthority": "platform-or-vangu-iac",
            }
        )


def _single_record(data: object) -> Mapping[str, object] | None:
    if data is None:
        return None
    selected = data
    if isinstance(data, Mapping):
        selected = next((data[key] for key in ("item", "record", "data") if key in data), data)
    if selected is None:
        return None
    if not isinstance(selected, Mapping):
        raise InvalidCostResult("Meridian get returned an invalid Cost record")
    return MappingProxyType(dict(selected))


@contextmanager
def _idempotency(kind: str, identity: str) -> Iterator[None]:
    context = current_context(required=False)
    if context is None:
        yield
        return
    selected = replace(
        context,
        idempotency_key=fingerprint({"kind": kind, "identity": identity}),
    )
    with bind_context(selected):
        yield


class CostRepository:
    """Persistence boundary using only mapping-first structured Expressions."""

    def __init__(
        self,
        executor: MeridianExecutor,
        resources: CostResources | None = None,
    ) -> None:
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must implement Meridian.execute(Expression)")
        self._executor = executor
        self.resources = resources or CostResources()
        self.queries = CostQueries(
            executor,
            self.resources.rate_cards,
            self.resources.calculations,
            self.resources.records,
        )
        self._surface = StructuredCatalogSurface()

    def _execute(self, expression: Expression, *, kind: str, identity: str) -> OperationResult:
        with _idempotency(kind, identity):
            return self._executor.execute(expression)

    def _get(
        self,
        resource: ResourceRef,
        where: Mapping[str, object],
        *,
        identity: str,
    ) -> Mapping[str, object] | None:
        expression = self._surface.get(resource=resource.to_dict(), where=where)
        try:
            result = self._execute(expression, kind="get", identity=identity)
        except NotFoundError:
            return None
        return _single_record(result.data)

    def _put(
        self,
        resource: ResourceRef,
        data: Mapping[str, object],
        *,
        identity: str,
    ) -> OperationResult:
        expression = self._surface.put(resource=resource.to_dict(), data=data)
        return self._execute(expression, kind="put", identity=identity)

    @staticmethod
    def _scan(query: CostQuery) -> tuple[Mapping[str, object], ...]:
        items: list[Mapping[str, object]] = []
        cursor: str | None = None
        while True:
            result = query.page(limit=500, cursor=cursor).execute()
            items.extend(result.items)
            cursor = result.cursor
            if cursor is None:
                return tuple(items)

    def list_rate_cards(
        self,
        *,
        provider: str | None = None,
        product: str | None = None,
        meter_id: str | None = None,
        meter_version: int | None = None,
        state: PublicationState | None = None,
        pricing_key: str | None = None,
    ) -> tuple[RateCardV1, ...]:
        where: dict[str, object] = {}
        if provider is not None:
            where["provider"] = logical_name(provider, "provider")
        if product is not None:
            where["product"] = logical_name(product, "product")
        if meter_id is not None:
            where["meterId"] = logical_name(meter_id, "meter_id")
        if meter_version is not None:
            if (
                isinstance(meter_version, bool)
                or not isinstance(meter_version, int)
                or meter_version < 1
            ):
                raise InvalidCost("meter_version must be a positive integer")
            where["meterVersion"] = meter_version
        if pricing_key is not None:
            where["pricingKey"] = pricing_key
        snapshots = (
            RateCardV1.from_mapping(item)
            for item in self._scan(self.queries.rate_cards(where=where))
        )
        latest: dict[tuple[str, int], RateCardV1] = {}
        for card in snapshots:
            key = (card.rate_card_id, card.version)
            current = latest.get(key)
            if current is None or card.revision > current.revision:
                latest[key] = card
        selected: Iterable[RateCardV1] = latest.values()
        if state is not None:
            if not isinstance(state, PublicationState):
                raise InvalidCost("state must be PublicationState")
            selected = (item for item in selected if item.state is state)
        return tuple(sorted(selected, key=lambda item: (item.rate_card_id, item.version)))

    def get_rate_card(
        self,
        rate_card_id: str,
        version: int,
        *,
        revision: int | None = None,
    ) -> RateCardV1:
        selected_id = token(rate_card_id, "rate_card_id")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise InvalidCost("rate-card version must be a positive integer")
        where: dict[str, object] = {"rateCardId": selected_id, "rateCardVersion": version}
        if revision is not None:
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise InvalidCost("rate-card revision must be a positive integer")
            where["rateCardRevision"] = revision
        values = tuple(
            RateCardV1.from_mapping(item)
            for item in self._scan(self.queries.rate_cards(where=where))
        )
        if not values:
            suffix = f"#{revision}" if revision is not None else ""
            raise MissingRateCard(f"{selected_id}@{version}{suffix}")
        return max(values, key=lambda item: item.revision)

    def _append_card(self, card: RateCardV1, *, stale_on_conflict: bool = False) -> RateCardV1:
        try:
            self._put(
                self.resources.rate_cards,
                card.to_dict(),
                identity=card.snapshot_id,
            )
        except ConflictError:
            persisted = self.get_rate_card(card.rate_card_id, card.version, revision=card.revision)
            if persisted.fingerprint == card.fingerprint:
                return persisted
            if stale_on_conflict:
                raise StaleRateCardRevision(card.identity) from None
            raise CostConflict(card.snapshot_id, kind="rate-card snapshot") from None
        return card

    def create_rate_card(self, card: RateCardV1) -> tuple[RateCardV1, bool]:
        if not isinstance(card, RateCardV1):
            raise TypeError("card must be RateCardV1")
        if card.revision != 1 or card.state is not PublicationState.DRAFT:
            raise InvalidCost("new rate cards must start as draft revision 1")
        try:
            existing = self.get_rate_card(card.rate_card_id, card.version, revision=1)
        except MissingRateCard:
            existing = None
        if existing is not None:
            if existing.fingerprint != card.fingerprint:
                raise CostConflict(card.snapshot_id, kind="rate-card snapshot")
            return existing, True
        return self._append_card(card), False

    def replace_draft(self, card: RateCardV1, *, expected_revision: int) -> RateCardV1:
        if not isinstance(card, RateCardV1):
            raise TypeError("card must be RateCardV1")
        current = self.get_rate_card(card.rate_card_id, card.version)
        if current.revision != expected_revision:
            raise StaleRateCardRevision(current.identity)
        if current.state is not PublicationState.DRAFT or card.state is not PublicationState.DRAFT:
            raise InvalidCost(
                "only a draft rate card can replace draft pricing",
                requirement="cost.rate-card.lifecycle",
            )
        if card.revision != expected_revision + 1:
            raise InvalidCost("replacement draft revision must increment expected_revision")
        return self._append_card(card, stale_on_conflict=True)

    def _transition(
        self,
        rate_card_id: str,
        version: int,
        expected_revision: int,
        state: PublicationState,
        now: datetime | None,
    ) -> RateCardV1:
        current = self.get_rate_card(rate_card_id, version)
        if current.revision != expected_revision:
            raise StaleRateCardRevision(current.identity)
        selected_now = utc_datetime(now or datetime.now(UTC), "transition time")
        transitioned = current.transition(state, at=selected_now)
        return self._append_card(transitioned, stale_on_conflict=True)

    def validate_rate_card(
        self,
        rate_card_id: str,
        version: int,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> RateCardV1:
        return self._transition(
            rate_card_id,
            version,
            expected_revision,
            PublicationState.VALIDATED,
            now,
        )

    def publish_rate_card(
        self,
        rate_card_id: str,
        version: int,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> RateCardV1:
        current = self.get_rate_card(rate_card_id, version)
        if current.revision != expected_revision:
            raise StaleRateCardRevision(current.identity)
        if current.state is not PublicationState.VALIDATED:
            raise InvalidCost(
                "only a validated rate card can be published",
                requirement="cost.rate-card.lifecycle",
            )
        for other in self.list_rate_cards(
            pricing_key=current.pricing_key,
            state=PublicationState.PUBLISHED,
        ):
            overlaps = (
                current.effective_start < other.effective_end
                and other.effective_start < current.effective_end
            )
            if other.identity != current.identity and overlaps:
                raise OverlappingRateCard(current.identity, other.identity)
        return self._transition(
            rate_card_id,
            version,
            expected_revision,
            PublicationState.PUBLISHED,
            now,
        )

    def retire_rate_card(
        self,
        rate_card_id: str,
        version: int,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> RateCardV1:
        return self._transition(
            rate_card_id,
            version,
            expected_revision,
            PublicationState.RETIRED,
            now,
        )

    def delete_rate_card(
        self,
        rate_card_id: str,
        version: int,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> RateCardV1:
        """V1 delete is an immutable retirement snapshot, never physical deletion."""

        return self.retire_rate_card(
            rate_card_id,
            version,
            expected_revision=expected_revision,
            now=now,
        )

    def resolve_rate_card(
        self,
        *,
        provider: str,
        product: str,
        meter_id: str,
        meter_version: int,
        window: UsageWindow,
        dimensions: Mapping[str, str] | None = None,
    ) -> RateCardV1:
        if not isinstance(window, UsageWindow):
            raise TypeError("window must be UsageWindow")
        selected_dimensions = dimensions or {}
        matches = tuple(
            card
            for card in self.list_rate_cards(
                provider=provider,
                product=product,
                meter_id=meter_id,
                meter_version=meter_version,
                state=PublicationState.PUBLISHED,
            )
            if card.covers(window) and card.matches_dimensions(selected_dimensions)
        )
        if not matches:
            raise MissingRateCard(
                f"{provider}/{product}/{meter_id}@{meter_version}/{window.start.isoformat()}"
            )
        if len(matches) != 1:
            raise InvalidCost(
                "more than one published rate card matches the pricing interval",
                requirement="cost.rate-card.resolve.unique",
            )
        return matches[0]

    def get_calculation(self, calculation_id: str, revision: int) -> CalculationV1 | None:
        selected_id = token(calculation_id, "calculation_id")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise InvalidCost("calculation revision must be positive")
        record = self._get(
            self.resources.calculations,
            {"calculationId": selected_id, "calculationRevision": revision},
            identity=f"{selected_id}@{revision}",
        )
        return None if record is None else CalculationV1.from_mapping(record)

    def find_calculation(self, calculation_identity: str) -> CalculationV1 | None:
        values = tuple(
            CalculationV1.from_mapping(item)
            for item in self._scan(
                CostQuery(
                    self._executor,
                    self.resources.calculations,
                    {"calculationIdentity": calculation_identity},
                )
            )
        )
        if not values:
            return None
        first = values[0]
        if any(item.fingerprint != first.fingerprint for item in values[1:]):
            raise CostConflict(calculation_identity, kind="calculation identity")
        return first

    def latest_calculation(self, calculation_id: str) -> CalculationV1 | None:
        selected_id = token(calculation_id, "calculation_id")
        values = tuple(
            CalculationV1.from_mapping(item)
            for item in self._scan(
                CostQuery(
                    self._executor,
                    self.resources.calculations,
                    {"calculationId": selected_id},
                )
            )
        )
        return None if not values else max(values, key=lambda item: item.revision)

    def get_cost_record(self, cost_id: str) -> CostRecordV1 | None:
        selected_id = token(cost_id, "cost_id")
        record = self._get(
            self.resources.records,
            {"costId": selected_id},
            identity=selected_id,
        )
        return None if record is None else CostRecordV1.from_mapping(record)

    def records_for_calculation(
        self,
        calculation_id: str,
        revision: int,
    ) -> tuple[CostRecordV1, ...]:
        version_id = f"{token(calculation_id, 'calculation_id')}@{revision}"
        values = (
            CostRecordV1.from_mapping(item)
            for item in self._scan(
                CostQuery(
                    self._executor,
                    self.resources.records,
                    {"calculationVersionId": version_id},
                )
            )
        )
        return tuple(sorted(values, key=lambda item: item.cost_id))

    def _put_record(self, record: CostRecordV1) -> CostRecordV1:
        existing = self.get_cost_record(record.cost_id)
        if existing is not None:
            if existing.fingerprint != record.fingerprint:
                raise CostConflict(record.cost_id, kind="cost record")
            return existing
        try:
            self._put(self.resources.records, record.to_dict(), identity=record.cost_id)
        except ConflictError:
            existing = self.get_cost_record(record.cost_id)
            if existing is None or existing.fingerprint != record.fingerprint:
                raise CostConflict(record.cost_id, kind="cost record") from None
            return existing
        return record

    def _put_calculation(self, calculation: CalculationV1) -> CalculationV1:
        existing = self.get_calculation(calculation.calculation_id, calculation.revision)
        if existing is not None:
            if existing.fingerprint != calculation.fingerprint:
                raise CostConflict(calculation.version_id, kind="calculation")
            return existing
        try:
            self._put(
                self.resources.calculations,
                calculation.to_dict(),
                identity=calculation.version_id,
            )
        except ConflictError:
            existing = self.get_calculation(calculation.calculation_id, calculation.revision)
            if existing is None or existing.fingerprint != calculation.fingerprint:
                raise CostConflict(calculation.version_id, kind="calculation") from None
            return existing
        return calculation

    def persist_calculation(
        self,
        calculation: CalculationV1,
        records: Sequence[CostRecordV1],
    ) -> tuple[CalculationV1, tuple[CostRecordV1, ...]]:
        if not isinstance(calculation, CalculationV1):
            raise TypeError("calculation must be CalculationV1")
        selected = tuple(records)
        if not selected or any(not isinstance(item, CostRecordV1) for item in selected):
            raise InvalidCost("records must contain CostRecordV1 values")
        typed = selected
        if tuple(sorted(item.cost_id for item in typed)) != calculation.record_ids:
            raise InvalidCost("calculation record ids differ from persisted records")
        if any(item.calculation_version_id != calculation.version_id for item in typed):
            raise InvalidCost("cost records reference another calculation version")
        if (
            sum((item.final_amount for item in typed), start=calculation.final_amount * 0)
            != calculation.final_amount
        ):
            raise InvalidCost("allocated records must preserve the calculation final amount")
        transaction = getattr(self._executor, "transaction", None)
        boundary: AbstractContextManager[object]
        if callable(transaction):
            boundary = cast(TransactionalExecutor, self._executor).transaction(
                self.resources.calculations
            )
        else:
            boundary = nullcontext()
        with boundary:
            persisted_records = tuple(self._put_record(item) for item in typed)
            persisted_calculation = self._put_calculation(calculation)
        return persisted_calculation, tuple(
            sorted(persisted_records, key=lambda item: item.cost_id)
        )


__all__ = ["CostRepository", "CostResources", "TransactionalExecutor"]
