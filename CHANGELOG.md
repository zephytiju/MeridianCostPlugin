<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog

## 2.0.1 - 2026-09-08

- Admit the public Core 1.1.0 / Usage 2.0.2 closure with justified compatible dependency bounds.
- Separate API contracts and package requirements in compatibility metadata v2; retain domain
  schemas, pricing, calculation identity and structured.put 2.0.0 requirements.
- Lock release validation dependencies and hashes separately from runtime package metadata.
- Keep mandatory PostgreSQL tests, reproducible artifacts, audits and published-wheel verification.

## 2.0.0 - 2026-09-07

- Consume corrected released Usage 2.0.1 and its coherent dependency closure.
- Use explicit immutable create mode and the supported Cost time-series Resource profile.
- Lower Cost query operators to supported predicates while preserving logical-plan fingerprints.
- Add live PostgreSQL restart, replay, correction and scoped-read provenance acceptance tests.
- Preserve clean-install locks and artifact digests through CI, including registry-only
  acceptance after publication. Default Usage/Cost provider composition is verified.

## 1.0.1 - 2026-08-28

- Corrected the canonical repository identity to `MeridianCostPlugin`.
- Retained the existing distribution identity `meridian-plugin-cost`.
- Pinned the official released Usage dependency to `meridian-plugin-usage==1.0.2`.
- Preserved the `meridian_storage.plugins.cost` import namespace and Cost 1.0.0 schema contract.

## 1.0.0 - 2026-08-26

- Initial Meridian V1 Cost plugin release.
- Immutable versioned rate cards and lifecycle transitions.
- Exact Decimal pricing, deterministic allocation, and calculation explanations.
- Public Usage 1.0.0 integration, Meridian-backed Cost persistence, and Evidence output.
