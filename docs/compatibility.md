# Cost / Usage compatibility

Cost 2.0.0 consumes only released public packages. Its runtime closure is Usage
2.0.1, Core 1.0.1, Evidence 1.0.1, Query 1.0.2 and Semantics 2.0.0. PostgreSQL 2.1.1
is an acceptance dependency. Dependency overrides, sibling repository imports and
dependency installation with `--no-deps` are unsupported.

| Combination / path | Compatibility |
| --- | --- |
| Cost 1.0.0 + Usage 1.0.0 | Historical coherent pins; missing the Usage repairs |
| Cost 1.0.0 + Usage 1.0.2 | Incoherent declared dependency closure |
| Cost candidate + Usage 2.0.0 | Historical failure: public aggregate predicates and default Resource profiles |
| Cost 2.0.0 + Usage 2.0.1 | Corrected coherent closure; default provider startup and public aggregate queries pass |
| Repository and normalized-input providers | Real PostgreSQL tests pass for 12, scale12, scale18, zero and 2.500; restart replay, immutable old versions and corrections preserve exact provenance |

The full 83-test suite includes ten live PostgreSQL cases, with no skipped gates.
Tests exercise the actual Usage and Cost schema-provider entry points and default
Resources, without substitute providers, schema edits or capability overlays.
Cost reads Usage only through the released public API. The test host provisions
physical layouts from owning Schemas; unique logical indexes become native btree
indexes with uniqueness retained.

## Persistence and activation compatibility

Immutable Cost writes use `structured.put` 2.0.0 `if_absent`. Resource requirements
declare that version, and the Cost record Resource uses its owning Schema's
`time-series` profile. Cost domain schemas, pricing vectors, canonical bytes and
calculation fingerprints remain unchanged. Resource/provider bundle fingerprints
change with dependency metadata and corrected capabilities/profiles.

Before activation, hosts must regenerate the Resource and provider pins from the
released bundles, compile the corresponding physical layout and retain runtime
physical verification. A configuration pinned to the old Resource/provider
fingerprints must fail closed; do not copy the new hash onto stale metadata or
turn off fingerprint checks. This release does not perform a production data
migration. Existing immutable domain records retain their original fingerprints.

## Reproduce and release evidence

Install `meridian-plugin-cost[test]==2.0.0` from PyPI into a fresh Python 3.12–3.14
environment after publication. Start PostgreSQL 16 / PostGIS 3.4 with an isolated
database and run the source-distribution tests:

```sh
export COST_TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/cost_test
python -m pip check
pytest --junitxml=compatibility-tests.xml
```

A missing database fails acceptance instead of skipping it. CI runs the full suite
on all three Python versions and preserves JUnit evidence. Package and release
workflows capture clean-install reports (URLs and artifact hashes), resolved
version locks and dependency checks. Release manifests checksum these artifacts.
Candidate smoke installations contain only the Cost wheel locally; runtime
dependencies resolve from the registry. The release workflow additionally installs
Cost itself from PyPI, checks dependencies, and runs all acceptance tests after
publication. Its `published-compatibility` artifact contains the registry-only
install reports, lock and test evidence. GitHub artifacts alone do not establish a
successful PyPI release.
