#!/usr/bin/env python3
"""Build the hackathon submission: a clean, provably byte-identical copy.

THE CONTRACT. Every file carried into the submission is byte-for-byte the same
as the file it came from, and that is PROVEN rather than asserted -- sha256 is
taken on the source before the copy and on the destination after it, and any
mismatch aborts the whole build. Cleaning happens by OMITTING whole files, never
by editing them, so there is no route by which behaviour drifts between the
working repo and what is submitted.

Exactly three files in the output are not copies: README.md, VERIFY.md and
MANIFEST.sha256. They are written fresh because the originals reference an
internal engineering log that is not shipped, and a document with nine dangling
links is precisely the "outdated information" this exercise removes.

WHY A SCRIPT AND NOT A FEW cp COMMANDS. A submission assembled by hand is
assembled differently every time and cannot be reviewed. More to the point, the
failure that matters here is silent: a copy that quietly omits something, or a
glob that matches nothing and ships an empty directory while reporting success.
This project fixed exactly that bug in `cache.load` the day before this was
written -- a tag matching no file returned an empty set instead of raising, and
would have trained a model on half its data while printing "done". Every check
below aborts; none of them warn.

  python3 scripts/make_submission.py --dry-run     # list what would ship
  python3 scripts/make_submission.py               # build it
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT.parent / "pcie_eq_rl_submission"

#: Files written by this script rather than copied. Listed so the manifest can
#: mark them and a reader is never misled about what is original.
GENERATED = ("README.md", "VERIFY.md", "MANIFEST.sha256")

#: What ships. Explicit, and deliberately NOT "copy everything, then delete":
#: an allow-list fails closed, a deny-list ships whatever nobody thought of.
#: Each entry is a path or a glob relative to the repo root.
INCLUDE: tuple[str, ...] = (
    # --- the framework ---------------------------------------------------
    "eqrl/*.py",
    "eqrl/agents/*.py",

    # --- the pipeline and the analysis behind every claim -----------------
    # Historical analysis scripts stay: they are working code, and the result
    # they produced is quoted in the docs.
    "scripts/*.py",
    "scripts/run_checkpoint6.sh",

    # --- tests, minus the one coupled to the unshipped log ----------------
    "tests/conftest.py",
    "tests/test_cache.py",
    "tests/test_deploy.py",
    "tests/test_env_goal.py",
    "tests/test_margin_vector.py",
    "tests/test_pure.py",
    "tests/test_spice.py",
    "tests/test_surrogate.py",

    # --- results: the claims, and the brief's actual deliverable ----------
    # The .sp files ARE the deliverable ("outputs the final schematic and
    # resulting specs"). .dat and .png are regenerated and already gitignored.
    "results/*.json",
    "results/pvt_sweep.csv",
    "results/ctle_final.sp",
    "results/ctle_final_eye.sp",

    # --- the evaluation data the test suite actually reads -----------------
    # NOT optional. tests/test_margin_vector.py checks the central property of
    # this project -- min(margin_vector) >= 0 <=> all_pass -- against every
    # real measurement, and `cache.load` raises on a missing tag rather than
    # returning empty, so without these the test ERRORS instead of skipping.
    # Also what `scripts/margin_transfer.py` reads at its default tags.
    "results/evals/collect.jsonl",
    "results/evals/goalstart.jsonl",
    "results/evals/goalstart2.jsonl",
    "results/evals/plan_collect.json",
    "results/evals/plan_goalstart.json",
    "results/evals/plan_goalstart2.json",

    # --- trained models, so the framework can actually be RUN --------------
    # Without these a judge would have to spend ~12 000 simulations rebuilding
    # the surrogate before the RL could start.
    "models/surrogate/LATEST",
    "models/surrogate/*/*.pt",
    "models/surrogate/*/*.npz",
    "models/surrogate/*/*.json",
    "models/policy/LATEST",
    "models/policy/ckpt_120000.zip",

    # --- schematics and project files -------------------------------------
    "netlists/*.sch",
    "requirements.txt",
    "pytest.ini",
    ".gitignore",
)

#: What is deliberately left out, with the reason. Not used by the build -- the
#: allow-list above decides that -- but recorded here because "why is X not in
#: the submission" is the question this file exists to answer.
EXCLUDED_AND_WHY = {
    "HANDOVER.md": "internal engineering log written for the agent, not the project",
    "tests/test_docs.py": "reads HANDOVER.md directly; it checks the working repo's doc hygiene, not the framework",
    "scripts/run_checkpoint5.sh": "superseded by run_checkpoint6.sh; nothing references it",
    "results/evals/bon_*.jsonl": "17 MB of raw per-simulation records no test or script reads; their conclusions are in best_of_n_*.json",
    "models/policy/ckpt_100000.zip": "superseded training checkpoint",
    "models/policy/ckpt_110000.zip": "superseded training checkpoint",
    "models/policy/ckpt5_ppo/": "checkpoint 5's PPO weights, trained against a reward that was replaced",
    "results/*.dat, *.png": "regenerated by the scripts and already gitignored",
    "logs/": "run logs from this machine",
    "configs/": "empty",
    "__pycache__/, .pytest_cache/, .git/": "build and VCS state",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BuildError(RuntimeError):
    """Anything that would produce a submission nobody can trust."""


def git_sha_and_cleanliness() -> tuple[str, bool]:
    def run(*a):
        return subprocess.run(a, cwd=ROOT, capture_output=True, text=True,
                              timeout=30)
    sha = run("git", "rev-parse", "HEAD").stdout.strip()
    dirty = bool(run("git", "status", "--porcelain").stdout.strip())
    return sha, not dirty


def resolve_include() -> list[Path]:
    """Expand INCLUDE to concrete files.

    An entry matching NOTHING is a hard error. A typo in a glob that silently
    ships an empty directory is the exact failure this project has already been
    bitten by once, and it is invisible in a success message.
    """
    out: list[Path] = []
    empty: list[str] = []
    for pattern in INCLUDE:
        hits = sorted(p for p in ROOT.glob(pattern) if p.is_file())
        if not hits:
            empty.append(pattern)
        out.extend(hits)
    if empty:
        raise BuildError(
            "these INCLUDE patterns matched no files:\n  "
            + "\n  ".join(empty)
            + "\nA pattern that matches nothing means the submission is missing "
              "something and would not say so.")
    # one file may match two patterns; keep each once, in a stable order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return sorted(uniq)


def check_no_stragglers(files: list[Path]) -> None:
    """Refuse to ship anything the allow-list did not mean to include."""
    bad = [f for f in files
           if "__pycache__" in f.parts or f.suffix in {".pyc", ".log"}
           or f.name == "HANDOVER.md" or f.name == "test_docs.py"]
    if bad:
        raise BuildError("allow-list matched files that must not ship:\n  "
                         + "\n  ".join(str(f.relative_to(ROOT)) for f in bad))


def check_latest_pointers(dest: Path) -> None:
    """Both LATEST files name a path that must resolve INSIDE the copy.

    They hold relative paths, so a model directory left out of INCLUDE produces
    a submission that imports fine and then fails at the first `load_latest()`
    with a confusing missing-file error, long after anyone is watching.
    """
    for rel in ("models/surrogate/LATEST", "models/policy/LATEST"):
        f = dest / rel
        if not f.exists():
            raise BuildError(f"{rel} is missing from the submission")
        target = (dest / f.read_text().strip()).resolve()
        if not target.exists():
            raise BuildError(
                f"{rel} points at {f.read_text().strip()}, which does not "
                f"exist in the submission. The model it names was not copied.")


def check_python_compiles(dest: Path) -> None:
    """Every shipped .py must at least parse. Cheap, and catches a truncated
    copy that a checksum would also catch but that this reports readably."""
    bad = []
    for f in sorted(dest.rglob("*.py")):
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            bad.append(f"{f.relative_to(dest)}: {e}")
    if bad:
        raise BuildError("shipped Python does not compile:\n  " + "\n  ".join(bad))


def write_manifest(dest: Path, copied: list[tuple[str, str]], src_sha: str) -> None:
    lines = [
        "# MANIFEST.sha256 -- every file in this submission.",
        "#",
        "# Verify with:   sha256sum -c MANIFEST.sha256",
        "#",
        f"# Built from commit {src_sha} at "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC.",
        "# Every file listed below is byte-for-byte identical to the working",
        "# repository it was copied from, except these, which were written for",
        "# this submission: " + ", ".join(GENERATED) + ".",
        "#",
    ]
    body = [f"{h}  {rel}" for rel, h in copied]
    (dest / "MANIFEST.sha256").write_text("\n".join(lines + body) + "\n",
                                          encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a non-empty destination")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="build from an uncommitted tree. Off by default: a "
                         "submission that does not correspond to a commit "
                         "cannot be reproduced or traced.")
    a = ap.parse_args()
    dest = Path(a.dest).resolve()

    try:
        src_sha, clean = git_sha_and_cleanliness()
        if not clean and not a.allow_dirty:
            raise BuildError(
                "the working tree has uncommitted changes. Commit them first, "
                "or pass --allow-dirty and accept that the submission cannot be "
                "traced to a commit.")

        files = resolve_include()
        check_no_stragglers(files)
        total = sum(f.stat().st_size for f in files)

        print(f"source commit {src_sha[:12]}{'' if clean else '  (DIRTY)'}")
        print(f"{len(files)} files, {total / 1048576:.1f} MB -> {dest}")
        if a.dry_run:
            for f in files:
                print(f"   {f.stat().st_size:>10,}  {f.relative_to(ROOT)}")
            print("\n(dry run -- nothing written)")
            return 0

        if dest.exists() and any(dest.iterdir()) and not a.force:
            raise BuildError(f"{dest} exists and is not empty. Use --force.")

        # sha256 the SOURCE before copying, so the comparison afterwards is
        # against what was read, not against a re-read that could hide a
        # mid-build change.
        planned = [(str(f.relative_to(ROOT)), sha256(f)) for f in files]

        if dest.exists() and a.force:
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        copied = []
        for (rel, want), f in zip(planned, files):
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)            # contents AND mtime
            got = sha256(out)
            if got != want:
                raise BuildError(
                    f"copy of {rel} does not match its source\n"
                    f"  source      {want}\n  destination {got}")
            copied.append((rel, got))

        check_latest_pointers(dest)
        check_python_compiles(dest)
        write_manifest(dest, copied, src_sha)

        print(f"\n  {len(copied)} files copied, every sha256 verified")
        print(f"  MANIFEST.sha256 written")
        print(f"\n  still to write by hand: README.md, VERIFY.md")
        return 0

    except BuildError as e:
        print(f"\nREFUSED: {e}", file=sys.stderr)
        if not a.dry_run and dest.exists() and dest != ROOT:
            shutil.rmtree(dest, ignore_errors=True)
            print(f"  removed the partial build at {dest}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
