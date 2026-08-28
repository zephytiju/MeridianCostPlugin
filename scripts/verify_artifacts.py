# SPDX-License-Identifier: Apache-2.0
"""Verify the one-distribution Cost wheel and source archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath

from packaging.specifiers import SpecifierSet

PACKAGE = "meridian_storage_plugin_cost"
VERSION = "1.0.1"
WHEEL_REQUIRED = {
    "meridian_storage/plugins/cost/__init__.py",
    "meridian_storage/plugins/cost/compatibility.json",
    "meridian_storage/plugins/cost/plugin.py",
    "meridian_storage/plugins/cost/py.typed",
    "meridian_storage/plugins/cost/schema.py",
}
SDIST_REQUIRED = {
    "LICENSE",
    "NOTICE",
    "README.md",
    "contracts/cost-plugin.v1.json",
    "contracts/conformance/golden/fingerprints.json",
    "contracts/conformance/plugin-contract.json",
    "docs/architecture.md",
    "pyproject.toml",
    "scripts/verify_contracts.py",
    "src/meridian_storage/plugins/cost/compatibility.json",
}


def _fail(message: str) -> None:
    raise SystemExit(message)


def _safe_names(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            _fail(f"archive contains an unsafe path: {name}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _safe_names(names)
        missing = WHEEL_REQUIRED - set(names)
        if missing:
            _fail(f"wheel is missing required files: {sorted(missing)}")
        if "meridian_storage/__init__.py" in names:
            _fail("wheel must preserve the shared meridian_storage namespace")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            _fail("wheel must contain exactly one distribution metadata directory")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_names[0]))
        if metadata["Name"] != "meridian-storage-plugin-cost" or metadata["Version"] != VERSION:
            _fail("wheel Name or Version differs from the release contract")
        if SpecifierSet(metadata["Requires-Python"]) != SpecifierSet(">=3.12,<3.15"):
            _fail("wheel Python compatibility differs from the release contract")
        if metadata["License-Expression"] != "Apache-2.0":
            _fail("wheel must publish the SPDX Apache-2.0 license expression")
        requirements = metadata.get_all("Requires-Dist", failobj=[])
        if "meridian-plugin-usage==1.0.2" not in requirements:
            _fail("wheel must depend on the exact released Usage distribution")
        package_roots = {
            "/".join(PurePosixPath(name).parts[:3])
            for name in names
            if name.startswith("meridian_storage/plugins/") and name.endswith(".py")
        }
        if package_roots != {"meridian_storage/plugins/cost"}:
            _fail(f"wheel contains unexpected Python packages: {sorted(package_roots)}")
    return {"file": path.name, "sha256": _sha256(path), "size": path.stat().st_size}


def _verify_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        _safe_names(names)
        roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
        if roots != {f"{PACKAGE}-{VERSION}"}:
            _fail(f"source archive has unexpected roots: {sorted(roots)}")
        prefix = f"{PACKAGE}-{VERSION}/"
        relative = {name.removeprefix(prefix) for name in names if name.startswith(prefix)}
        missing = SDIST_REQUIRED - relative
        if missing:
            _fail(f"source archive is missing required evidence: {sorted(missing)}")
        python_packages = {
            "/".join(PurePosixPath(name).parts[2:5])
            for name in names
            if "/src/meridian_storage/plugins/" in name and name.endswith(".py")
        }
        if python_packages != {"meridian_storage/plugins/cost"}:
            _fail(f"source archive contains unexpected packages: {sorted(python_packages)}")
    return {"file": path.name, "sha256": _sha256(path), "size": path.stat().st_size}


def verify(directory: Path) -> dict[str, object]:
    wheels = sorted(directory.glob(f"{PACKAGE}-{VERSION}-*.whl"))
    sdists = sorted(directory.glob(f"{PACKAGE}-{VERSION}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        _fail("artifact directory must contain exactly one Cost wheel and one source archive")
    return {
        "artifacts": [_verify_wheel(wheels[0]), _verify_sdist(sdists[0])],
        "distribution": "meridian-storage-plugin-cost",
        "formatVersion": "meridian.cost.artifact-report.v1",
        "packageCount": 1,
        "passed": True,
        "version": VERSION,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = verify(arguments.directory)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
