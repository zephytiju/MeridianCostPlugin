<!-- SPDX-License-Identifier: Apache-2.0 -->
# Conformance and release evidence

The repository contains four independent evidence layers:

- `contracts/cost-plugin.v1.json`: JSON Schema for the released compatibility claim.
- `contracts/conformance/plugin-contract.json`: the exact V1 claim validated by that schema.
- `contracts/conformance/golden/fingerprints.json`: deterministic pricing, allocation, Resource
  bundle, and schema vectors.
- `src/meridian_storage/plugins/cost/compatibility.json`: the compatibility matrix shipped in
  the wheel.

Run the complete local acceptance path with:

```console
ruff format --check .
ruff check .
mypy src
python scripts/verify_contracts.py
pytest
python -m build --no-isolation --outdir dist
twine check dist/*
python scripts/verify_artifacts.py dist
```

CI runs that path on Python 3.12, 3.13, and 3.14, performs Bandit and dependency audits, and
builds twice under a fixed `SOURCE_DATE_EPOCH`. `compare_artifacts.py` requires the two builds to
be byte-identical.

An immutable tag release contains the wheel, source archive, `SHA256SUMS`, deterministic
conformance and artifact reports, a CycloneDX SBOM, and a release manifest. GitHub artifact and
SBOM attestations bind those files to the workflow identity. PyPI publication uses trusted
publishing after the owner establishes the first project/identity gate.

The contract asserts that Cost owns no Catalog, service, database, Engine decision, credential,
or Usage storage. A change to those facts—or to a locked public interface—requires an approved
design write-back and a new compatible contract version.
