"""Pure-Python tests: no ngspice, no PDK, target runtime < 1 s.

Most of the defects this suite guards against are arithmetic or bookkeeping
errors that never needed a simulator to catch. Keeping them here means a
regression is caught for free instead of costing an ~8 s ngspice launch.
"""
import math

import numpy as np
import pytest

from eqrl import dfe, netlist as nl, pdk, specs as sp
from eqrl.netlist import CtleParams, PARAM_BOUNDS, PARAM_NAMES
from eqrl.spice import Metrics


# --------------------------------------------------------------------------
# device value <-> geometry round trips
# --------------------------------------------------------------------------
@pytest.mark.parametrize("R", [100.0, 450.0, 1200.0, 5000.0, 10000.0])
def test_res_geometry_round_trip(R):
    l, mult = nl.res_geometry(R)
    assert nl.RES_L_MIN <= l <= nl.RES_L_MAX
    assert 1 <= mult <= nl.RES_MULT_MAX
    assert nl.res_value(l, mult) == pytest.approx(R, rel=1e-6)


def test_res_geometry_uses_parallel_units_below_contact_resistance():
    """A single unit cannot go below the ~334 ohm contact resistance."""
    l, mult = nl.res_geometry(100.0)
    assert mult > 1


@pytest.mark.parametrize("C", [30e-15, 300e-15, 1e-12, 3e-12])
def test_cap_side_round_trip(C):
    s = nl.cap_side(C)
    assert nl.CAP_S_MIN <= s <= nl.CAP_S_MAX
    assert nl.cap_value(s) == pytest.approx(C, rel=1e-6)


def test_cap_side_clamps_and_geometry_reports_realised_value():
    """At the low Cs bound the square hits its minimum side and the realised
    capacitance differs from the requested one. geometry() must report what is
    actually instantiated, not what was asked for."""
    C_req = 5e-15                           # below what a 2 um square can make
    s = nl.cap_side(C_req)
    assert s == nl.CAP_S_MIN
    realised = nl.cap_value(s)
    assert realised > C_req      # clamped UP to the minimum square
    g = CtleParams(Cs=C_req).geometry()
    assert g["cs_actual"] == pytest.approx(realised)


# --------------------------------------------------------------------------
# normalised <-> physical parameter mapping
# --------------------------------------------------------------------------
def test_denorm_norm_round_trip():
    u = [i / (len(PARAM_NAMES) - 1) for i in range(len(PARAM_NAMES))]
    phys = nl.denorm(u)
    back = nl.norm(phys)
    assert back == pytest.approx(u, abs=1e-9)


def test_denorm_hits_bounds_exactly():
    lo = nl.denorm([0.0] * len(PARAM_NAMES))
    hi = nl.denorm([1.0] * len(PARAM_NAMES))
    for n in PARAM_NAMES:
        assert lo[n] == pytest.approx(PARAM_BOUNDS[n][0], rel=1e-9)
        assert hi[n] == pytest.approx(PARAM_BOUNDS[n][1], rel=1e-9)


def test_log_params_are_geometric_at_midpoint():
    mid = nl.denorm([0.5] * len(PARAM_NAMES))
    for n in nl.LOG_PARAMS:
        lo, hi = PARAM_BOUNDS[n]
        assert mid[n] == pytest.approx(math.sqrt(lo * hi), rel=1e-9)


def test_denorm_output_constructs_ctle_params():
    """denorm() must produce exactly the CtleParams design-variable kwargs."""
    p = CtleParams(**nl.denorm([0.5] * len(PARAM_NAMES)))
    assert p.W > 0 and p.Itail > 0


# --------------------------------------------------------------------------
# PVT corners
# --------------------------------------------------------------------------
def test_pvt_grid_sizes():
    assert len(pdk.pvt_grid("quick")) == 9
    assert len(pdk.pvt_grid("spec")) == 20
    assert len(pdk.pvt_grid("full")) == 60


def test_pvt_grid_rejects_unknown_mode():
    with pytest.raises(ValueError):
        pdk.pvt_grid("nonsense")


def test_spec_grid_is_the_briefs_cross_product():
    """5 process x VDD+-5% x {0,125} C, R/C typical."""
    g = pdk.pvt_grid("spec")
    assert {c.process for c in g} == set(pdk.PROCESS_CORNERS)
    assert {c.rc for c in g} == {None}
    assert {round(c.vdd, 3) for c in g} == {1.71, 1.89}
    assert {c.temp for c in g} == {0.0, 125.0}


def test_full_grid_adds_the_rc_axis():
    assert {c.rc for c in pdk.pvt_grid("full")} == {None, "ll", "hh"}


# These are the section names that actually exist in sky130.lib.spice; the
# naming is irregular (tt+ll is "ll", not "tt_ll") and getting it wrong picks
# the wrong models silently.
EXPECTED_SECTIONS = {
    ("tt", None): "tt", ("tt", "ll"): "ll", ("tt", "hh"): "hh",
    ("ss", None): "ss", ("ss", "ll"): "ss_ll", ("ss", "hh"): "ss_hh",
    ("ff", None): "ff", ("ff", "ll"): "ff_ll", ("ff", "hh"): "ff_hh",
    ("sf", None): "sf", ("sf", "ll"): "sf_ll", ("sf", "hh"): "sf_hh",
    ("fs", None): "fs", ("fs", "ll"): "fs_ll", ("fs", "hh"): "fs_hh",
}


