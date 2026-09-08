<!-- SPDX-License-Identifier: Apache-2.0 -->
# Cost / Usage compatibility

Cost 2.0.1 consumes released public packages with API compatibility bounds. The
previous Cost 2.0.0 exact recipe could not resolve with Core 1.1.0. Package release
numbers identify artifacts; they do not replace the operation and schema contracts.

| Dependency | Runtime requirement | Basis |
| --- | --- | --- |
| Usage | >=2.0.2,<3 | Public normalized aggregates, exact decimals, default Resources and corrected Core 1.1 dependency closure; major 3 unreviewed |
| Core | >=1.1.0,<2 | Public runtime/registry/SPI, transactions and independent Engine release provenance; major 2 unreviewed |
| Evidence | >=1.0.2,<2 | Public append and required atomic Evidence with a resolvable Core 1.1 closure |
| Query | >=1.0.3,<2 | Public mapping-first predicates and decoupled package metadata |
| Semantics | >=2.0.1,<3 | Schema documents and structured.put 2.0.0; pre-2 write semantics excluded |
| PostgreSQL (test extra) | >=2.2.0,<3 | Released Adapter supporting Core 1.1 and required real-engine behavior |

These floors select the repaired public closure; they do not add new pricing or
storage semantics. Future combinations admitted by the bounds remain unverified
until their behavior has been tested. No sibling source, dependency override or
`--no-deps` installation establishes compatibility.

## Validation selection and evidence

`requirements/validation-constraints.txt` selects the release-validation recipe.
`requirements/validation.lock` locks all direct and transitive test dependencies
with public SHA-256 hashes across Python 3.12–3.14. It selects Core 1.1.0, Usage
2.0.2, Evidence 1.0.2, Query 1.0.3, Semantics 2.0.1 and PostgreSQL 2.2.0. CI installs
that lock with hash enforcement, then installs only this repository normally.
The independent candidate and published-wheel installations resolve unconstrained
public dependencies and record the observed versions, URLs and artifact hashes.

| Combination | Evidence |
| --- | --- |
| Cost 2.0.0 + Usage 2.0.1 / Core 1.0.1 | Historical tested recipe, retained in the previous release |
| Cost 2.0.1 + validation lock | Full tests on Python 3.12, 3.13 and 3.14 required by CI |
| Cost 2.0.1 public wheel + normally resolved closure | Mandatory clean install, pip check and full PostgreSQL suite after CI publication |
| Other versions admitted by the bounds | Unverified until exercised; never inferred from release equality |

The suite retains all pricing/rounding, half-open intervals, overlap rejection,
idempotency, corrections, public Usage integration, tenant isolation and restart
replay gates. Additional live PostgreSQL tests cover a host-owned transaction
containing Cost writes and required audit/lineage receipts: success and replay,
audit failure, and lineage failure after the first receipt. Failed appends must
roll back records and both receipts; a fresh runtime checks durable state.

## Persistence and activation

`structured.put` remains 2.0.0 with `if_absent`. Cost domain schemas, pricing vectors,
canonical bytes and calculation fingerprints remain unchanged. The Resource
bundle fingerprint changes because its declared Usage dependency changes. Hosts
must regenerate bundle pins from the released providers and keep physical Schema,
Resource, placement and runtime fingerprint checks. Never put a new hash on stale
metadata. This release performs no production migration.

The wheel's compatibility metadata uses `meridian.cost.compatibility.v2` to
separate real `contracts` from distribution `dependencies`; consumers of that
metadata must handle the explicit format version. Historical v1 metadata remains
in the old release. Plugin and domain contract versions do not change.

## Reproduce

```sh
python -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements/validation.lock
.venv/bin/python -m pip install -e '.[test]'
export COST_TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/cost_test
.venv/bin/python -m pip check
.venv/bin/python -m pytest --junitxml=compatibility-tests.xml
```

Use the exact PostgreSQL/PostGIS image digest in `.github/workflows/ci.yml`.
A missing database fails acceptance. To choose a different validation recipe,
update the constraints and regenerate the hash lock with
`uv pip compile pyproject.toml --extra test --constraint requirements/validation-constraints.txt --generate-hashes --universal --output-file requirements/validation.lock`,
then repeat all gates. The recipe is not a runtime acceptance allowlist.

Release evidence includes install reports, the validation hash lock, JUnit, SBOM,
artifact checksums and attestations. The `published-compatibility` artifact proves
the installed public wheel's full acceptance; an uploaded GitHub artifact alone
does not prove a successful PyPI release.
