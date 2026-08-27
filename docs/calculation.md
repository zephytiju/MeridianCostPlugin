<!-- SPDX-License-Identifier: Apache-2.0 -->
# Calculation and allocation

`CostCalculator` prices only a closed `UsageInputV1`. The input contains exact released Usage
aggregate revisions, their fingerprints, the meter version, canonical unit, scope, half-open
window, and a watermark at or after the end of that window.

For every invocation the calculator:

1. loads the latest lifecycle snapshot for the exact fingerprint-bound `RateCardRef`;
2. requires that snapshot to remain published and cover the entire window;
3. fetches closed Usage through `UsageInputProvider`;
4. computes an exact `PriceResultV1` with explicit components and adjustments;
5. applies deterministic largest-remainder allocation at the target scale;
6. persists immutable cost records and their parent calculation in one transaction when the
   runtime exposes a transaction boundary; and
7. optionally emits typed audit and lineage records through the `evidence` Catalog.

## Identity and corrections

The calculation identity fingerprints Usage input, rate-card pricing, allocation, window, and
calculator version. Repeating the same identity returns the persisted calculation and records
with `replayed=True`. A changed source aggregate, rate card, allocation, or calculator version
creates the next revision in the same calculation series and records `supersedes` links.

Each `CalculationV1` records exact aggregate references, pricing and allocation fingerprints,
Decimal totals, output record IDs, and a canonical explanation tree. Each `CostRecordV1` repeats
the relevant lineage and carries its allocation key and fingerprint. Stored record totals must
sum exactly to the parent final amount.

## Largest-remainder rule

Weights are normalized by exact Decimal division. Each raw share is floored at the requested
quantum, then residual quanta are assigned by descending fractional remainder. Ties use the
canonical allocation-key fingerprint, so caller ordering cannot affect output. The same rule
works for signed adjustment values while preserving the exact input total.
