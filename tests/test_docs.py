"""Documentation consistency, enforced rather than asserted.

Three separate commits in this project have claimed the docs were free of stale
counts and self-contradictions, and each time a reviewer found more.  The
failure mode is always the same: one fact living in several files, updated in
some.  Prose cannot be trusted to stay in step with the code by good intentions,
so the facts that keep drifting are checked here.

Pure tests -- no PDK, no ngspice.  They read the docs and the committed
report.json, both of which are in the repository.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "HANDOVER.md", "VERIFY.md")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _collect(args: list[str]) -> list[str]:
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-m", "pytest", str(ROOT / "tests"),
         "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        capture_output=True, text=True, cwd=ROOT).stdout
    return [ln for ln in out.splitlines() if "::" in ln]


def _collected_test_counts() -> tuple[int, int, dict[str, int]]:
    """(total, fast, per-file) as pytest actually collects them.

    Per-file counts are returned because the docs legitimately quote them too
    (`tests/test_pure.py` (111)), and a count that is real must not be flagged
    just because it is not one of the three totals.
    """
    from collections import Counter
    all_ids = _collect([])
    fast_ids = _collect(["-m", "not needs_pdk"])
    per_file = Counter(ln.split("::", 1)[0].split("/")[-1] for ln in all_ids)
    return len(all_ids), len(fast_ids), dict(per_file)


# --------------------------------------------------------------------------
# 1. every "N tests" claim matches what pytest actually collects
# --------------------------------------------------------------------------
def test_documented_test_counts_match_collection():
    total, fast, per_file = _collected_test_counts()
    slow = total - fast
    allowed = {total, fast, slow} | set(per_file.values())

    # Only the shapes the docs actually use to state a count. Deliberately
    # narrow: a loose pattern matches "13.2%", "100 mV" and the line-number in
    # `test_spice.py:240-241`, none of which are counts.
    pat = re.compile(
        r"\b(\d{2,4})\s+(?:tests?\b|total\b|pure\b|fast\b|against\s+real\s+ngspice)"
        r"|\ball\s+(\d{2,4})\b"
        r"|test_\w+\.py`?\s*\((\d{2,4})\)")
    bad: list[str] = []
    for name in DOCS:
        for i, line in enumerate(_read(name).splitlines(), 1):
            if "test" not in line.lower():
                continue
            for m in pat.finditer(line):
                val = int(next(g for g in m.groups() if g))
                if val not in allowed:
                    bad.append(f"{name}:{i}: claims {val}, "
                               f"collected total={total} fast={fast} slow={slow}"
                               f"\n    {line.strip()}")
    assert not bad, "stale test-count claims:\n" + "\n".join(bad)


# --------------------------------------------------------------------------
# 2. the scorecard is 8 brief specs + 1 internal check, everywhere
# --------------------------------------------------------------------------
def test_no_doc_claims_nine_brief_specs():
    """"9 specs pass" conflates the brief's 8 with the internal bias check.

    The count is allowed to APPEAR (the scorecard really does show nine rows);
    what is banned is presenting nine as the number of requirements met.
    """
    banned = re.compile(r"9\s*/\s*9\s*specs|all\s+9\s+specs\s+pass|"
                        r"\b9\s+specs\s+pass", re.I)
    bad = []
    for name in DOCS:
        for i, line in enumerate(_read(name).splitlines(), 1):
            if banned.search(line):
                bad.append(f"{name}:{i}: {line.strip()}")
    assert not bad, (
        "docs present 9 as the number of brief specs met; it is 8 + 1 internal "
        "saturation check (specs.BRIEF_SPECS / specs.INTERNAL_CHECKS):\n"
        + "\n".join(bad))


def test_brief_spec_count_is_what_the_code_says():
    """The prose number must track the code, not a memory of it."""
    from eqrl import specs as sp
    assert len(sp.BRIEF_SPECS) == 8, sp.BRIEF_SPECS
    # INTERNAL_CHECKS is deliberately not pinned to a count: adding an internal
    # validity check is fine and expected, adding a *brief* spec is not.
    assert not set(sp.INTERNAL_CHECKS) & set(sp.BRIEF_SPECS)
    readme = _read("README.md")
    assert f"All {len(sp.BRIEF_SPECS)} brief specs" in readme, (
        "README's headline no longer matches len(specs.BRIEF_SPECS)")


# --------------------------------------------------------------------------
# 3. headline numbers in the README come from the committed report
# --------------------------------------------------------------------------
REPORT = ROOT / "results" / "report.json"


@pytest.mark.skipif(not REPORT.exists(), reason="no committed report.json")
def test_readme_headline_numbers_match_the_report():
    r = json.loads(REPORT.read_text())
    pv = r["pvt"]

    def worst(key: str, how: str) -> float:
        vals = [c["metrics"][key] for c in pv.values()
                if isinstance(c["metrics"].get(key), (int, float))]
        return min(vals) if how == "min" else max(vals)

    # (README token, metric, min|max, tolerance as a fraction)
    claims = [
        ("peaking", "peaking_db", "min"),
        ("f_peak", "f_peak", "max"),
        ("hd3", "hd3_db", "max"),
        ("noise", "vn_in_rms", "max"),
        ("power", "power_w", "max"),
        ("eye H", "eye_height_v", "min"),
        ("eye W", "eye_width_ui", "min"),
    ]
    readme = _read("README.md")
    block = readme[readme.find("## Result"):readme.find("## Status")]
    nums = [float(x) for x in re.findall(r"-?\d+\.\d+", block)]
    assert nums, "no numbers found in the README Result block"

    missing = []
    for token, key, how in claims:
        actual = worst(key, how)
        # the README prints these in display units; accept any scale decade
        scales = (1.0, 1e3, 1e-3, 1e9, 1e-9, 1e6)
        if not any(any(abs(n - actual * s) <= abs(actual * s) * 5e-3
                       for n in nums) for s in scales):
            missing.append(f"{token} ({key}): report says {actual:.6g}, "
                           f"no matching number in the README Result block")
    assert not missing, "README headline drifted from report.json:\n" + "\n".join(missing)


@pytest.mark.skipif(not REPORT.exists(), reason="no committed report.json")
def test_report_pass_dict_splits_into_brief_and_internal():
    """Guards the 8+1 split at its source, so the doc test above cannot be
    satisfied by prose alone while the code drifts."""
    from eqrl import specs as sp
    r = json.loads(REPORT.read_text())
    keys = set(r["pvt_all_pass"])
    known = set(sp.BRIEF_SPECS) | set(sp.INTERNAL_CHECKS)
    # Every brief spec must be scored. Internal checks added since the report
    # was generated may legitimately be absent from it -- what must never
    # happen is the report carrying a key the code does not know about, or a
    # brief spec going missing (the bug where a NaN eye deleted eye_h/eye_w).
    assert set(sp.BRIEF_SPECS) <= keys, (
        f"report is missing brief specs: {sorted(set(sp.BRIEF_SPECS) - keys)}")
    assert keys <= known, (
        f"report scores keys the code does not define: {sorted(keys - known)}")


# --------------------------------------------------------------------------
# 4. the seed-vs-search comparison must stay pinned to its artifact
# --------------------------------------------------------------------------
SEEDBENCH = ROOT / "results" / "seed_vs_search.json"


@pytest.mark.skipif(not SEEDBENCH.exists(), reason="no seed_vs_search.json")
def test_seed_benchmark_reproduces_the_report():
    """The 'after CEM' row is scored through the same Objective as the search.

    If it does not reproduce report.json exactly, the harness is measuring under
    different conditions -- and the other two rows, which are the whole point of
    the comparison, mean nothing. This is the check that can actually fail.
    """
    d = json.loads(SEEDBENCH.read_text())
    r = json.loads(REPORT.read_text())
    cem = next(x for x in d["rows"] if x["name"] == "after CEM")

    assert cem["reward_worst_corner"] == pytest.approx(r["curve"][-1], abs=1e-9)
    assert cem["pvt"]["all_pass_everywhere"] is r["pvt_meets_all"]
    assert cem["pvt"]["binding"][0] == r["binding_spec"][0]
    assert cem["pvt"]["binding"][1] == pytest.approx(r["binding_spec"][1], abs=1e-9)
    for k, v in r["margins_worst"].items():
        assert cem["pvt"]["margins"][k] == pytest.approx(v, abs=1e-9), k


@pytest.mark.skipif(not SEEDBENCH.exists(), reason="no seed_vs_search.json")
def test_docs_quote_the_measured_seed_numbers_not_the_stale_ones():
    """HANDOVER 5 carried "box centre 0.522 (5/9), analytic 0.999 (9/9)" long
    after the scorer, topology and corner set had all changed under it."""
    d = json.loads(SEEDBENCH.read_text())
    by = {x["name"]: x for x in d["rows"]}
    seed, cem = by["analytic seed"], by["after CEM"]

    blob = _read("HANDOVER.md") + _read("README.md") + _read("VERIFY.md")
    assert "0.522" not in blob and "0.999 (9/9)" not in blob, (
        "the stale typical-corner seed figures are back in the docs")

    # the figures the docs now quote must come from the artifact
    assert f"{seed['pvt']['brief_specs_passed']} / 8" in blob \
        or f"{seed['pvt']['brief_specs_passed']}/8" in blob
    assert f"{seed['reward_ungated']:.4f}" in blob, (
        f"docs should quote the measured seed reward {seed['reward_ungated']:.4f}")
    assert f"{cem['reward_ungated']:.4f}" in blob
