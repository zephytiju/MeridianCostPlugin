<!-- SPDX-License-Identifier: Apache-2.0 -->
# Rate-card contract

`RateCardV1` is an immutable snapshot. A stable `(rate_card_id, version)` identifies one pricing
contract; `revision` identifies an append-only lifecycle snapshot. Pricing fields have a
fingerprint independent of lifecycle state so validation, publication, and retirement cannot
silently change money semantics.

## Lifecycle

The only transitions are:

1. `draft` → `validated`
2. `validated` → `published`
3. `published` → `retired`

Draft pricing may be replaced only by appending the next revision with an optimistic
`expected_revision`. Validated and published pricing is frozen. A pricing correction therefore
uses a new rate-card version and an explicit `RateCardRef` in `supersedes`. V1 deletion is
retirement; physical deletion is intentionally unsupported.

Publication rejects two rate cards with the same provider, product, meter version, matching
dimensions, and overlapping half-open effective intervals. Resolution requires exactly one
published rate card that covers the complete Usage window.

## Pricing models

V1 has a closed `PricingModel` enum:

- `flat_unit`: quantity multiplied by one unit price.
- `volume_tier`: the whole quantity uses the tier containing the quantity.
- `graduated_tier`: each contiguous half-open tier prices only its consumed slice.
- `minimum_charge`: flat pricing followed by a positive adjustment up to a minimum.
- `commitment_credit`: flat pricing followed by a fingerprint-bound credit capped at zero.

Tier sequences start at zero, are contiguous, use `[start, end)` boundaries, and end with one
unbounded tier. Currency is a normalized three-letter code. Monetary and quantity inputs reject
binary floats and use the released `Decimal(76,18)` bound.

`RoundingPolicy` names both intermediate and final modes and scales. Intermediate pricing is
rounded once per documented component; final currency rounding is explicit and captured in the
explanation. This makes boundary values and replays deterministic across Python versions.
