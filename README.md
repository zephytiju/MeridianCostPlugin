<!-- SPDX-License-Identifier: Apache-2.0 -->
# meridian-plugin-cost

Meridian V1's open-source Cost plugin provides immutable rate cards, exact Decimal pricing,
deterministic allocation, versioned calculations, cost records, and reproducible explanations
over logical Meridian Resources.

This distribution is a Python library and Meridian plugin. It is not a service, a Cost Catalog,
or an owner of a product-private database. It never opens Usage storage: normalized aggregates
are obtained through the released `meridian-plugin-usage==1.0.2` public API. Cost data targets
distinct `structured/cost/*` Resources; deployment IaC owns their physical placement and keeps
Usage and Cost storage isolated.

## Install

```console
python -m pip install meridian-plugin-cost==1.0.1
```

Python 3.12, 3.13, and 3.14 are supported. Runtime Meridian dependencies remain pinned to their
compatible 1.0.0 releases, while the official Usage distribution is pinned to 1.0.2.

## Define and publish a rate card

```python
from datetime import UTC, datetime
from decimal import Decimal

from meridian_storage.plugins.cost import (
    CostRepository,
    PricingModel,
    PublicationState,
    RateCardV1,
    RoundingPolicy,
)

cost = CostRepository(ready_meridian)
draft = RateCardV1(
    rate_card_id="cloud.api.requests",
    version=1,
    revision=1,
    provider="example-cloud",
    product="api",
    meter_id="api.requests",
    meter_version=1,
    currency="USD",
    effective_start=datetime(2026, 1, 1, tzinfo=UTC),
    effective_end=datetime(2027, 1, 1, tzinfo=UTC),
    pricing_model=PricingModel.FLAT_UNIT,
    unit_price=Decimal("0.0025"),
    rounding=RoundingPolicy(currency_scale=2, intermediate_scale=8),
    state=PublicationState.DRAFT,
    created_at=datetime(2026, 8, 26, tzinfo=UTC),
    provenance={"source": "provider-rate-sheet-2026"},
)
cost.create_rate_card(draft)
validated = cost.validate_rate_card(draft.rate_card_id, 1, expected_revision=1)
published = cost.publish_rate_card(
    draft.rate_card_id,
    1,
    expected_revision=validated.revision,
)
```

Lifecycle changes append immutable snapshots. Draft pricing can be replaced with optimistic
revision checks; once validated, pricing is frozen. A correction uses a new rate-card version and
an explicit supersession reference. Physical deletion is deliberately unsupported; retirement is
the V1 delete semantic.

## Calculate from released Usage aggregates

```python
from meridian_storage.plugins.cost import CostCalculator, RepositoryUsageProvider

usage_provider = RepositoryUsageProvider(released_usage.repository)
calculator = CostCalculator(usage_provider, cost)
result = calculator.calculate(
    scope={"tenant": "acme", "environment": "prod"},
    start=start,
    end=end,
    rate_card=published.ref,
    occurred_at=end,
)
```

The calculation requires closed Usage aggregates and records their exact aggregate versions,
fingerprints, rate-card fingerprint, allocation fingerprint, calculator version, Decimal rounding
stages, explanation tree, and supersession chain. Identical inputs replay the existing result;
changed inputs create a new calculation revision.

## Contracts and evidence

The wheel ships Cost Schemas, plugin/schema-provider entry points, a Usage compatibility matrix,
locked design revisions, JSON Schema compatibility evidence, and deterministic golden values.
Optional `MeridianEvidenceSink` emits audit and lineage records through the registered `evidence`
Catalog public Expression surface.

See [architecture](docs/architecture.md), [rate cards](docs/rate-cards.md),
[calculation](docs/calculation.md), [integration](docs/integration.md), and
[conformance](docs/conformance.md).

Licensed under the Apache License, Version 2.0.
