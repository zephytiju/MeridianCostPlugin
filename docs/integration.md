<!-- SPDX-License-Identifier: Apache-2.0 -->
# Integration guide

Install the exact V1 distribution:

```console
python -m pip install meridian-storage-plugin-cost==1.0.1
```

Meridian discovers `CostPluginFactory` and `CostSchemaProvider` from package entry points. The
schema provider publishes a `ResourceBundle`; deployment configuration must bind its three
logical `structured:cost.*` Resources to suitable pre-provisioned capabilities.

## In-process Usage

```python
from meridian_storage.plugins.cost import CostCalculator, CostRepository
from meridian_storage.plugins.usage import UsageRepository

cost = CostRepository(ready_meridian)
usage = UsageRepository(ready_meridian)
calculator = CostCalculator(usage, cost)
```

Passing `UsageRepository` automatically wraps it in `RepositoryUsageProvider`. Cost invokes only
the released public meter and aggregate APIs. `CostResources.assert_usage_isolation()` can be
used explicitly during composition and is also applied by the ready-runtime `Cost` facade.

## Authorized service boundary

A process that cannot install Usage locally may provide an object implementing:

```python
class UsageInputProvider(Protocol):
    def fetch(
        self,
        card: RateCardV1,
        scope: UsageScope,
        window: UsageWindow,
    ) -> UsageInputV1: ...
```

Authorization, transport, retry policy, and service operation are outside this library. The
provider must still return released Usage models with closed watermarks and immutable source
fingerprints; Cost applies the same validation and calculation path afterward.

## Evidence

Construct `MeridianEvidenceSink(ready_meridian)` and pass it to `CostCalculator` to append one
audit record and one lineage record for each persisted or replayed result. Evidence resources,
retention, access policy, and physical placement are deployment-owned.

Public failures are Meridian error envelopes with stable `MERIDIAN_COST_*` codes and a
`requirement` field. No error includes credentials or physical database details.
