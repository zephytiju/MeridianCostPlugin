# SPDX-License-Identifier: Apache-2.0
"""Audit and lineage emission through the registered Evidence Catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from meridian_storage import OperationResult, ResourceRef
from meridian_storage.evidence import (
    AuditOutcome,
    AuditRecord,
    EvidenceCatalogSurface,
    EvidenceMetadata,
    EvidenceProfile,
    EvidenceReference,
    LineageRecord,
    LineageStatus,
)

from ._canonical import fingerprint
from .models import CalculationV1, CostRecordV1
from .query import MeridianExecutor


class CostEvidenceSink(Protocol):
    def emit(
        self,
        calculation: CalculationV1,
        records: tuple[CostRecordV1, ...],
    ) -> CostEvidenceReceipt: ...


@dataclass(frozen=True, slots=True)
class CostEvidenceResources:
    audit: ResourceRef = field(default_factory=lambda: ResourceRef("evidence", "cost", "audit"))
    lineage: ResourceRef = field(default_factory=lambda: ResourceRef("evidence", "cost", "lineage"))

    def __post_init__(self) -> None:
        for name in ("audit", "lineage"):
            selected = ResourceRef.parse(getattr(self, name), catalog="evidence")
            object.__setattr__(self, name, selected)
        if self.audit == self.lineage:
            raise ValueError("Cost audit and lineage Resources must be distinct")


@dataclass(frozen=True, slots=True)
class CostEvidenceReceipt:
    audit: OperationResult
    lineage: OperationResult


class MeridianEvidenceSink:
    """Append deterministic typed evidence without selecting an Adapter or Engine."""

    def __init__(
        self,
        executor: MeridianExecutor,
        resources: CostEvidenceResources | None = None,
    ) -> None:
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must implement Meridian.execute(Expression)")
        self._executor = executor
        self.resources = resources or CostEvidenceResources()
        self._surface = EvidenceCatalogSurface()

    def emit(
        self,
        calculation: CalculationV1,
        records: tuple[CostRecordV1, ...],
    ) -> CostEvidenceReceipt:
        if not isinstance(calculation, CalculationV1) or any(
            not isinstance(item, CostRecordV1) for item in records
        ):
            raise TypeError("calculation and records must be immutable Cost V1 models")
        calculation_ref = EvidenceReference(
            "cost.calculation",
            calculation.calculation_id,
            str(calculation.revision),
            calculation.fingerprint,
        )
        metadata = EvidenceMetadata(
            evidence_id=fingerprint(
                {"kind": "cost-calculation", "version": calculation.version_id}
            ),
            event_time=calculation.created_at,
            observed_time=calculation.created_at,
            scope=calculation.scope.to_dict(),
            subject=calculation_ref,
            attributes={
                "currency": calculation.currency,
                "calculatorVersion": calculation.calculator_version,
            },
            provenance={"plugin": "meridian-storage-plugin-cost", "version": "1.0.1"},
        )
        audit = AuditRecord(
            action="cost.calculation.persisted",
            outcome=AuditOutcome.SUCCESS,
            operation=calculation_ref,
            changes={
                "calculationVersionId": calculation.version_id,
                "rateCard": calculation.rate_card.identity,
                "usageFingerprint": calculation.usage_fingerprint,
                "recordCount": len(records),
            },
            policy_labels=("immutable", "cost"),
            metadata=metadata,
        )
        inputs = (
            *(EvidenceReference("usage.aggregate", value) for value in calculation.aggregate_refs),
            EvidenceReference(
                "cost.rate-card",
                calculation.rate_card.identity,
                digest=calculation.rate_card.pricing_fingerprint,
            ),
        )
        outputs = (
            calculation_ref,
            *(
                EvidenceReference("cost.record", item.cost_id, digest=item.fingerprint)
                for item in records
            ),
        )
        lineage = LineageRecord(
            activity="cost.calculate",
            status=LineageStatus.COMPLETED,
            execution_id=calculation.version_id,
            inputs=inputs,
            outputs=outputs,
            schema_versions={"cost": "1.0.0", "usage": "1.0.0"},
            model_versions={"calculator": calculation.calculator_version},
            source_revisions={
                "usage": calculation.usage_fingerprint,
                "rateCard": calculation.rate_card.pricing_fingerprint,
            },
            transformation_digest=calculation.calculation_identity,
            context_scope=calculation.scope.to_dict(),
            metadata_values={
                "windowStart": calculation.window.to_dict()["start"],
                "windowEnd": calculation.window.to_dict()["end"],
            },
            metadata=metadata,
        )
        audit_result = self._executor.execute(
            self._surface.append(
                resource=self.resources.audit,
                data=audit,
                profile=EvidenceProfile.AUDIT,
                idempotency_key=f"cost-audit-{calculation.version_id}",
            )
        )
        lineage_result = self._executor.execute(
            self._surface.append(
                resource=self.resources.lineage,
                data=lineage,
                profile=EvidenceProfile.LINEAGE,
                idempotency_key=f"cost-lineage-{calculation.version_id}",
            )
        )
        return CostEvidenceReceipt(audit_result, lineage_result)


__all__ = [
    "CostEvidenceReceipt",
    "CostEvidenceResources",
    "CostEvidenceSink",
    "MeridianEvidenceSink",
]
