# SPDX-License-Identifier: Apache-2.0
"""Create a release manifest and portable SHA-256 checksum ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_REQUIRED = {
    "artifact-report.json",
    "conformance-report.json",
    "meridian_plugin_cost-1.0.0-py3-none-any.whl",
    "meridian_plugin_cost-1.0.0.tar.gz",
    "sbom.cdx.json",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()
    if _COMMIT.fullmatch(arguments.source_commit) is None:
        raise SystemExit("source commit must be a full hexadecimal Git commit identifier")
    if _TAG.fullmatch(arguments.tag) is None:
        raise SystemExit("release tag must have the form vMAJOR.MINOR.PATCH")
    names = {path.name for path in arguments.directory.iterdir() if path.is_file()}
    missing = _REQUIRED - names
    if missing:
        raise SystemExit(f"release evidence is incomplete: {sorted(missing)}")
    artifacts = [
        {
            "file": path.name,
            "sha256": _digest(path),
            "size": path.stat().st_size,
        }
        for path in sorted(arguments.directory.iterdir())
        if path.is_file() and path.name not in {"release-manifest.json", "SHA256SUMS"}
    ]
    manifest = {
        "artifacts": artifacts,
        "distribution": "meridian-plugin-cost",
        "formatVersion": "meridian.cost.release-manifest.v1",
        "repository": "zephytiju/MeridianPluginCost",
        "sourceCommit": arguments.source_commit,
        "tag": arguments.tag,
        "usageDependency": "meridian-plugin-usage==1.0.0",
        "version": "1.0.0",
    }
    manifest_path = arguments.directory / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksum_paths = sorted(
        path
        for path in arguments.directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksums = "".join(f"{_digest(path)}  {path.name}\n" for path in checksum_paths)
    (arguments.directory / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
