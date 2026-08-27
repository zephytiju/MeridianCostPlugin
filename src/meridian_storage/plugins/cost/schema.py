# SPDX-License-Identifier: Apache-2.0
"""Released Semantics schemas and Resource bundle for Cost V1."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from meridian_storage.registry.resources import (
    CapabilityRequirement,
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
)
from meridian_storage.semantics import (
    PROFILE_EXTENSION_KEY,
    CatalogName,
    FieldDefinition,
    FrozenJson,
    IndexDefinition,
    LogicalKind,
    LogicalType,
    RelationalProfile,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
    TimeSeriesProfile,
)

from ._version import __version__
from .repository import CostResources

_CONTRACT_VERSION = "1.0.0"
_DECIMAL = LogicalType(LogicalKind.DECIMAL, precision=76, scale=18)


def _field(
    name: str, kind: LogicalKind | LogicalType, *, nullable: bool = False
) -> FieldDefinition:
    return FieldDefinition(
        name,
        kind if isinstance(kind, LogicalType) else LogicalType(kind),
        nullable=nullable,
        mutable=False,
    )


def _document(
    name: str,
    fields: Iterable[FieldDefinition],
    identity: tuple[str, ...],
    profile: RelationalProfile | TimeSeriesProfile,
    *,
    indexes: tuple[IndexDefinition, ...],
) -> SchemaDocument:
    return SchemaDocument(
        ref=SchemaReference(CatalogName.STRUCTURED, "cost", name, _CONTRACT_VERSION),
        semantic_kind=SemanticKind(profile.kind),
        fields=tuple(fields),
        identity=identity,
        indexes=indexes,
        consistency="strong",
        retention_label="cost-immutable",
        extensions=cast(
            Mapping[str, FrozenJson],
            {
                PROFILE_EXTENSION_KEY: profile.to_dict(),
                "org.meridian.cost/contract": _CONTRACT_VERSION,
                "org.meridian.cost/physical-isolation": "usage",
            },
        ),
        compatibility={"mode": "backward"},
    )


def rate_card_schema() -> SchemaDocument:
    return _document(
        "rate_cards",
        (
            _field("schemaVersion", LogicalKind.STRING),
            _field("rateCardRevisionId", LogicalKind.STRING),
            _field("rateCardId", LogicalKind.STRING),
            _field("rateCardVersion", LogicalKind.INT64),
            _field("rateCardRevision", LogicalKind.INT64),
            _field("provider", LogicalKind.STRING),
            _field("product", LogicalKind.STRING),
            _field("meterId", LogicalKind.STRING),
            _field("meterVersion", LogicalKind.INT64),
            _field("currency", LogicalKind.STRING),
            _field("effectiveStart", LogicalKind.UTC_TIMESTAMP),
            _field("effectiveEnd", LogicalKind.UTC_TIMESTAMP),
            _field("pricingModel", LogicalKind.STRING),
            _field("unitPrice", _DECIMAL, nullable=True),
            _field("tiers", LogicalKind.JSON),
            _field("minimumCharge", _DECIMAL, nullable=True),
            _field("commitmentCredit", LogicalKind.JSON, nullable=True),
            _field("matchingDimensions", LogicalKind.JSON),
            _field("rounding", LogicalKind.JSON),
            _field("provenance", LogicalKind.JSON),
            _field("pricingKey", LogicalKind.STRING),
            _field("pricingFingerprint", LogicalKind.STRING),
            _field("state", LogicalKind.STRING),
            _field("createdAt", LogicalKind.UTC_TIMESTAMP),
            _field("publishedAt", LogicalKind.UTC_TIMESTAMP, nullable=True),
            _field("supersedes", LogicalKind.JSON, nullable=True),
            _field("fingerprint", LogicalKind.STRING),
        ),
        ("rateCardRevisionId",),
        RelationalProfile(unique_fields=(("rateCardRevisionId",),)),
        indexes=(
            IndexDefinition("rate-card-snapshot", "hash", ("rateCardRevisionId",), True),
            IndexDefinition(
                "rate-card-version",
                "btree",
                ("rateCardId", "rateCardVersion", "rateCardRevision"),
                True,
            ),
            IndexDefinition("pricing-key", "hash", ("pricingKey",)),
        ),
    )


def calculation_schema() -> SchemaDocument:
    return _document(
        "calculations",
        (
            _field("schemaVersion", LogicalKind.STRING),
            _field("calculationId", LogicalKind.STRING),
            _field("calculationRevision", LogicalKind.INT64),
            _field("calculationVersionId", LogicalKind.STRING),
            _field("calculationIdentity", LogicalKind.STRING),
            _field("scope", LogicalKind.JSON),
            _field("scopeFingerprint", LogicalKind.STRING),
            _field("windowStart", LogicalKind.UTC_TIMESTAMP),
            _field("windowEnd", LogicalKind.UTC_TIMESTAMP),
            _field("usageFingerprint", LogicalKind.STRING),
            _field("aggregateRefs", LogicalKind.JSON),
            _field("rateCard", LogicalKind.JSON),
            _field("allocationFingerprint", LogicalKind.STRING),
            _field("calculatorVersion", LogicalKind.STRING),
            _field("quantity", _DECIMAL),
            _field("unit", LogicalKind.STRING),
            _field("currency", LogicalKind.STRING),
            _field("preAdjustmentAmount", _DECIMAL),
            _field("adjustmentTotal", _DECIMAL),
            _field("finalAmount", _DECIMAL),
            _field("explanation", LogicalKind.JSON),
            _field("createdAt", LogicalKind.UTC_TIMESTAMP),
            _field("recordIds", LogicalKind.JSON),
            _field("supersedes", LogicalKind.STRING, nullable=True),
            _field("fingerprint", LogicalKind.STRING),
        ),
        ("calculationVersionId",),
        RelationalProfile(unique_fields=(("calculationVersionId",), ("calculationIdentity",))),
        indexes=(
            IndexDefinition("calculation-version", "hash", ("calculationVersionId",), True),
            IndexDefinition("calculation-identity", "hash", ("calculationIdentity",), True),
            IndexDefinition("calculation-created", "btree", ("createdAt",)),
        ),
    )


def cost_record_schema() -> SchemaDocument:
    profile = TimeSeriesProfile(
        timestamp_field="occurredAt",
        series_identity=("costId",),
        dimensions=(
            "scopeFingerprint",
            "currency",
            "allocationFingerprint",
        ),
        measurements=("quantity", "preAdjustmentAmount", "finalAmount"),
    )
    return _document(
        "records",
        (
            _field("schemaVersion", LogicalKind.STRING),
            _field("costId", LogicalKind.STRING),
            _field("calculationId", LogicalKind.STRING),
            _field("calculationRevision", LogicalKind.INT64),
            _field("calculationVersionId", LogicalKind.STRING),
            _field("scope", LogicalKind.JSON),
            _field("scopeFingerprint", LogicalKind.STRING),
            _field("windowStart", LogicalKind.UTC_TIMESTAMP),
            _field("windowEnd", LogicalKind.UTC_TIMESTAMP),
            _field("usageFingerprint", LogicalKind.STRING),
            _field("aggregateRefs", LogicalKind.JSON),
            _field("rateCard", LogicalKind.JSON),
            _field("quantity", _DECIMAL),
            _field("unit", LogicalKind.STRING),
            _field("currency", LogicalKind.STRING),
            _field("preAdjustmentAmount", _DECIMAL),
            _field("adjustments", LogicalKind.JSON),
            _field("finalAmount", _DECIMAL),
            _field("allocationDimensions", LogicalKind.JSON),
            _field("allocationFingerprint", LogicalKind.STRING),
            _field("occurredAt", LogicalKind.UTC_TIMESTAMP),
            _field("supersedes", LogicalKind.STRING, nullable=True),
            _field("lineage", LogicalKind.JSON),
            _field("fingerprint", LogicalKind.STRING),
        ),
        ("costId",),
        profile,
        indexes=(
            IndexDefinition("cost-id", "hash", ("costId",), True),
            IndexDefinition("cost-calculation", "hash", ("calculationVersionId",)),
            IndexDefinition("cost-occurred", "time-series", ("occurredAt",)),
        ),
    )


def cost_schemas() -> tuple[SchemaDocument, ...]:
    return rate_card_schema(), calculation_schema(), cost_record_schema()


def _requirements(*methods: str) -> tuple[CapabilityRequirement, ...]:
    return tuple(CapabilityRequirement(f"meridian.structured.{item}", "1.0.0") for item in methods)


class CostSchemaProvider:
    @property
    def provider_id(self) -> str:
        return "cost"

    @property
    def provider_contract_version(self) -> str:
        return _CONTRACT_VERSION

    def load(self) -> ResourceBundle:
        resources = CostResources()
        documents = cost_schemas()
        definitions = tuple(item.to_core_definition() for item in documents)
        logical_resources = (
            ResourceDefinition(
                resources.rate_cards,
                "relational",
                definitions[0].ref,
                labels={"plugin": "cost", "recordType": "rate-card"},
                requirements=_requirements("get", "put", "query"),
                related_resources=(resources.calculations, resources.records),
            ),
            ResourceDefinition(
                resources.calculations,
                "relational",
                definitions[1].ref,
                labels={"plugin": "cost", "recordType": "calculation"},
                requirements=_requirements("get", "put", "query"),
                related_resources=(resources.rate_cards, resources.records),
            ),
            ResourceDefinition(
                resources.records,
                "cost",
                definitions[2].ref,
                labels={"plugin": "cost", "recordType": "cost-record"},
                requirements=_requirements("get", "put", "query"),
                related_resources=(resources.rate_cards, resources.calculations),
            ),
        )
        return ResourceBundle(
            provider_id=self.provider_id,
            provider_version=__version__,
            provider_contract_version=self.provider_contract_version,
            namespaces=(
                NamespaceDefinition(
                    "structured",
                    "cost",
                    labels={
                        "plugin": "cost",
                        "lifecycleOwner": "platform",
                        "physicalIsolation": "usage",
                    },
                ),
            ),
            schemas=definitions,
            resources=logical_resources,
            extensions={
                "distribution": "meridian-plugin-cost",
                "catalog": "structured",
                "usageDependency": "meridian-plugin-usage==1.0.0",
                "design": {
                    "hldRevision": 56,
                    "catalogRevision": 70,
                    "adapterRevision": 24,
                    "kafkaStreamingRevision": 6,
                    "constructsRevision": 45,
                    "costLldRevision": 16,
                },
            },
        )


__all__ = [
    "CostSchemaProvider",
    "calculation_schema",
    "cost_record_schema",
    "cost_schemas",
    "rate_card_schema",
]
