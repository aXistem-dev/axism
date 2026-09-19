#!/usr/bin/env python3
"""Fail if tracked sources contain machine-identifying path fingerprints.

Allowed synthetic examples: /home/alice/... and /Users/alice/...
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Absolute home paths that are not the synthetic alice fixtures
HOME_PATH = re.compile(r"/(?:home|Users)/(?!alice\b)[A-Za-z0-9._-]+")
# Private forge / host style markers (assembled so this file does not self-match)
_PRIVATE = "|".join(
    [
        r"forgejo\.[a-z0-9.-]+",
        "carrez" + r"\.be",
        "neutron" + "nest",
        "stellar" + "station",
        "onyx-" + "carrez",
    ]
)
PRIVATE_HOST = re.compile(rf"(?i)({_PRIVATE})")


def tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # fallback: walk without .git / .venv
        files: list[Path] = []
        for p in ROOT.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            parts = set(rel.parts)
            if parts & {".git", ".venv", "__pycache__", ".pytest_cache"}:
                continue
            if any(part.startswith(".") and part not in {".gitignore"} for part in rel.parts[:-1]):
                continue
            files.append(p)
        return files
    return [ROOT / line for line in out.splitlines() if line]


def main() -> int:
    bad: list[str] = []
    for path in tracked_files():
        if path.suffix in {".png", ".jpg", ".ico", ".whl", ".so"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if HOME_PATH.search(line):
                bad.append(f"{rel}:{i}: home-path fingerprint: {line.strip()[:120]}")
            if PRIVATE_HOST.search(line):
                bad.append(f"{rel}:{i}: private-host fingerprint: {line.strip()[:120]}")
    if bad:
        print("Anonymity check failed:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"Anonymity check OK ({len(list(tracked_files()))} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
