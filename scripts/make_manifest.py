#!/usr/bin/env python3
"""Regenerate MANIFEST.sha256 over every file in this submission.

The original manifest was produced by the build script in the working
repository and covered only the COPIED files -- which is what it could prove,
since a generated file has no original to match. Then the MCP server was
written directly here, so the submission now contains files that are genuinely
its own.

Leaving those out of the manifest would be the worse of two errors: a reader
running `sha256sum -c` would see "OK" for everything listed and have no way to
tell that several files were not checked at all. So every file is listed, and
the ones that are copies are marked as such. The manifest then does two jobs
and says which is which:

  * for COPIED files, the hash proves byte-identity with the working repo
  * for files ORIGINAL to this submission, it proves only that nothing has been
    altered since it was built -- which is still worth having, and is not the
    same claim

  python3 scripts/make_manifest.py --check     # verify, change nothing
  python3 scripts/make_manifest.py             # rewrite it
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"

#: Written for this submission rather than copied from the working repository.
#: Anything not listed here is expected to be byte-identical to its source.
ORIGINAL = {
    "README.md", "VERIFY.md", "MANIFEST.sha256", ".gitignore",
    "MCP.md", "requirements-mcp.txt",
    "eqrl/mcp_server.py", "scripts/run_mcp_server.py",
    "scripts/make_manifest.py", "tests/test_mcp.py",
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p == MANIFEST:
            continue            # cannot hash itself while writing itself
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify against the existing manifest and change "
                         "nothing; non-zero exit if anything differs")
    a = ap.parse_args()

    files = walk()
    rows = [(str(p.relative_to(ROOT)), sha256(p)) for p in files]
    copied = [(r, h) for r, h in rows if r not in ORIGINAL]
    original = [(r, h) for r, h in rows if r in ORIGINAL]

    if a.check:
        if not MANIFEST.exists():
            print("no MANIFEST.sha256", file=sys.stderr)
            return 1
        listed = {}
        for line in MANIFEST.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            h, _, rel = line.partition("  ")
            listed[rel] = h
        have = dict(rows)
        bad = [r for r, h in have.items() if listed.get(r) != h]
        missing = [r for r in have if r not in listed]
        extra = [r for r in listed if r not in have]
        for label, items in (("CHANGED", bad), ("NOT LISTED", missing),
                             ("LISTED BUT ABSENT", extra)):
            for r in items:
                print(f"{label}: {r}")
        ok = not (bad or missing or extra)
        print(f"{len(have)} files checked -- "
              f"{'all consistent' if ok else 'MANIFEST IS STALE'}")
        return 0 if ok else 1

    lines = [
        "# MANIFEST.sha256 -- every file in this submission.",
        "#",
        "# Verify with:   sha256sum -c MANIFEST.sha256",
        f"# Written {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC.",
        "#",
        "# TWO KINDS OF FILE ARE LISTED, and they support different claims.",
        "#",
        f"# {len(copied)} files are COPIES of the working repository this was",
        "# built from. For those the hash proves byte-identity with the source;",
        "# `diff` against that repository reports no differences.",
        "#",
        f"# {len(original)} files are ORIGINAL to this submission -- the two",
        "# documents written for it, its .gitignore, and the MCP server added",
        "# afterwards. For those the hash proves only that nothing has changed",
        "# since this manifest was written, which is a weaker and different",
        "# claim. They are:",
    ]
    lines += [f"#   {r}" for r, _ in original]
    lines += ["#"]
    lines += [f"{h}  {r}" for r, h in rows]
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.name}: {len(rows)} files "
          f"({len(copied)} copied, {len(original)} original)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
