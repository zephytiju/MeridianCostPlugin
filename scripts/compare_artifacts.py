# SPDX-License-Identifier: Apache-2.0
"""Require byte-identical Cost builds under a fixed SOURCE_DATE_EPOCH."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix in {".whl", ".gz"}
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    arguments = parser.parse_args()
    first = _digests(arguments.first)
    second = _digests(arguments.second)
    if not first or first != second:
        raise SystemExit(
            "Cost builds are not byte-identical:\n"
            + json.dumps({"first": first, "second": second}, indent=2, sort_keys=True)
        )
    print(
        json.dumps(
            {
                "artifacts": first,
                "formatVersion": "meridian.cost.reproducibility.v1",
                "passed": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
