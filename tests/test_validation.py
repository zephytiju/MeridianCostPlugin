# SPDX-License-Identifier: Apache-2.0
"""Fail-closed canonicalization, error-envelope, and query-shape tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conftest import MemoryExecutor, make_fingerprint
from meridian_storage import ResourceRef
from meridian_storage.plugins.cost import (
    CostConflict,
    CurrencyMismatch,
    DecimalOverflow,
    InvalidCost,
    InvalidCostResult,
    MissingRateCard,
    OverlappingRateCard,
    StaleRateCardRevision,
    UnitMismatch,
    UsageDependencyFailure,
)
from meridian_storage.plugins.cost._canonical import (
    bounded_text,
    canonical_json,
    currency_code,
    decimal_text,
    decimal_value,
    fingerprint,
    immutable_json_mapping,
    iso_datetime,
    json_value,
    logical_name,
    parse_datetime,
    require_fingerprint,
    string_map,
    token,
    utc_datetime,
)
from meridian_storage.plugins.cost.query import CostOrder, CostQuery, _items


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: bounded_text(1, "value"), "string"),
        (lambda: bounded_text("", "value"), "UTF-8"),
        (lambda: bounded_text("bad\nvalue", "value"), "control"),
        (lambda: token("has space", "value"), "stable token"),
        (lambda: logical_name("has/slash", "value"), "logical name"),
        (lambda: currency_code("US"), "three-letter"),
        (lambda: utc_datetime(datetime(2026, 1, 1), "at"), "timezone-aware"),
        (lambda: parse_datetime(42, "at"), "RFC 3339"),
        (lambda: parse_datetime("not-a-date", "at"), "RFC 3339"),
        (lambda: decimal_value(True), "exact decimal"),
        (lambda: decimal_value("not-decimal"), "exact decimal"),
        (lambda: decimal_value("Infinity"), "finite"),
        (lambda: decimal_value("0.0000000000000000001"), r"Decimal\(76, 18\)"),
        (lambda: string_map([], "metadata"), "mapping"),
        (lambda: json_value(0.5), "floating-point"),
        (lambda: json_value(object()), "canonical JSON"),
        (lambda: immutable_json_mapping([], "metadata"), "mapping"),
        (lambda: require_fingerprint("sha256:nope", "digest"), "sha256 fingerprint"),
    ],
)
def test_canonical_validation_rejects_ambiguous_values(call, message: str) -> None:
    with pytest.raises(InvalidCost, match=message):
        call()


def test_canonical_serialization_is_stable_and_deeply_immutable() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    value = {
        "decimal": Decimal("1.2300"),
        "time": moment,
        "nested": [True, None, {"key": "value"}],
    }
    assert decimal_text(Decimal("-0.00")) == "0"
    assert iso_datetime(moment) == "2026-01-01T00:00:00.000000Z"
    assert parse_datetime(iso_datetime(moment), "at") == moment
    assert json_value(value)["decimal"] == "1.23"  # type: ignore[index]
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert fingerprint({"a": 1}) == fingerprint({"a": 1})
    assert require_fingerprint(make_fingerprint("ok"), "digest").startswith("sha256:")
    frozen = immutable_json_mapping(value, "metadata")
    assert isinstance(frozen["nested"], tuple)
    with pytest.raises(TypeError):
        frozen["new"] = "value"  # type: ignore[index]


@pytest.mark.parametrize(
    "error",
    [
        InvalidCost("invalid", requirement="cost.test", resource_ref="cost:test"),
        MissingRateCard("card@1"),
        OverlappingRateCard("card@2", "card@1"),
        StaleRateCardRevision("card@1"),
        CostConflict("record-1"),
        UsageDependencyFailure("usage unavailable", resource_ref="meter@1"),
        UnitMismatch("byte", "request"),
        CurrencyMismatch("EUR", "USD"),
        DecimalOverflow("too precise"),
        InvalidCostResult("bad result"),
    ],
)
def test_public_errors_include_stable_requirement(error) -> None:
    payload = error.to_dict()
    assert payload["code"].startswith("MERIDIAN_COST_")
    assert payload["requirement"].startswith("cost.")
    assert "message" in payload
    with pytest.raises(TypeError, match="unsupported"):
        InvalidCost("bad", unexpected="must-not-leak")


def test_query_validation_and_result_shape_fail_closed(executor: MemoryExecutor) -> None:
    resource = ResourceRef("structured", "cost", "records")
    with pytest.raises(InvalidCost, match="membership"):
        CostQuery(executor, resource, {"costId": {"in": []}})
    with pytest.raises(InvalidCost, match="isNull"):
        CostQuery(executor, resource, {"costId": {"isNull": "yes"}})
    with pytest.raises(InvalidCost, match="unique"):
        CostQuery(executor, resource, select=("costId", "costId"))
    with pytest.raises(InvalidCost, match="ordering"):
        CostQuery(executor, resource, order_by=(object(),))  # type: ignore[arg-type]
    with pytest.raises(InvalidCost, match="cursor"):
        CostQuery(executor, resource, cursor="")
    with pytest.raises(InvalidCost, match="logical structured"):
        CostQuery(executor, ResourceRef("object", "cost", "records"))
    with pytest.raises(InvalidCostResult, match="collection"):
        _items("bad", 1)
    with pytest.raises(InvalidCostResult, match="page size"):
        _items([{}, {}], 1)
    with pytest.raises(InvalidCostResult, match="non-record"):
        _items([1], 1)
    query = CostQuery(executor, resource, {"costId": {"notIn": ["x"], "isNull": False}})
    assert query.selecting("costId").select == ("costId",)
    assert CostOrder("costId").to_dict() == {"field": "costId", "direction": "asc"}
