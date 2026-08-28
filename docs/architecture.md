<!-- SPDX-License-Identifier: Apache-2.0 -->
# Architecture and authority boundaries

`meridian-plugin-cost` is the Cost profile/plugin for Meridian V1. Its canonical source
repository is [`zephytiju/MeridianCostPlugin`](https://github.com/zephytiju/MeridianCostPlugin).
Repository identity and Python distribution identity are deliberately different: the repository
uses the approved CamelCase identifier, while package metadata and installation use
`meridian-plugin-cost`.

## Composition

Cost is an in-process library discovered through the `meridian_storage.plugins` entry-point
group. It publishes schemas through `meridian_storage.schemas` and creates mapping-first
Expressions against already registered Catalogs. It owns no Catalog and introduces no
Adapter-, Engine-, SQL-, or NativeQuery-facing API.

The three Cost logical Resources are:

| Resource | Purpose | Mutation rule |
| --- | --- | --- |
| `structured:cost.rate_cards` | Rate-card lifecycle snapshots | append immutable revisions |
| `structured:cost.calculations` | Reproducible calculation revisions | immutable, idempotent put |
| `structured:cost.records` | Allocated cost facts | immutable, idempotent put |

Audit and lineage are optionally appended to `evidence:cost.audit` and
`evidence:cost.lineage`. These are uses of the existing `evidence` Catalog, not new Catalogs.

## Usage isolation

The only supported in-process Usage integration is `RepositoryUsageProvider`, which accepts the
released `UsageRepository` from `meridian-plugin-usage==1.0.2` and calls its public
`get_meter()` and aggregate query APIs. Cost never reads a Usage executor, physical table,
connection, credential, or private implementation module. An authorized service boundary can
instead implement `UsageInputProvider` and return the same normalized `UsageInputV1` contract.

Cost and Usage logical Resource references are checked for overlap at composition time. Physical
placement must also remain separate. Platform or Vangu/application IaC owns engine selection,
provisioning and references, state, identity, ACLs, migrations, backup/recovery, and lifecycle;
this package only states logical capability requirements.

## Locked baseline

V1 behavior is based on HLD revision 56, Catalog/Public Interfaces revision 70, Engine Adapters
revision 24, Kafka Streaming Adapter LLD revision 6, MeridianConstructs LLD revision 45, and
Cost LLD revision 68. The Catalog registry remains exactly `structured`, `object`, `cache`,
`evidence`, and `streaming`. Cost is a profile over `structured` and never a sixth Catalog.

The public query surface accepts mappings and produces Meridian Expressions plus serialized
Query Operations. Adapter and Engine concepts remain below the runtime boundary, and
NativeQuery is outside Meridian V1.
