"""The report's headline claims, checked against the artifacts they come from.

The document generator reads every number it quotes out of `results/*.json` and
the saved model's `validation.json`, so the prose cannot disagree with the data
by a typo. What it CAN do is quote a number that has quietly become wrong --
an evaluation rerun with fewer specs, a stale design artifact, a coverage curve
that no longer supports the sentence built around it.

So this file checks the claims themselves. A report is a set of claims, and the
rest of this project checks its claims; the report should not be the exception.

Pure tier: reads committed JSON, runs no simulator.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RESULTS = ROOT / "results"


@pytest.fixture(scope="module")
def facts():
    from make_report_docx import load_facts
    return load_facts()


# --------------------------------------------------------------------------
# the coverage claim: "solved X% within N candidates"
# --------------------------------------------------------------------------
def test_coverage_only_ever_increases(facts):
    """Trying MORE candidates cannot solve fewer specifications.

    If this ever fails the curve is being computed wrongly, and the sentence
    "met within N candidates" means nothing.
    """
    for key in ("pol", "cem"):
        at = facts[key]["at"]
        vals = [at(k) for k in range(1, 9)]
        assert vals == sorted(vals), f"{key} coverage is not monotone: {vals}"
        assert 0.0 <= vals[-1] <= 1.0


def test_the_agent_reaches_its_plateau_before_the_search_does(facts):
    """The central comparison the report makes.

    The agent gets to its coverage in `n_at_plateau` candidates; the search
    needs `n_cem_match` to match it. If the search ever got there first, the
    report's claim would be backwards.
    """
    assert facts["n_cem_match"] is not None, (
        "the search never reaches the agent's coverage; the report's "
        "comparison does not hold")
    assert facts["n_at_plateau"] < facts["n_cem_match"]


def test_using_both_methods_is_at_least_as_good_as_either(facts):
    """"Both together" is a union, so it cannot be smaller than its parts."""
    assert facts["union"] >= facts["pol"]["solved"]
    assert facts["union"] >= facts["cem"]["solved"]
    assert facts["union"] <= facts["n_specs"]


def test_the_per_proposal_rate_recomputes_from_the_raw_proposals(facts):
    """Guards against the one figure in this repo that does not reconcile.

    README.md states a 35.2 % per-proposal rate for the agent; the committed
    artifact records 45 of 80 proposals passing 20-corner verification, which
    is 56.2 %. The report avoids the discrepancy by computing from the raw
    `real_pass` flags rather than quoting the prose, and this pins that the
    computation is what it claims to be.
    """
    pol = facts["pol"]
    assert pol["n_prop"] > 0
    assert pol["per_proposal"] == pytest.approx(pol["n_pass"] / pol["n_prop"])
    assert 0.0 < pol["per_proposal"] <= 1.0


# --------------------------------------------------------------------------
# the timing claim
# --------------------------------------------------------------------------
def test_the_timing_sample_is_real_and_positive(facts):
    w = facts["walls"]
    assert len(w) >= 10, f"only {len(w)} timed runs behind the median"
    assert all(t > 0 for t in w)
    assert w == sorted(w)


# --------------------------------------------------------------------------
# the worked example in section 7.3
# --------------------------------------------------------------------------
def test_the_worked_example_actually_passed_everywhere(facts):
    """The report prints this design's scorecard as proof. If the artifact
    ever held a failing design, the report would be presenting a failure as a
    success."""
    d = facts["design"]
    assert d is not None, "results/report_design.json is missing"
    assert d["all_pass"] is True
    assert d["ok"] is True
    rows = d["scorecard"]
    assert len(rows) == 10, f"expected ten conditions, got {len(rows)}"
    assert all(r["pass"] for r in rows), (
        f"failing rows: {[r['spec'] for r in rows if not r['pass']]}")


def test_the_worked_example_was_verified_at_every_corner(facts):
    """A pass means all 20 corners were simulated. The report says so, and the
    artifact has to back it."""
    d, n = facts["design"], len(facts["corners"])
    assert str(n) in d["specs_passed"], d["specs_passed"]
    assert not d["failed_specs"]


def test_the_worked_example_meets_the_briefs_own_numbers(facts):
    """It is the specification from the problem statement, so the limits it was
    held to must be the brief's, not something easier."""
    rows = {r["spec"]: r["required"] for r in facts["design"]["scorecard"]}
    for spec, expected in (("power", "15 mW"), ("eye_h", "100 mV"),
                           ("eye_w", "0.4 UI"), ("hd3", "-30 dBc"),
                           ("noise", "1.5 mVrms"), ("area", "0.05 mm2")):
        assert expected in rows[spec], f"{spec}: {rows[spec]!r}"


# --------------------------------------------------------------------------
# the figures the document embeds
# --------------------------------------------------------------------------
def test_every_figure_the_report_embeds_exists():
    """A missing figure is skipped silently by the generator, so the document
    would still build -- one caption short and nobody the wiser."""
    figs = ROOT / "docs" / "figures"
    if not figs.exists():
        pytest.skip("figures not built yet (run scripts/make_report_figures.py)")
    for name in ("fig1a_stage1.png", "fig1b_stage2.png", "fig2_flow.png",
                 "fig3_physics_anchor.png", "fig4_surrogate_agreement.png",
                 "fig5_coverage.png", "fig6_time.png", "fig7_eye.png",
                 "fig8_ac.png"):
        assert (figs / name).exists(), f"{name} is missing"
