# Cost / Usage compatibility

The 2.0.0 candidate consumes only released public packages. Its runtime closure is
Usage 2.0.0, Core 1.0.1, Evidence 1.0.1, Query 1.0.2 and Semantics 2.0.0;
PostgreSQL 2.1.1 is an acceptance dependency. No dependency overrides, sibling
repository imports, or dependency installation with `--no-deps` are supported.

| Combination / path | Result |
| --- | --- |
| Published Cost 1.0.0 + Usage 1.0.0 | Historical coherent pins; does not contain the Usage repairs |
| Published Cost 1.0.0 + Usage 1.0.2 | Incoherent declared dependency closure |
| Cost 2.0.0 candidate + Usage 2.0.0 | Clean dependency resolution and `pip check` pass |
| Persisted Usage aggregates supplied as normalized input to Cost | Real PostgreSQL tests pass for 12, scale12, scale18, zero and 2.500; restart replay, immutable old versions and correction provenance verified |
| `RepositoryUsageProvider` with Usage 2.0.0 / Query 1.0.2 / PostgreSQL 2.1.1 | **Blocked:** public aggregate query executes shorthand window range as a dict SQL parameter |
| Standard Usage 2.0.0 schema-provider placement on PostgreSQL | **Blocked upstream:** event/aggregate Resources use `usage` profile; PostgreSQL requires supported semantic profiles |

This candidate is not release acceptance and must not be published until the
repository provider tests pass against an owning released correction. The tests
retain the failure (no skip or expected-failure marker). A successful GitHub
artifact build is not a PyPI publication.

## Persistence and fingerprint compatibility

Cost's immutable writes now use `structured.put` 2.0.0 `if_absent`. Resource
requirements declare that version, and the Cost record Resource uses the owning
Schema's `time-series` profile. The Cost domain schemas, pricing vectors, canonical
bytes and calculation fingerprints remain unchanged. Only the Resource bundle
fingerprint changes with package/dependency metadata and corrected capabilities.

The live tests use the actual Cost schema-provider entry point. Their host owns
separate Usage Resource names through `UsageResources`, deriving the Resources
from unchanged public Usage Schemas. This is explicit host configuration and does
not validate the broken default Usage Resource bundle. It does not access Usage
physical storage from Cost. PostgreSQL layout provisioning is test-host code;
unique logical indexes become native btree indexes with uniqueness retained.

Static-provider tests obtain the aggregate readback via public
`UsageRepository.put_aggregate` duplicate handling. They isolate Cost persistence
and replay, and cannot substitute for the failing public repository-provider gate.

## Reproduce

Install the candidate and its pinned test extras in a fresh Python 3.12–3.14
environment. Start PostgreSQL 16 / PostGIS 3.4 with an isolated database, then run:

```sh
export COST_TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/cost_test
python -m pip check
pytest --junitxml=compatibility-tests.xml
```

A missing database fails acceptance instead of skipping it. CI runs the full suite
on all three Python versions and preserves JUnit results even on failure. Package
and release workflows capture clean-install reports (URLs and artifact hashes),
resolved version locks and dependency checks; release manifests checksum those
files. The candidate wheel is the only local artifact in its smoke installation;
all runtime dependencies are resolved from the registry. A final registry-only
Cost installation remains mandatory after successful CI publication.
