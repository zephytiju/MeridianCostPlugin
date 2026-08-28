# SPDX-License-Identifier: Apache-2.0
"""Cost plugin factory and composition facade."""

from __future__ import annotations

from meridian_storage import Meridian, RuntimeState
from meridian_storage.plugins.usage import UsageRepository
from meridian_storage.spi import PluginManifest

from ._version import __version__
from .calculator import CostCalculator
from .errors import InvalidCost
from .evidence import CostEvidenceSink
from .repository import CostRepository, CostResources
from .usage import UsageInputProvider


class Cost:
    """Ready-runtime Cost repository; callers inject Usage when creating a calculator."""

    def __init__(
        self,
        meridian: Meridian,
        *,
        resources: CostResources | None = None,
    ) -> None:
        if not isinstance(meridian, Meridian) or meridian.state is not RuntimeState.READY:
            raise InvalidCost(
                "Cost requires a ready Meridian runtime",
                requirement="cost.runtime.ready",
            )
        self.repository = CostRepository(meridian, resources)
        self.queries = self.repository.queries

    def calculator(
        self,
        usage: UsageInputProvider | UsageRepository,
        *,
        evidence: CostEvidenceSink | None = None,
    ) -> CostCalculator:
        if isinstance(usage, UsageRepository):
            self.repository.resources.assert_usage_isolation(usage.resources)
        return CostCalculator(usage, self.repository, evidence=evidence)


class CostPluginFactory:
    @property
    def plugin_id(self) -> str:
        return "cost"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            plugin_version=__version__,
            plugin_contract_version="1.0.0",
            core_contract="1.x",
            extensions={
                "distribution": "meridian-storage-plugin-cost",
                "repository": "zephytiju/MeridianCostPlugin",
                "catalog": "structured",
                "evidenceCatalog": "evidence",
                "usageDependency": "meridian-plugin-usage==1.0.2",
                "service": "false",
                "privateDatabase": "false",
                "nativeQuery": "false",
                "engineAuthority": "false",
            },
        )

    def create(self, meridian: Meridian) -> Cost:
        return Cost(meridian)


__all__ = ["Cost", "CostPluginFactory"]
