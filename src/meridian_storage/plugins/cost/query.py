# SPDX-License-Identifier: Apache-2.0
"""Bounded Cost queries as mapping-first Expressions and serialized Operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, cast

from meridian_storage import Expression, OperationResult, ResourceRef
from meridian_storage.plugins.usage import UsageScope
from meridian_storage.query import (
    BooleanExpression,
    PageSpec,
    Projection,
    QueryOperation,
    QueryTarget,
    ResultSpec,
    SafetyBudget,
    Sort,
    ValueExpression,
    field,
)
from meridian_storage.semantics import StructuredCatalogSurface

from ._canonical import iso_datetime, json_value, logical_name, utc_datetime
from .errors import InvalidCost, InvalidCostResult

_MAX_PAGE_SIZE = 500
_OPERATORS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "notIn", "isNull"})


class MeridianExecutor(Protocol):
    def execute(self, expression: Expression) -> OperationResult: ...


@dataclass(frozen=True, slots=True)
class CostOrder:
    field: str
    direction: str = "asc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", logical_name(self.field, "order field"))
        if self.direction not in {"asc", "desc"}:
            raise InvalidCost("query order direction must be asc or desc")

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "direction": self.direction}


def _where(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise InvalidCost("query predicates must be a mapping with at most 32 fields")
    output: dict[str, object] = {}
    for raw_name, raw_predicate in value.items():
        name = logical_name(raw_name, "query field")
        if not isinstance(raw_predicate, Mapping):
            output[name] = json_value(raw_predicate, "query predicate")
            continue
        if not raw_predicate or set(raw_predicate) - _OPERATORS:
            raise InvalidCost("query predicate contains an unsupported operator")
        operators: dict[str, object] = {}
        for raw_operator, candidate in raw_predicate.items():
            operator = cast(str, raw_operator)
            if operator in {"in", "notIn"}:
                if (
                    not isinstance(candidate, Sequence)
                    or isinstance(candidate, str | bytes | bytearray)
                    or not 1 <= len(candidate) <= 100
                ):
                    raise InvalidCost("membership predicates require 1 to 100 values")
                operators[operator] = tuple(json_value(item) for item in candidate)
            elif operator == "isNull":
                if not isinstance(candidate, bool):
                    raise InvalidCost("isNull requires a boolean")
                operators[operator] = candidate
            else:
                operators[operator] = json_value(candidate)
        output[name] = MappingProxyType(dict(sorted(operators.items())))
    return MappingProxyType(dict(sorted(output.items())))


def _predicate(value: Mapping[str, object]) -> ValueExpression | None:
    expressions: list[ValueExpression] = []
    for name, selected in sorted(value.items()):
        operand = field(name)
        if not isinstance(selected, Mapping):
            expressions.append(operand.eq(selected))
            continue
        for operator, candidate in sorted(selected.items()):
            if operator == "in":
                expressions.append(operand.in_(cast(Sequence[object], candidate)))
            elif operator == "notIn":
                included = operand.in_(cast(Sequence[object], candidate))
                expressions.append(type(included)(included.operand, included.values, True))
            elif operator == "isNull":
                expressions.append(operand.is_null(cast(bool, candidate)))
            else:
                expressions.append(getattr(operand, operator)(candidate))
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return BooleanExpression("and", tuple(expressions))


def _items(data: object, limit: int) -> tuple[Mapping[str, object], ...]:
    selected = data
    if isinstance(data, Mapping):
        selected = next((data[key] for key in ("items", "records", "data") if key in data), data)
    if not isinstance(selected, Sequence) or isinstance(selected, str | bytes | bytearray):
        raise InvalidCostResult("Cost query returned an invalid record collection")
    if len(selected) > limit:
        raise InvalidCostResult("Cost query exceeded its requested page size")
    result: list[Mapping[str, object]] = []
    for item in selected:
        if not isinstance(item, Mapping):
            raise InvalidCostResult("Cost query returned a non-record item")
        result.append(MappingProxyType(dict(item)))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CostQueryResult:
    items: tuple[Mapping[str, object], ...]
    cursor: str | None
    operation_result: OperationResult

    @property
    def provenance(self) -> Mapping[str, str]:
        return cast(Mapping[str, str], self.operation_result.provenance)


@dataclass(frozen=True, slots=True)
class CostQuery:
    _executor: MeridianExecutor
    resource: ResourceRef
    where: Mapping[str, object] = dataclass_field(default_factory=dict)
    select: tuple[str, ...] = ()
    order_by: tuple[CostOrder, ...] = ()
    limit: int = 100
    cursor: str | None = None

    def __post_init__(self) -> None:
        try:
            resource = ResourceRef.parse(self.resource, catalog="structured")
        except (TypeError, ValueError) as exc:
            raise InvalidCost("Cost queries require a logical structured Resource") from exc
        object.__setattr__(self, "resource", resource)
        object.__setattr__(self, "where", _where(self.where))
        selected = tuple(logical_name(item, "selected field") for item in self.select)
        if len(set(selected)) != len(selected):
            raise InvalidCost("selected fields must be unique")
        object.__setattr__(self, "select", selected)
        order = tuple(self.order_by)
        if any(not isinstance(item, CostOrder) for item in order):
            raise InvalidCost("query ordering must contain CostOrder values")
        object.__setattr__(self, "order_by", order)
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= _MAX_PAGE_SIZE
        ):
            raise InvalidCost(f"query page size must be between 1 and {_MAX_PAGE_SIZE}")
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not self.cursor or len(self.cursor) > 4096
        ):
            raise InvalidCost("query cursor must be an opaque bounded token")

    @property
    def expression(self) -> Expression:
        return StructuredCatalogSurface().query(
            resource=self.resource.to_dict(),
            where=self.where,
            select=self.select,
            order_by=tuple(item.to_dict() for item in self.order_by),
            limit=self.limit,
            cursor=self.cursor,
        )

    @property
    def logical_plan(self) -> QueryOperation:
        return QueryOperation(
            catalog="structured",
            targets=(QueryTarget(self.resource),),
            operation="scan",
            result=ResultSpec("records", tuple(Projection(field(item)) for item in self.select)),
            filter=_predicate(self.where),
            order=tuple(Sort(field(item.field), item.direction) for item in self.order_by),
            page=PageSpec(self.limit, self.cursor),
            consistency="eventual",
            budget=SafetyBudget(max_result_values=self.limit),
            extensions={"org.meridian.cost/query": "1.0.0"},
        )

    @property
    def fingerprint(self) -> str:
        return self.logical_plan.fingerprint

    def page(self, *, limit: int | None = None, cursor: str | None = None) -> CostQuery:
        return replace(self, limit=self.limit if limit is None else limit, cursor=cursor)

    def selecting(self, *fields: str) -> CostQuery:
        return replace(self, select=tuple(fields))

    def execute(self) -> CostQueryResult:
        result = self._executor.execute(self.expression)
        cursor: str | None = None
        if isinstance(result.data, Mapping):
            candidate = result.data.get("cursor", result.data.get("nextCursor"))
            if candidate is not None and not isinstance(candidate, str):
                raise InvalidCostResult("Cost query returned an invalid cursor")
            cursor = candidate
        return CostQueryResult(_items(result.data, self.limit), cursor, result)


@dataclass(frozen=True, slots=True)
class CostQueries:
    _executor: MeridianExecutor
    rate_cards_resource: ResourceRef
    calculations_resource: ResourceRef
    records_resource: ResourceRef

    def rate_cards(self, *, where: Mapping[str, object] | None = None) -> CostQuery:
        return CostQuery(
            self._executor,
            self.rate_cards_resource,
            where or {},
            order_by=(
                CostOrder("rateCardId"),
                CostOrder("rateCardVersion"),
                CostOrder("rateCardRevision"),
            ),
        )

    def calculations(
        self,
        scope: UsageScope | Mapping[object, object],
        start: datetime,
        end: datetime,
        *,
        where: Mapping[str, object] | None = None,
    ) -> CostQuery:
        return self._scoped(
            self.calculations_resource,
            scope,
            start,
            end,
            "createdAt",
            (CostOrder("createdAt"), CostOrder("calculationId"), CostOrder("calculationRevision")),
            where,
        )

    def records(
        self,
        scope: UsageScope | Mapping[object, object],
        start: datetime,
        end: datetime,
        *,
        where: Mapping[str, object] | None = None,
    ) -> CostQuery:
        return self._scoped(
            self.records_resource,
            scope,
            start,
            end,
            "occurredAt",
            (CostOrder("occurredAt"), CostOrder("costId")),
            where,
        )

    def _scoped(
        self,
        resource: ResourceRef,
        scope: UsageScope | Mapping[object, object],
        start: datetime,
        end: datetime,
        time_field: str,
        order: tuple[CostOrder, ...],
        where: Mapping[str, object] | None,
    ) -> CostQuery:
        selected_scope = UsageScope.parse(scope)
        selected_start = utc_datetime(start, "query start")
        selected_end = utc_datetime(end, "query end")
        if selected_start >= selected_end:
            raise InvalidCost("Cost query intervals must be non-empty and half-open")
        predicates = dict(where or {})
        if {"scopeFingerprint", time_field} & set(predicates):
            raise InvalidCost("scope and time predicates are derived inputs")
        predicates["scopeFingerprint"] = selected_scope.fingerprint
        predicates[time_field] = {
            "gte": iso_datetime(selected_start),
            "lt": iso_datetime(selected_end),
        }
        return CostQuery(self._executor, resource, predicates, order_by=order)


__all__ = ["CostOrder", "CostQueries", "CostQuery", "CostQueryResult", "MeridianExecutor"]
