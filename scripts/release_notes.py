#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one version, for use as release notes.

Usage: release_notes.py VERSION [CHANGELOG]   (VERSION with or without a leading "v")
Exits 1 when the version has no section or the section is empty.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]")


def section(changelog: str, version: str) -> str:
    """Body of the ``## [version]`` section, without its heading."""
    version = version.removeprefix("v")
    lines: list[str] = []
    inside = False
    for line in changelog.splitlines():
        match = HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group("version") == version
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = Path(argv[2]) if len(argv) == 3 else Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    body = section(path.read_text(encoding="utf-8"), argv[1])
    if not body:
        print(f"no CHANGELOG section for {argv[1]}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
