# SPDX-License-Identifier: Apache-2.0
"""Closed validation and canonical serialization helpers for Cost V1."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast

from .errors import DecimalOverflow, InvalidCost

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@#-]*$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def bounded_text(value: object, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise InvalidCost(f"{name} must be a string")
    selected = unicodedata.normalize("NFC", value).strip()
    if not selected or len(selected.encode("utf-8")) > maximum:
        raise InvalidCost(f"{name} must contain 1 to {maximum} UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in selected):
        raise InvalidCost(f"{name} cannot contain control characters")
    return selected


def token(value: object, name: str, *, maximum: int = 256) -> str:
    selected = bounded_text(value, name, maximum=maximum)
    if not _TOKEN.fullmatch(selected):
        raise InvalidCost(f"{name} must be a stable token")
    return selected


def logical_name(value: object, name: str) -> str:
    selected = bounded_text(value, name, maximum=128)
    if not _NAME.fullmatch(selected):
        raise InvalidCost(f"{name} must be a logical name")
    return selected


def currency_code(value: object) -> str:
    selected = bounded_text(value, "currency", maximum=3).upper()
    if len(selected) != 3 or not selected.isascii() or not selected.isalpha():
        raise InvalidCost("currency must be a three-letter ISO-style code")
    return selected


def utc_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidCost(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def iso_datetime(value: datetime) -> str:
    return utc_datetime(value, "datetime").isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_datetime(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        return utc_datetime(value, name)
    if not isinstance(value, str):
        raise InvalidCost(f"{name} must be an RFC 3339 timestamp")
    try:
        return utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as exc:
        raise InvalidCost(f"{name} must be an RFC 3339 timestamp") from exc


def decimal_value(value: object, name: str = "decimal") -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | str | int):
        raise InvalidCost(f"{name} must be an exact decimal")
    try:
        selected = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidCost(f"{name} must be an exact decimal") from exc
    if not selected.is_finite():
        raise InvalidCost(f"{name} must be finite")
    return fit_decimal(selected, name)


def fit_decimal(value: Decimal, name: str = "decimal") -> Decimal:
    normalized = value if value.is_zero() else value.normalize()
    exponent = cast(int, normalized.as_tuple().exponent)
    fractional_digits = max(0, -exponent)
    integer_digits = 1 if normalized.is_zero() else max(1, normalized.adjusted() + 1)
    if fractional_digits > 18 or integer_digits > 58:
        raise DecimalOverflow(f"{name} must fit the released Decimal(76, 18) Cost schema")
    return value


def decimal_text(value: Decimal) -> str:
    selected = decimal_value(value)
    text = format(selected, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def string_map(
    value: object,
    name: str,
    *,
    maximum_entries: int = 32,
    value_maximum: int = 512,
) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or len(value) > maximum_entries:
        raise InvalidCost(f"{name} must be a mapping with at most {maximum_entries} entries")
    selected: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = logical_name(raw_key, f"{name} key")
        if key in selected:
            raise InvalidCost(f"{name} keys must be unique after normalization")
        selected[key] = bounded_text(raw_value, f"{name}[{key}]", maximum=value_maximum)
    return MappingProxyType(dict(sorted(selected.items())))


def json_value(value: object, name: str = "value") -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        raise InvalidCost(f"{name} cannot contain floating-point values")
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return iso_datetime(value)
    if isinstance(value, Mapping):
        return {
            bounded_text(key, f"{name} key", maximum=256): json_value(item, name)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_value(item, name) for item in value]
    raise InvalidCost(f"{name} must contain only canonical JSON values")


def immutable_json_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidCost(f"{name} must be a mapping")
    normalized = json_value(value, name)
    if not isinstance(normalized, dict):
        raise AssertionError("mapping normalization changed the root type")
    return cast(Mapping[str, object], _freeze_json(normalized))


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def canonical_json(value: object) -> bytes:
    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def require_fingerprint(value: object, name: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise InvalidCost(f"{name} must be a sha256 fingerprint")
    return value
