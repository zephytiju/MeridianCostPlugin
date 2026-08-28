# SPDX-License-Identifier: Apache-2.0
"""Verify the release CycloneDX SBOM root and locked runtime dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_RUNTIME = {
    "meridian-plugin-usage": "1.0.2",
    "meridian-storage-core": "1.0.0",
    "meridian-storage-evidence": "1.0.0",
    "meridian-storage-query": "1.0.0",
    "meridian-storage-semantics": "1.0.0",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sbom", type=Path)
    arguments = parser.parse_args()
    data = json.loads(arguments.sbom.read_text(encoding="utf-8"))
    if data.get("bomFormat") != "CycloneDX" or data.get("specVersion") != "1.6":
        raise SystemExit("release SBOM must be validated CycloneDX 1.6 JSON")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("component"), dict):
        raise SystemExit("release SBOM is missing its root component")
    root = metadata["component"]
    if (root.get("name"), root.get("version"), root.get("type")) != (
        "meridian-plugin-cost",
        "1.0.1",
        "library",
    ):
        raise SystemExit("release SBOM root differs from the Cost distribution")
    licenses = root.get("licenses", [])
    if not any(item.get("license", {}).get("id") == "Apache-2.0" for item in licenses):
        raise SystemExit("release SBOM root must declare SPDX Apache-2.0")
    components = data.get("components")
    if not isinstance(components, list):
        raise SystemExit("release SBOM is missing dependency components")
    observed = {
        item.get("name"): item.get("version") for item in components if isinstance(item, dict)
    }
    missing = {
        name: expected for name, expected in _RUNTIME.items() if observed.get(name) != expected
    }
    if missing:
        raise SystemExit(f"release SBOM is missing locked runtime dependencies: {missing}")
    print(
        json.dumps(
            {
                "formatVersion": "meridian.cost.sbom-report.v1",
                "passed": True,
                "root": "meridian-plugin-cost@1.0.1",
                "runtimeDependencies": _RUNTIME,
                "specVersion": "1.6",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
