# SPDX-License-Identifier: Apache-2.0
"""Shared public-contract fixtures for Cost tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

import pytest

from meridian_storage import (
    ConflictError,
    ErrorCode,
    Expression,
    NotFoundError,
    OperationResult,
    ResourceRef,
)
from meridian_storage.plugins.cost import (
    CostRepository,
    PricingModel,
    RateCardV1,
    UsageInputV1,
)
from meridian_storage.plugins.usage import UsageAggregateV1, UsageScope, UsageWindow

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 2, 1, tzinfo=UTC)
NOW = datetime(2026, 8, 26, tzinfo=UTC)
_ZERO_FINGERPRINT = "sha256:" + "0" * 64


def make_fingerprint(value: str) -> str:
    return f"sha256:{sha256(value.encode()).hexdigest()}"


def make_rate_card(**changes: object) -> RateCardV1:
    values: dict[str, Any] = {
        "rate_card_id": "cloud.requests",
        "version": 1,
        "revision": 1,
        "provider": "example.cloud",
        "product": "requests",
        "meter_id": "api.requests",
        "meter_version": 1,
        "currency": "USD",
        "effective_start": START,
        "effective_end": datetime(2027, 1, 1, tzinfo=UTC),
        "pricing_model": PricingModel.FLAT_UNIT,
        "created_at": NOW,
        "unit_price": Decimal("0.0025"),
        "provenance": {"source": "rate-sheet-2026"},
    }
    values.update(changes)
    return RateCardV1(**values)


def make_aggregate(**changes: object) -> UsageAggregateV1:
    scope = changes.pop("scope", UsageScope({"tenant": "acme"}))
    window = changes.pop("window", UsageWindow(START, END))
    values: dict[str, Any] = {
        "aggregate_id": "agg-001",
        "revision": 1,
        "scope": scope,
        "meter_id": "api.requests",
        "meter_version": 1,
        "window": window,
        "dimensions": {},
        "total": Decimal("1000"),
        "event_count": 10,
        "watermark": END,
        "source_fingerprint": make_fingerprint("usage-source"),
        "created_at": END,
    }
    values.update(changes)
    return UsageAggregateV1(**values)


def make_usage_input(*aggregates: UsageAggregateV1, unit: str = "request") -> UsageInputV1:
    selected = aggregates or (make_aggregate(),)
    first = selected[0]
    return UsageInputV1(
        first.scope,
        UsageWindow(START, END),
        first.meter_id,
        first.meter_version,
        unit,
        tuple(selected),
    )


def _resource(value: object) -> ResourceRef:
    if not isinstance(value, Mapping):
        raise AssertionError("test executor received an invalid Resource")
    return ResourceRef.parse(value)


def _compare(actual: object, operator: object, candidate: object) -> bool:
    matched = True
    if operator == "in":
        matched = actual in candidate  # type: ignore[operator]
    elif operator == "notIn":
        matched = actual not in candidate  # type: ignore[operator]
    elif operator == "isNull":
        matched = (actual is None) is candidate
    elif operator == "eq":
        matched = actual == candidate
    elif operator == "ne":
        matched = actual != candidate
    elif operator == "gte":
        matched = actual is not None and actual >= candidate  # type: ignore[operator]
    elif operator == "gt":
        matched = actual is not None and actual > candidate  # type: ignore[operator]
    elif operator == "lte":
        matched = actual is not None and actual <= candidate  # type: ignore[operator]
    elif operator == "lt":
        matched = actual is not None and actual < candidate  # type: ignore[operator]
    return matched


def _matches(record: Mapping[str, object], where: Mapping[str, object]) -> bool:
    for name, expected in where.items():
        actual = record.get(name)
        if not isinstance(expected, Mapping):
            if actual != expected:
                return False
            continue
        for operator, candidate in expected.items():
            if not _compare(actual, operator, candidate):
                return False
    return True


def _identity(record: Mapping[str, object]) -> tuple[object, ...]:
    for fields in (
        ("rateCardRevisionId",),
        ("costId",),
        ("calculationVersionId",),
        ("meterId", "meterVersion"),
        ("aggregateId", "aggregateRevision"),
    ):
        if all(name in record for name in fields):
            return tuple(record[name] for name in fields)
    return (make_fingerprint(repr(sorted(record.items()))),)


@dataclass
class MemoryExecutor:
    """A deterministic logical-resource executor, never a physical database substitute."""

    records: dict[ResourceRef, list[dict[str, object]]] = field(default_factory=dict)
    evidence: list[Mapping[str, object]] = field(default_factory=list)
    expressions: list[Expression] = field(default_factory=list)
    transactions: int = 0

    def seed(self, resource: ResourceRef, record: Mapping[str, object]) -> None:
        self.records.setdefault(resource, []).append(dict(record))

    def execute(self, expression: Expression) -> OperationResult:
        self.expressions.append(expression)
        arguments = expression.arguments
        resource = _resource(arguments["resource"])
        data: object
        if expression.catalog == "evidence" and expression.method == "append":
            payload = arguments["data"]
            assert isinstance(payload, Mapping)
            self.evidence.append(payload)
            data = {"accepted": True}
        elif expression.method == "put":
            payload = arguments["data"]
            assert isinstance(payload, Mapping)
            selected = dict(payload)
            target = self.records.setdefault(resource, [])
            if any(_identity(item) == _identity(selected) for item in target):
                raise ConflictError(ErrorCode.IDEMPOTENCY_CONFLICT, "duplicate test identity")
            target.append(selected)
            data = selected
        elif expression.method == "get":
            where = arguments["where"]
            assert isinstance(where, Mapping)
            match = next(
                (item for item in self.records.get(resource, ()) if _matches(item, where)),
                None,
            )
            if match is None:
                raise NotFoundError(ErrorCode.RESOURCE_NOT_FOUND, "test record not found")
            data = match
        elif expression.method == "query":
            where = arguments.get("where", {})
            assert isinstance(where, Mapping)
            matches = [item for item in self.records.get(resource, ()) if _matches(item, where)]
            order = arguments.get("orderBy", ())
            assert isinstance(order, tuple)
            for item in reversed(order):
                assert isinstance(item, Mapping)
                name = item["field"]
                reverse = item.get("direction", "asc") == "desc"
                matches.sort(key=lambda value: repr(value.get(name)), reverse=reverse)
            limit = arguments.get("limit", 100)
            assert isinstance(limit, int)
            raw_cursor = arguments.get("cursor", "0")
            assert isinstance(raw_cursor, str)
            offset = int(raw_cursor)
            page = matches[offset : offset + limit]
            next_offset = offset + len(page)
            data = {
                "items": page,
                "cursor": str(next_offset) if next_offset < len(matches) else None,
            }
        else:
            raise AssertionError(
                f"unsupported test expression: {expression.catalog}.{expression.method}"
            )
        return OperationResult(
            data=data,
            catalog=expression.catalog,
            operation_contract=f"meridian.{expression.catalog}.{expression.method}",
            operation_version="1.0.0",
            resources=(resource,),
            request_id="request-test",
            execution_id=f"execution-{len(self.expressions)}",
            operation_fingerprint=expression.fingerprint,
            registry_fingerprint=_ZERO_FINGERPRINT,
            capability_fingerprint=_ZERO_FINGERPRINT,
            provenance={"adapter": "logical-memory-test"},
        )

    @contextmanager
    def transaction(self, resource: ResourceRef) -> Iterator[object]:
        del resource
        self.transactions += 1
        yield object()


@pytest.fixture
def executor() -> MemoryExecutor:
    return MemoryExecutor()


@pytest.fixture
def repository(executor: MemoryExecutor) -> CostRepository:
    return CostRepository(executor)


@pytest.fixture
def published_card(repository: CostRepository) -> RateCardV1:
    draft, _ = repository.create_rate_card(make_rate_card())
    validated = repository.validate_rate_card(
        draft.rate_card_id,
        draft.version,
        expected_revision=draft.revision,
        now=NOW,
    )
    return repository.publish_rate_card(
        validated.rate_card_id,
        validated.version,
        expected_revision=validated.revision,
        now=NOW,
    )