@pytest.mark.parametrize("key,expected", sorted(
    EXPECTED_SECTIONS.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")))
def test_lib_section_naming(key, expected):
    proc, rc = key
    assert pdk.Corner(proc, 1.8, 27.0, rc=rc).lib_section == expected


def test_every_grid_corner_maps_to_a_known_section():
    known = set(EXPECTED_SECTIONS.values())
    assert {c.lib_section for c in pdk.pvt_grid("full")} <= known


# --------------------------------------------------------------------------
# PRBS / stimulus
# --------------------------------------------------------------------------
def test_prbs_has_period_127_and_is_balanced():
    b = dfe.prbs(300)
    assert set(np.unique(b)) <= {0, 1}
    assert np.array_equal(b[:127], b[127:254])       # PRBS-7 period
    ones = int(b[:127].sum())
    assert ones == 64                                 # 2^6 ones per period


def test_nrz_pwl_time_points_are_monotonic():
    bits = dfe.prbs(32)
    s = dfe.nrz_pwl(bits, 200e-12, 0.05, 1.1)
    nums = [float(x) for x in s[4:-1].split()]
    t, v = nums[0::2], nums[1::2]
    assert all(b >= a for a, b in zip(t, t[1:]))
    assert min(v) == pytest.approx(1.05) and max(v) == pytest.approx(1.15)


def test_nrz_pwl_invert_mirrors_levels():
    bits = np.array([0, 1, 0, 1], dtype=np.int8)
    a = dfe.nrz_pwl(bits, 200e-12, 0.05, 1.1)
    b = dfe.nrz_pwl(bits, 200e-12, 0.05, 1.1, invert=True)
    va = [float(x) for x in a[4:-1].split()][1::2]
    vb = [float(x) for x in b[4:-1].split()][1::2]
    assert [round(2.2 - x, 9) for x in va] == [round(x, 9) for x in vb]


# --------------------------------------------------------------------------
# eye measurement
# --------------------------------------------------------------------------
def _synthetic_eye(height_v: float, ui: float = 200e-12, n_ui: int = 80,
                   n_per_ui: int = 64, jitter: float = 0.0) -> tuple:
    """A clean NRZ waveform with a known vertical opening."""
    rng = np.random.default_rng(0)
    bits = rng.integers(0, 2, n_ui) * 2 - 1
    t = np.linspace(0, n_ui * ui, n_ui * n_per_ui, endpoint=False)
    v = np.repeat(bits, n_per_ui).astype(float) * height_v / 2.0
    if jitter:
        v = v + rng.normal(0, jitter, v.size)
    return t, v


def test_eye_from_waveform_measures_a_known_opening():
    t, v = _synthetic_eye(0.3)
    e = dfe.eye_from_waveform(t, v, 200e-12, skip_ui=5)
    assert e.height_v == pytest.approx(0.3, rel=0.05)
    assert e.width_ui > 0.9


def test_eye_from_waveform_reports_a_closed_eye_as_closed():
    """Regression for the circular width metric (HANDOVER bug 5).

    Splitting traces by sign *at each phase* separates the groups by
    construction, so width was always 1.000 UI and a closed eye could never be
    reported. Noise larger than the opening must collapse the measurement.
    """
    clean = dfe.eye_from_waveform(*_synthetic_eye(0.3), 200e-12, skip_ui=5)
    t, v = _synthetic_eye(0.02, jitter=0.15)
    closed = dfe.eye_from_waveform(t, v, 200e-12, skip_ui=5)
    assert closed.width_ui < 0.5 * clean.width_ui
    assert closed.height_v < 0.5 * clean.height_v


def test_eye_width_counts_only_a_contiguous_opening():
    """Two disjoint open phase regions must not report as one wide eye."""
    ui, n_ui, n_per_ui = 200e-12, 60, 64
    rng = np.random.default_rng(1)
    bits = rng.integers(0, 2, n_ui) * 2 - 1
    # Two disjoint open regions, each a quarter of the UI, separated by two
    # closed bands. Total open = 1/2 UI; largest usable (contiguous) = 1/4.
    shape = np.zeros(n_per_ui)
    shape[0: n_per_ui // 4] = 1.0
    shape[n_per_ui // 2: 3 * n_per_ui // 4] = 1.0
    v = (np.repeat(bits, n_per_ui).astype(float) * 0.15
         * np.tile(shape, n_ui))
    t = np.linspace(0, n_ui * ui, n_ui * n_per_ui, endpoint=False)
    e = dfe.eye_from_waveform(t, v, ui, skip_ui=5)
    # the old metric counted every open phase and reported ~0.5 UI
    assert e.width_ui == pytest.approx(0.25, abs=0.05)


def test_eye_from_waveform_handles_degenerate_input():
    t = np.linspace(0, 1e-9, 4)
    assert math.isnan(dfe.eye_from_waveform(t, np.zeros(4), 200e-12).height_v)
    t = np.linspace(0, 20e-9, 1000)
    e = dfe.eye_from_waveform(t, np.ones(1000) * 0.1, 200e-12, skip_ui=5)
    assert math.isnan(e.height_v)          # DC: no bipolar transitions


# --------------------------------------------------------------------------
# netlist generation
# --------------------------------------------------------------------------
def test_ctle_stage_has_every_device_and_no_tap():
    """The CTLE is now a standalone stage: the tap must NOT be on its loads."""
    n = CtleParams().core_netlist()
    for inst in ("X1", "X2", "XRs", "XCs", "XRLp", "XRLn", "CLp", "CLn",
                 "Iref", "Xref", "Xt1", "Xt2"):
        assert f"\n{inst} " in "\n" + n, f"missing {inst}"
    assert nl.NFET in n and nl.RES in n and nl.CAP in n
    # the old merged topology drained the tap onto the CTLE loads
    assert "vfbp" not in n and "vfbn" not in n
    assert "outp_ctle" in n and "outp_dfe" not in n


def test_summer_stage_is_a_real_summing_node():
    """Both transconductors must drain into the SAME load pair -- that sharing
    IS the summer. If GmF had its own loads it would be a separate stage and
    nothing would sum."""
    n = CtleParams().summer_netlist()
    for inst in ("XRsp", "XRsn", "XG1p", "XG1n", "XGFp", "XGFn",
                 "XTsum1", "XTsumf"):
        assert f"\n{inst} " in "\n" + n, f"missing {inst}"
    nodes = {}
    for line in n.splitlines():
        if not line.startswith("X"):
            continue
        t = [x for x in line.split() if "=" not in x]
        nodes[t[0]] = t[1:-1]
    # drain of Gm1, drain of GmF and the load all land on the same node
    assert nodes["XG1n"][0] == nodes["XGFn"][0] == nodes["XRsp"][1] == "outp_dfe"
    assert nodes["XG1p"][0] == nodes["XGFp"][0] == nodes["XRsn"][1] == "outn_dfe"
    # Gm1 is driven by the CTLE, GmF by the feedback
    assert nodes["XG1p"][1] == "outp_ctle" and nodes["XG1n"][1] == "outn_ctle"
    assert nodes["XGFp"][1] == "vfbp" and nodes["XGFn"][1] == "vfbn"
    # separate tails -- the two pairs must not share a current source
    assert nodes["XG1p"][2] == "sgm1" and nodes["XGFp"][2] == "sgmf"


def test_reference_current_is_sourced_from_the_supply():
    """Iref must source INTO vbias. `Iref vbias 0` drains it (HANDOVER bug 10)."""
    line = [l for l in CtleParams().core_netlist().splitlines()
            if l.startswith("Iref")][0]
    assert line.split()[1:3] == ["vdd", "vbias"]


def test_dfe_static_bias_holds_the_pair_at_common_mode():
    s = nl.dfe_static_bias()
    assert s.count("DC") == 2
    assert f"{nl.VFB_CM:.4g}" in s


def test_dfe_static_tap_steers_both_ways():
    pos, neg = nl.dfe_static_tap(+1.0), nl.dfe_static_tap(-1.0)
    assert pos != neg
    for s in (pos, neg):
        assert f"{nl.VFB_HI:.4g}" in s and f"{nl.VFB_LO:.4g}" in s


def test_dfe_feedback_polarity_is_inverted():
    """q1b (NOT q1) must drive vfbp, or the tap reinforces the post-cursor
    instead of cancelling it (HANDOVER bug 4). Measured, not assumed: GmF is
    crossed the same way as Gm1, so vfbp high drives the summing node the same
    direction a positive symbol does -- verified in
    test_spice.py::test_tap_opposes_the_main_path."""
    s = nl.dfe_decision_path()
    assert "adac_p [q1b] [vfbp]" in s
    assert "adac_n [q1]  [vfbn]" in s or "adac_n [q1] [vfbn]" in s


def test_decision_path_has_two_clocked_stages():
    """Clocked comparator + 1 UI hold, both on the same edge: the second
    captures what the first held BEFORE the edge, giving exactly one UI of
    feedback delay."""
    s = nl.dfe_decision_path()
    assert "acomp" in s and "ahold" in s
    assert s.count("dff_1tap") >= 3          # two instances + the .model
    # the slicer must watch the SUMMING node, not the CTLE output
    assert "V(outp_dfe)-V(outn_dfe)" in s


def test_xspice_instances_start_with_a():
    """XSPICE code-model instances must begin with 'a' or ngspice parses them
    as digital devices (HANDOVER bug 9)."""
    for line in nl.dfe_decision_path().splitlines():
        if any(m in line for m in ("adc_bridge", "dac_bridge", "d_dff")):
            continue                      # .model cards
        tok = line.split()[0] if line.split() else ""
        if tok.startswith(("adc_", "adac_", "aff")):
            assert tok[0] == "a"


@pytest.mark.parametrize("ui", [200e-12, 100e-12, 400e-12])
def test_dfe_clock_period_follows_the_requested_ui(ui):
    """Regression: the DFE clock was hardcoded at 200 ps while the PRBS
    followed the requested UI, so any rate but 5 Gb/s was silently wrong."""
    s = nl.dfe_decision_path(ui_s=ui)
    vclk = [l for l in s.splitlines() if l.startswith("Vclk")][0]
    period = float(vclk.split("PULSE(")[1].rstrip(")").split()[-1])
    assert period == pytest.approx(ui, rel=1e-9)


def test_assembled_closed_loop_deck_uses_the_requested_ui():
    """The UI must survive the whole path: tran_eye -> _assemble -> netlist."""
    from eqrl.spice import Simulator
    import inspect
    src = inspect.getsource(Simulator._assemble)
    assert "ui" in inspect.signature(Simulator._assemble).parameters, \
        "_assemble must accept a ui so the DFE clock can track the bit rate"


def test_area_breakdown_sums():
    a = CtleParams().area_um2()
    parts = ["input_pair", "tail", "summer", "resistors", "cap"]
    assert a["total_um2"] == pytest.approx(sum(a[k] for k in parts))
    assert a["total_mm2"] == pytest.approx(a["total_um2"] * 1e-6)


def test_mirror_uses_identical_unit_devices():
    """A current mirror ratios by device MULTIPLICITY, not by unequal finger
    widths -- 7.5 um/finger against 10 um/finger does not match."""
    n = CtleParams().core_netlist()
    ref = [l for l in n.splitlines() if l.startswith("Xref")][0]
    tail = [l for l in n.splitlines() if l.startswith("Xt1")][0]

    def geom(line):
        d = dict(kv.split("=") for kv in line.split() if "=" in kv)
        return float(d["W"]) / float(d["nf"]), float(d["L"])

    w_ref, l_ref = geom(ref)
    w_tail, l_tail = geom(tail)
    assert w_ref == pytest.approx(w_tail), "finger widths must match"
    assert l_ref == pytest.approx(l_tail), "lengths must match"


# --------------------------------------------------------------------------
# spec scoring
# --------------------------------------------------------------------------
def _good_metrics() -> Metrics:
    return Metrics(
        ok=True, dc_gain_db=-0.1, peak_gain_db=8.9, peaking_db=9.0,
        f_peak=2.0e9, gain_2g5_db=8.0, bw_3db=4e9, power_w=2.8e-3,
        hd3_db=-66.0, vn_in_rms=0.5e-3, area_mm2=2.6e-4,
        eye_height_v=0.17, eye_width_ui=0.69, saturated=True,
        tail_saturated=True,
    )


def test_a_good_design_passes_everything():
    sc = sp.score(_good_metrics(), sp.EqualizerSpec())
    assert sp.all_pass(sc), sc["pass"]
    assert sc["reward"] > 0.9


def test_failed_simulation_scores_worst():
    sc = sp.score(Metrics(ok=False, note="ac-failed"), sp.EqualizerSpec())
    assert sc["reward"] == -1.0
    assert not sp.all_pass(sc)


def test_nan_metrics_never_score_as_a_pass():
    """Every soft helper returns 0.0 for NaN so a failed analysis reads as a
    failure rather than silently passing (HANDOVER bug 3)."""
    m = _good_metrics()
    for field in ("peaking_db", "f_peak", "power_w", "hd3_db", "vn_in_rms",
                  "area_mm2"):
        bad = _good_metrics()
        setattr(bad, field, float("nan"))
        sc = sp.score(bad, sp.EqualizerSpec())
        assert not sp.all_pass(sc), f"NaN {field} passed"
        assert math.isfinite(sc["reward"])
    assert sp.all_pass(sp.score(m, sp.EqualizerSpec()))


def test_bias_failure_caps_the_reward():
    m = _good_metrics()
    m.saturated = False
    sc = sp.score(m, sp.EqualizerSpec())
    assert sc["reward"] <= 0.2
    assert not sc["pass"]["bias"]


def test_eye_is_scored_when_present_and_FAILED_when_missing():
    """This test previously asserted the opposite -- that a missing eye removes
    the eye specs from the scorecard. That WAS the behaviour, and it was a bug:
    all_pass() then returned True over the remaining seven specs. The eye specs
    must stay and fail."""
    with_eye = sp.score(_good_metrics(), sp.EqualizerSpec())
    assert with_eye["pass"]["eye_h"] and with_eye["pass"]["eye_w"]
    m = _good_metrics()
    m.eye_height_v = float("nan")
    m.eye_width_ui = float("nan")
    without = sp.score(m, sp.EqualizerSpec())
    assert without["pass"]["eye_h"] is False
    assert without["pass"]["eye_w"] is False
    # ...unless the caller explicitly declared an AC-only evaluation
    optout = sp.score(m, sp.EqualizerSpec(require_eye=False))
    assert "eye_h" not in optout["pass"]


def test_eye_spec_field_names_match_their_units():
    """eye_width_min_ui bounds the WIDTH (in UI); eye_height_min_v the HEIGHT
    (in volts). They used to be crossed, which the scorer crossed back."""
    spec = sp.EqualizerSpec()
    assert spec.eye_width_min_ui == pytest.approx(0.4)
    assert spec.eye_height_min_v == pytest.approx(0.100)
    m = _good_metrics()
    m.eye_width_ui = 0.3                      # below the 0.4 UI limit
    assert not sp.score(m, spec)["pass"]["eye_w"]
    m = _good_metrics()
    m.eye_height_v = 0.05                     # below the 100 mV limit
    assert not sp.score(m, spec)["pass"]["eye_h"]


def test_gain_at_nyquist_is_reported_but_not_constrained():
    """gain_2g5_min_db is not in the brief (HANDOVER 11); it must not veto an
    otherwise-passing design."""
    m = _good_metrics()
    m.gain_2g5_db = -6.0
    sc = sp.score(m, sp.EqualizerSpec())
    assert sp.all_pass(sc), "an invented spec is vetoing a valid design"


def test_pass_keys_are_stable_and_complete():
    """Regression for the design.py KeyError: anything aggregating this dict
    must be derived from it, never from a hand-maintained key list."""
    sc = sp.score(_good_metrics(), sp.EqualizerSpec())
    assert set(sc["pass"]) == {
        "peaking", "fpeak", "power", "hd3", "noise", "area", "bias",
        "eye_h", "eye_w"}


def test_reward_is_comparable_across_candidates():
    """Weighted sum must be normalised by the weights actually used, so two
    designs scored the same way are directly comparable."""
    a = sp.score(_good_metrics(), sp.EqualizerSpec())
    worse = _good_metrics()
    worse.f_peak = 4.0e9
    b = sp.score(worse, sp.EqualizerSpec())
    assert 0.0 <= b["reward"] < a["reward"] <= 1.0


# --------------------------------------------------------------------------
# parallel-evaluation bookkeeping
# --------------------------------------------------------------------------
def test_each_candidate_corner_pair_gets_its_own_workdir(tmp_path):
    """Analyses write fixed filenames (ac.dat, tran.dat, eye.dat), so two
    workers sharing a directory silently read each other's dumps. The corners of
    one candidate run concurrently, so the directory must be keyed on the PAIR."""
    from eqrl import pdk
    from eqrl.env import Objective, _eval_task
    import inspect
    src = inspect.getsource(_eval_task)
    assert "corner.name" in src, "workdir must be keyed on the corner too"
    # two corners of the same candidate must not collide
    key = Objective._digest((0.5,) * len(PARAM_NAMES))
    a = f"c{key}/" + pdk.Corner("tt", 1.8, 27.0).name
    b = f"c{key}/" + pdk.Corner("ss", 1.71, 125.0).name
    assert a != b


# --------------------------------------------------------------------------
# the Gymnasium interface (the "drop in PPO/TD3" claim)
# --------------------------------------------------------------------------
class _StubObjective:
    """Objective-shaped stand-in that returns extreme metrics without SPICE."""

    def __init__(self, spec):
        self.spec = spec
        self.n_sims = 0

    def params_from_u(self, u):
        return CtleParams(**nl.denorm(list(u)))

    def __call__(self, u):
        from eqrl.env import EvalResult
        m = Metrics(ok=True, dc_gain_db=200.0, peaking_db=50.0,
                    f_peak=2.0e10, gain_2g5_db=-300.0, power_w=1.0,
                    hd3_db=-10.0, vn_in_rms=1.0, area_mm2=1.0,
                    saturated=True)
        self.n_sims += 1
        sc = sp.score(m, self.spec)
        return EvalResult(u=tuple(u), reward=sc["reward"], scored=sc,
                          params=self.params_from_u(u),
                          per_corner={"tt": sc})


def test_env_observations_stay_inside_the_declared_space():
    """f_peak/2.5e9 alone exceeds the declared +-5 box for any candidate peaking
    above 12.5 GHz, and gymnasium's env_checker rejects an env whose
    observations escape its own observation_space."""
    gym = pytest.importorskip("gymnasium")
    from eqrl.env import CtleEnv
    env = CtleEnv(_StubObjective(sp.EqualizerSpec()), max_steps=3, seed=0)
    obs, _ = env.reset(seed=0)
    assert env.observation_space.contains(obs), obs
    for _ in range(3):
        obs, r, term, trunc, _ = env.step(env.action_space.sample())
        assert env.observation_space.contains(obs), obs
        assert math.isfinite(r)
        if term or trunc:
            break


def test_env_passes_the_gymnasium_env_checker():
    gym = pytest.importorskip("gymnasium")
    from gymnasium.utils.env_checker import check_env
    from eqrl.env import CtleEnv
    check_env(CtleEnv(_StubObjective(sp.EqualizerSpec()), max_steps=3, seed=0),
              skip_render_check=True)


def test_partial_results_stay_scoreable_and_flagged():
    """A partial result must still produce a full pass/fail scorecard.

    If `ok` were cleared on a partial failure, score() would return an empty
    `pass` dict, and any aggregation over it would record no failures at all --
    a corner that did not simulate would silently read as a passing corner.
    """
    m = _good_metrics()
    m.hd3_db = float("nan")
    m.partial = True
    sc = sp.score(m, sp.EqualizerSpec())
    assert sc["pass"], "a partial result must still be scoreable"
    assert not sc["pass"]["hd3"]
    assert not sp.all_pass(sc)


def test_eval_result_records_which_corner_it_scored():
    """`scored` is the WORST corner, not the typical one. Anything that labels
    it must be able to say which -- mislabelling it made a correct exported
    deck look 25% wrong, because tt_ll pushes f_peak up ~33% over tt."""
    from eqrl.env import EvalResult
    import dataclasses
    assert "scored_corner" in {f.name for f in dataclasses.fields(EvalResult)}


def test_report_distinguishes_typical_from_worst_corner():
    import inspect
    from eqrl import design
    src = inspect.getsource(design.run_design)
    assert '"typ_corner"' in src
    assert '"worst_search_corner"' in src
    # the typical corner must be evaluated explicitly: the spec/full PVT grids
    # never contain it (they use VDD+-5% and 0/125 C only)
    assert "typ_scored" in src


def test_instance_names_are_unique_case_insensitively():
    """ngspice folds case, so `XT1` and `Xt1` are the SAME instance and the deck
    dies with 'device already exists, bail out'. Caught exactly that when the
    summing stage was added."""
    names = [l.split()[0] for l in CtleParams().full_netlist().splitlines()
             if l.strip() and not l.startswith("*")]
    lowered = [n.lower() for n in names]
    dupes = {n for n in lowered if lowered.count(n) > 1}
    assert not dupes, f"instance name collision: {dupes}"
    assert len(names) == 20


# --------------------------------------------------------------------------
# PDK device-size limits
# --------------------------------------------------------------------------
def test_w_mult_never_exceeds_the_model_bin_limit():
    """A single sky130 nfet instance may not exceed 100 um of TOTAL width --
    the widest model bin is 7..100 um and beyond it ngspice refuses to run
    ("could not find a valid modelname"). nf does NOT help: binning is on total
    W, measured W=150/nf=30 (5 um per finger) still fails."""
    for w in (1.0, 99.9, 100.0, 100.1, 150.0, 16000.0):
        w_unit, m = nl.w_mult(w)
        assert w_unit <= nl.W_MAX_UM + 1e-9, (w, w_unit, m)
        assert m >= 1
        assert w_unit * m == pytest.approx(w)


@pytest.mark.parametrize("params", [
    {},
    {"Wtail": 200.0, "Ig1": 4e-3, "tap_pct": 30.0, "Itail": 50e-6, "W": 100.0},
    {"Wtail": 5.0, "Ig1": 100e-6, "tap_pct": 1.0, "Itail": 2e-3},
])
def test_no_instance_exceeds_the_width_limit_anywhere_in_the_box(params):
    """Every corner of the parameter box must emit legal device widths. The
    summing-stage tails are the risk: wg1_tail = Wtail*Ig1/Itail reaches
    ~16000 um at the extremes."""
    for line in CtleParams(**params).full_netlist().splitlines():
        if not line.startswith("X") or nl.NFET not in line:
            continue
        kv = dict(x.split("=", 1) for x in line.split() if "=" in x)
        assert float(kv["W"]) <= nl.W_MAX_UM + 1e-9, line


# --------------------------------------------------------------------------
# ngspice failure detection -- nothing may fail silently
# --------------------------------------------------------------------------
@pytest.mark.parametrize("log,tag", [
    ("could not find a valid modelname", "model-bin-miss"),
    ("device already exists, bail out", "duplicate-instance"),
    ("Error on line 42: bogus", "deck-syntax"),
    ("singular matrix: check nodes", "singular-matrix"),
    ("Too many iterations without convergence", "no-convergence"),
    ("doAnalyses: TRAN: Timestep too small", "timestep-too-small"),
    ("MIF-ERROR: bad port", "xspice-error"),
    ("unknown subckt: foo", "missing-subckt"),
    ("Simulation interrupted due to error!", "sim-interrupted"),
])
def test_fatal_ngspice_conditions_are_detected(log, tag):
    """ngspice prints most fatal errors and STILL EXITS 0, so the log is the
    only reliable failure signal. A missed pattern means a broken deck is
    scored as a bad design and silently steers the optimiser."""
    from eqrl.spice import Simulator
    assert Simulator.scan_log(log) == tag


@pytest.mark.parametrize("log", [
    "Note: No compatibility mode selected!",
    'Note: No ".plot", ".print", or ".fourier" lines; no simulations run',
    "Using SPARSE 1.3 as Direct Linear Solver",
    "",
])
def test_benign_notes_are_not_flagged_as_failures(log):
    """Crying wolf is as bad as missing the wolf: these appear in every healthy
    run and must not abort a candidate."""
    from eqrl.spice import Simulator
    assert Simulator.scan_log(log) is None


def test_fatal_detection_survives_a_realistic_log():
    from eqrl.spice import Simulator
    log = ("Note: No compatibility mode selected!\n"
           "Circuit: * auto-generated deck\n"
           "Error on line:\n  m.xtsumf... w= 1.5e+02\n"
           "could not find a valid modelname\n"
           "    Simulation interrupted due to error!\n")
    assert Simulator.scan_log(log) is not None


# --------------------------------------------------------------------------
# trimmed model library
# --------------------------------------------------------------------------
def test_trimlib_refuses_an_unknown_section(tmp_path):
    from eqrl import trimlib
    pytest.importorskip("eqrl.pdk")
    try:
        trimlib.build("definitely_not_a_section", tmp_path)
    except Exception as e:
        assert "not found" in str(e).lower()
    else:
        pytest.fail("should have refused an unknown section")


def test_trimlib_reports_unvalidated_as_none(tmp_path):
    """A trimmed library that has not been proven equal to the full one must
    read as 'unavailable', so the caller falls back instead of trusting it."""
    from eqrl import trimlib
    assert trimlib.validated_lib("tt", tmp_path) is None


def test_trimlib_manifest_is_invalidated_by_a_different_pdk(tmp_path):
    """If the PDK changes under us, every previous validation is void."""
    from eqrl import trimlib
    trimlib.save_manifest(tmp_path, {
        "pdk_fingerprint": "not-the-current-pdk",
        "sections": {"tt": {"path": str(tmp_path / "x"), "validated": True}},
    })
    assert trimlib.validated_lib("tt", tmp_path) is None


def test_trimlib_always_keep_covers_the_known_traps():
    """all.spice carries `.option scale=1.0u` and r+c sets rm3/rcvia3 which the
    MIM cap reads. Dropping all.spice fails loudly; dropping r+c does NOT --
    it quietly changes the capacitor. Both must be pinned."""
    from eqrl import trimlib
    assert "all.spice" in trimlib.ALWAYS_KEEP
    assert any("r+c" in k for k in trimlib.ALWAYS_KEEP)


def test_resource_failures_are_distinguished_from_design_failures():
    """An OOM-killed ngspice must not be scored as a bad design. On a 7.6 GiB
    box, 16 parallel launches can trip the OOM killer, and a SIGKILL that reads
    as 'this design is bad' would steer the search away from a good region for
    no reason."""
    import inspect
    from eqrl.spice import Simulator
    src = inspect.getsource(Simulator._run)
    assert "killed-oom" in src
    assert "-9" in src


def test_safe_job_count_respects_memory_and_cores():
    """Parallelism must be capped by RAM, not just core count. Measured: 16
    ngspice processes at ~322 MiB each is 5.0 GiB, which on a 7.6 GiB box
    OOM-killed unrelated processes mid-run."""
    import os
    from eqrl.spice import safe_job_count
    n = safe_job_count()
    assert 1 <= n <= (os.cpu_count() or 4)
    # an explicit request is a ceiling, never a floor
    assert safe_job_count(2) <= 2
    assert safe_job_count(1) == 1
    # a trimmed library holds less model text, so more workers fit
    assert safe_job_count(64, trimmed=True) >= safe_job_count(64, trimmed=False)


def test_safe_job_count_survives_an_unreadable_meminfo(monkeypatch):
    """If memory cannot be measured, fall back to the core count rather than
    refusing to run."""
    from eqrl import spice
    monkeypatch.setattr(spice, "available_mib", lambda: 1 << 20)
    assert spice.safe_job_count(8) == 8


# --------------------------------------------------------------------------
# DFE tap weight parameterisation
# --------------------------------------------------------------------------
def test_tap_branch_is_a_scaled_copy_of_the_main_branch():
    """A current-steering tap must be the SAME device at the SAME current
    density, scaled. Then gm tracks tap_pct linearly. Sizing W and I
    independently makes gm go as sqrt(W*I), so "30% tap" is not 30%."""
    for tp in (5.0, 10.0, 30.0):
        p = CtleParams(Wg1=40.0, Ig1=1e-3, tap_pct=tp)
        assert p.Igf == pytest.approx(1e-3 * tp/100)
        assert p.Wgf == pytest.approx(40.0 * tp/100)
        # same current density => same W/I ratio as the main branch
        assert (p.Wgf/p.Igf) == pytest.approx(40.0/1e-3)
        assert p.tap_weight_ratio() == pytest.approx(tp/100)


def test_tap_width_is_floored_at_the_pdk_minimum():
    """Below the minimum drawn width the branch stops being a scaled copy;
    the floor must be applied, not silently produce an illegal device."""
    p = CtleParams(Wg1=2.0, Ig1=1e-3, tap_pct=1.0)
    assert p.Wgf == pytest.approx(nl.W_MIN_UM)


def test_wgf_igf_are_not_independent_design_variables():
    assert "Wgf" not in PARAM_NAMES and "Igf" not in PARAM_NAMES
    assert "tap_pct" in PARAM_NAMES


# --------------------------------------------------------------------------
# analytic initialisation
# --------------------------------------------------------------------------
def test_cem_accepts_a_vector_starting_mean():
    """The analytic initialiser supplies a per-knob starting point."""
    import numpy as np
    from eqrl.agents.cem import CemConfig, optimize
    n = len(PARAM_NAMES)
    cfg = CemConfig(pop=3, iters=1, init_mean=[0.4]*n, stop_on_pass=False)

    class Stub:
        n_sims = n_launches = 0
        _cache: dict = {}
        def __call__(self, u):
            from eqrl.env import EvalResult
            m = Metrics(ok=True, peaking_db=8.0, f_peak=2e9, power_w=2e-3,
                        hd3_db=-60.0, vn_in_rms=5e-4, area_mm2=3e-4,
                        saturated=True)
            sc = sp.score(m, sp.EqualizerSpec())
            return EvalResult(u=tuple(u), reward=sc["reward"], scored=sc,
                              params=CtleParams(**nl.denorm(list(u))),
                              per_corner={"tt": sc})
    res = optimize(Stub(), cfg, verbose=False)
    assert res.best is not None


def test_cem_rejects_a_wrong_length_starting_mean():
    from eqrl.agents.cem import CemConfig, optimize
    cfg = CemConfig(pop=2, iters=1, init_mean=[0.5, 0.5])
    with pytest.raises(ValueError, match="init_mean"):
        optimize(lambda u: None, cfg, verbose=False)


def test_analytic_solve_places_the_output_pole_at_the_target():
    """RL is set by f_p2 = 1/(2*pi*RL*Cout) -- the output pole is what puts the
    peak where it is asked for."""
    from eqrl import analytic
    import math
    gm, cout = 5.72e-3, 66.9e-15
    RL, Rs, Cs = analytic.solve(gm, cout, 8.0, 2.0e9)
    assert RL == pytest.approx(1/(2*math.pi*2.0e9*cout), rel=0.02)
    assert nl.PARAM_BOUNDS["Rs"][0] <= Rs <= nl.PARAM_BOUNDS["Rs"][1]
    assert nl.PARAM_BOUNDS["Cs"][0] <= Cs <= nl.PARAM_BOUNDS["Cs"][1]


def test_analytic_start_is_inside_the_parameter_box():
    """initial_u must be a legal normalised vector or CEM starts out of bounds."""
    from eqrl import analytic
    p = CtleParams(**analytic.FIXED_START, tap_pct=analytic.TAP_PCT_START,
                   RL=811.0, Rs=3453.0, Cs=168e-15, Rsum=1800.0)
    u = nl.norm({k: getattr(p, k) for k in PARAM_NAMES})
    assert len(u) == len(PARAM_NAMES)
    assert all(0.0 <= x <= 1.0 for x in u), u


def test_first_population_contains_the_starting_mean():
    """Sampling from N(mu, std) never yields mu exactly, so an analytically
    derived starting point would never be scored. It must be injected, or the
    search is not guaranteed to do at least as well as its own seed."""
    from eqrl.agents.cem import CemConfig, optimize
    n = len(PARAM_NAMES)
    seed_u = [0.37] * n
    seen = []

    class Stub:
        n_sims = n_launches = 0
        _cache: dict = {}
        def __call__(self, u):
            from eqrl.env import EvalResult
            seen.append(tuple(round(float(x), 6) for x in u))
            m = Metrics(ok=True, peaking_db=8.0, f_peak=2e9, power_w=2e-3,
                        hd3_db=-60.0, vn_in_rms=5e-4, area_mm2=3e-4, saturated=True)
            sc = sp.score(m, sp.EqualizerSpec())
            return EvalResult(u=tuple(u), reward=sc["reward"], scored=sc,
                              params=CtleParams(**nl.denorm(list(u))),
                              per_corner={"tt": sc})

    optimize(Stub(), CemConfig(pop=4, iters=1, init_mean=seed_u,
                               stop_on_pass=False), verbose=False)
    assert tuple(round(x, 6) for x in seed_u) in seen, \
        "the analytic starting point was never evaluated"


def test_reward_has_a_real_step_at_each_spec_boundary():
    """The soft scores saturate at the boundary: a design 10 MHz over the
    2.5 GHz f_peak limit scored 0.99972 against 1.00000 for one that passes.
    That gap is below the noise of a population sample, so the optimiser could
    not prefer a PASSING design over a marginally failing one -- measured, a
    run stalled at 0.998 for three iterations with a spec still failing.
    Crossing a boundary must be worth more than any soft improvement."""
    spec = sp.EqualizerSpec()
    passes = sp.score(_good_metrics(), spec)
    marginal = _good_metrics()
    marginal.f_peak = spec.fpeak_hi * 1.004          # 10 MHz over
    fails = sp.score(marginal, spec)
    assert passes["pass"]["fpeak"] and not fails["pass"]["fpeak"]
    step = passes["reward"] - fails["reward"]
    assert step > 0.005, f"boundary step only {step:.5f} -- too small to search on"


def test_an_all_pass_design_scores_near_one():
    """The hard-pass bonus must not cap a passing design. It does not reach
    exactly 1.0 unless every SOFT score is also 1.0 -- _good_metrics() peaks at
    9 dB against an 8 dB preference, so its shape score is 0.969 by design."""
    sc = sp.score(_good_metrics(), sp.EqualizerSpec())
    assert sp.all_pass(sc)
    assert sc["reward"] > 0.95
    # a design that also hits every soft target exactly must reach 1.0
    ideal = _good_metrics()
    ideal.peaking_db = sp.EqualizerSpec().peaking_target_db
    assert sp.score(ideal, sp.EqualizerSpec())["reward"] == pytest.approx(1.0, abs=1e-6)


def test_more_specs_passed_always_scores_higher():
    """Monotonicity: failing an extra spec must lower the reward."""
    spec = sp.EqualizerSpec()
    base = sp.score(_good_metrics(), spec)["reward"]
    one_bad = _good_metrics(); one_bad.power_w = 20e-3
    two_bad = _good_metrics(); two_bad.power_w = 20e-3; two_bad.hd3_db = -10.0
    r1 = sp.score(one_bad, spec)["reward"]
    r2 = sp.score(two_bad, spec)["reward"]
    assert base > r1 > r2


# --------------------------------------------------------------------------
# a missing eye must FAIL, not silently delete two specs
# --------------------------------------------------------------------------
def test_a_missing_eye_is_a_failure_not_a_vanishing_spec():
    """If the eye simulation does not produce a result, the two eye specs used
    to simply disappear from the pass dict and all_pass() returned True over
    the remaining seven -- a design could be declared compliant by deleting the
    two hardest requirements. A missing eye is a FAILURE."""
    m = _good_metrics()
    m.eye_height_v = float("nan")
    m.eye_width_ui = float("nan")
    sc = sp.score(m, sp.EqualizerSpec())
    assert "eye_h" in sc["pass"] and "eye_w" in sc["pass"], \
        "eye specs vanished from the scorecard"
    assert not sc["pass"]["eye_h"] and not sc["pass"]["eye_w"]
    assert not sp.all_pass(sc), "a design with no eye result must not 'pass'"


def test_a_half_measured_eye_also_fails():
    m = _good_metrics()
    m.eye_width_ui = float("nan")          # height fine, width missing
    sc = sp.score(m, sp.EqualizerSpec())
    assert not sp.all_pass(sc)


def test_ac_only_evaluation_is_still_scoreable():
    """Scoring an AC-only sweep (no eye requested) must stay usable -- the
    optimiser evaluates that way when --no-eye is passed. It should score, but
    it must not claim the eye specs passed."""
    m = _good_metrics()
    m.eye_height_v = float("nan")
    m.eye_width_ui = float("nan")
    sc = sp.score(m, sp.EqualizerSpec(require_eye=False))
    assert "eye_h" not in sc["pass"]
    assert sp.all_pass(sc), "with require_eye=False an AC-only result may pass"
