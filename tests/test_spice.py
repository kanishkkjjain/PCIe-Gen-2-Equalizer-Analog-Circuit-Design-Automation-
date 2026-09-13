"""Tier-2 tests: real ngspice against the real SKY130 models.

Slower (each ngspice launch pays ~8 s of PDK model parsing regardless of the
circuit), so these are marked ``needs_pdk`` and kept to the checks that genuinely
require a simulator. Anything provable from the netlist text belongs in
test_pure.py.
"""
import math
import subprocess

import pytest

from eqrl import netlist as nl, pdk, specs as sp
from eqrl.netlist import CtleParams

pytestmark = pytest.mark.needs_pdk


def _run_deck(tmp_path, body: str, control: str) -> str:
    """Run a standalone deck and return ngspice's stdout+stderr."""
    lib = pdk.find_sky130_lib()
    deck = (f'.lib "{lib}" tt\n.temp 27\n{body}\n'
            f".control\n{control}\n.endc\n.end\n")
    d = tmp_path / "t.sp"
    d.write_text(deck)
    r = subprocess.run(["ngspice", "-b", str(d)], cwd=tmp_path,
                       capture_output=True, text=True, timeout=300)
    return r.stdout + r.stderr


def _scalar(log: str, name: str) -> float:
    for line in log.splitlines():
        s = line.strip().lower()
        if s.startswith(name.lower()) and "=" in s:
            return float(s.split("=")[1].split()[0])
    raise AssertionError(f"{name!r} not found in:\n{log[-1500:]}")


# --------------------------------------------------------------------------
# device-model conventions the whole design rests on
# --------------------------------------------------------------------------
def test_resistor_parallel_units_need_m_not_mult(tmp_path):
    """`mult=` is inert on the sky130 resistor; `m=` is the real multiplier.

    In sky130_fd_pr__res_high_po_1p41 the `mult` parameter appears only inside
    the Monte-Carlo mismatch expressions and never divides the two resistance
    elements, so `mult=4` leaves the resistance unchanged. res_geometry()
    returns mult>1 for every R below the ~450 ohm contact floor, so emitting it
    as `mult=` simulated Rs=50 ohm as 450 ohm -- 9x too large -- and would have
    done so for any low-RL/Rs candidate the optimiser proposed.
    """
    body = ("Va a 0 DC 1\nVb b 0 DC 1\nVc c 0 DC 1\n"
            f"XA a 0 0 {nl.RES} w=1.41 l=5\n"
            f"XB b 0 0 {nl.RES} w=1.41 l=5 mult=4\n"
            f"XC c 0 0 {nl.RES} w=1.41 l=5 m=4\n")
    log = _run_deck(tmp_path, body, "op\nprint abs(1/i(Va)) abs(1/i(Vb)) abs(1/i(Vc))")
    one = _scalar(log, "abs(1/i(va))")
    mult4 = _scalar(log, "abs(1/i(vb))")
    m4 = _scalar(log, "abs(1/i(vc))")
    assert mult4 == pytest.approx(one, rel=1e-6), "mult= unexpectedly did something"
    assert m4 == pytest.approx(one / 4.0, rel=1e-3), "m= must put 4 in parallel"


def test_netlist_emits_m_for_parallel_resistors():
    """The generator must use the multiplier that actually works."""
    n = CtleParams(RL=120.0).core_netlist()      # low enough to force mult>1
    _, mult = nl.res_geometry(120.0)
    assert mult > 1
    rl = [l for l in n.splitlines() if l.startswith("XRLp")][0]
    assert f" m={mult}" in rl
    assert "mult=" not in rl


def test_nfet_w_is_total_width(tmp_path):
    """W is TOTAL width: W=40/nf=4 must equal 4 parallel W=10/nf=1 devices.

    If W were per-finger, _nf() would silently multiply every device width by
    its finger count and every current in the design would be wrong.
    """
    body = ("Vg g 0 DC 0.9\nVd1 d1 0 DC 0.9\nVd2 d2 0 DC 0.9\n"
            f"XM1 d1 g 0 0 {nl.NFET} L=0.5 W=40 nf=4\n"
            f"XM2 d2 g 0 0 {nl.NFET} L=0.5 W=10 nf=1 m=4\n")
    log = _run_deck(tmp_path, body, "op\nprint abs(i(Vd1)) abs(i(Vd2))")
    assert _scalar(log, "abs(i(vd1))") == pytest.approx(
        _scalar(log, "abs(i(vd2))"), rel=1e-6)


# --------------------------------------------------------------------------
# the analyses
# --------------------------------------------------------------------------
def test_op_reports_both_devices_and_a_valid_bias(sim, corner):
    op = sim.op(CtleParams(), corner)
    assert op["ok"], op.get("note")
    for k in ("id_m1", "gm_m1", "vds_m1", "vdsat_m1",
              "id_tail", "vds_tail", "vdsat_tail"):
        assert math.isfinite(op[k]), f"{k} not measured"
    assert op["vds_m1"] > op["vdsat_m1"] + 0.02
    assert op["tail_saturated"], "the real tail must not sit in triode"
    assert op["saturated"]


def test_op_supply_current_matches_the_design_budget(sim, corner):
    p = CtleParams()
    op = sim.op(p, corner)
    budget = (2 + 1 / nl.MIRROR_RATIO) * p.Itail + p.Ig1 + p.Igf
    assert op["i_supply"] == pytest.approx(budget, rel=0.20)


def test_a_stale_device_probe_fails_loudly(sim, corner, monkeypatch):
    """A wrong probe path used to return ok=True with saturated=False, capping
    every candidate's reward at 0.2 with no error anywhere (HANDOVER bug 7)."""
    monkeypatch.setattr(nl, "NFET", "sky130_fd_pr__nfet_01v8_NOPE")
    op = sim.op(CtleParams(), corner)
    assert not op["ok"]
    assert "missing" in op.get("note", "")


def test_ac_peaks_in_band(sim, corner):
    """Peaking is a property of the CTLE stage, which the split did not touch."""
    ac = sim.ac(CtleParams(), corner)
    assert ac["ok"]
    assert 3.0 < ac["peaking_db"] < 12.0
    assert 1.0e9 < ac["f_peak"] < 5.0e9
    assert math.isfinite(ac["gain_2g5_db"])


def test_summing_stage_adds_gain(sim, corner):
    """Gm1 into Rsum is a real gain stage; the summing node should show more
    gain at Nyquist than the CTLE alone."""
    ac = sim.ac(CtleParams(), corner)
    assert "sum_gain_2g5_db" in ac
    assert ac["sum_gain_2g5_db"] > ac["gain_2g5_db"]


def test_noise_is_finite_and_agrees_with_ngspices_own_integration(sim, corner):
    """ngspice's inoise_total returns -nan at many corners while the spectrum it
    computed is clean, so we integrate the spectrum ourselves. Where ngspice
    does work, the two must agree."""
    nz = sim.noise(CtleParams(), corner)
    assert nz["ok"], nz.get("note")
    assert math.isfinite(nz["vn_in_rms"]) and 0 < nz["vn_in_rms"] < 5e-3
    ng = nz.get("ngspice_inoise_total")
    if ng is not None and math.isfinite(ng) and ng > 0:
        assert nz["vn_in_rms"] == pytest.approx(ng, rel=0.10)


def test_hd3_is_finite(sim, corner):
    hd = sim.tran_hd3(CtleParams(), corner)
    assert hd["ok"]
    assert math.isfinite(hd["hd3_db"]) and hd["hd3_db"] < -20


def test_tap_weight_is_realised(sim, corner):
    """Fully steered, GmF should move the SUMMING NODE by ~2*Igf*Rsum."""
    tw = sim.tap_weight(CtleParams(), corner)
    assert tw["ok"]
    assert 0.5 < tw["tap_efficiency"] <= 1.05, tw
    # a 1-tap DFE must be able to cancel a meaningful fraction of the cursor
    assert 0.05 < tw["tap_frac_of_main"] < 1.0, tw


def test_both_stages_bias_correctly(sim, corner):
    """Every stage must be in saturation, not just the CTLE. The summing stage
    sits under two stacked drops (Rsum load + tail), so it is the one most
    likely to run out of headroom."""
    op = sim.op(CtleParams(), corner)
    assert op["ok"], op.get("note")
    assert op["tail_saturated"], "CTLE tail in triode"
    assert op["summer_saturated"], "summing-stage devices not in saturation"
    assert op["saturated"]


def test_measured_tap_ratio_is_sane(sim, corner):
    """Tap weight is now gmF/gm1, measured from .op. The square-law estimate in
    tap_weight_ratio() is only a guide -- these devices are short-channel -- so
    assert they are in the same ballpark, not equal."""
    p = CtleParams()
    op = sim.op(p, corner)
    assert op["ok"]
    assert op["gm_g1"] > 0 and op["gm_gf"] > 0
    assert 0.1 < op["tap_ratio"] < 1.0, op["tap_ratio"]
    assert op["tap_ratio"] == pytest.approx(p.tap_weight_ratio(), rel=0.5)


def test_tap_opposes_the_main_path(sim, corner):
    """THE polarity test. GmF is crossed the same way as Gm1, so vfbp HIGH
    drives the summing node the same direction a positive symbol does.
    Therefore the INVERTED decision (q1b) must reach vfbp, or the tap
    reinforces the post-cursor instead of cancelling it.

    This is HANDOVER bug 4 re-opened by the topology change. It is invisible in
    every metric -- the old merged version measured 0.078 UI wrong vs 0.453 UI
    right -- so it is asserted here rather than reasoned about in a comment.
    """
    from eqrl.spice import _parse_prints
    p = CtleParams()
    ctl = (".control\nop\n"
           "print v(outp_ctle) v(outn_ctle) v(outp_dfe) v(outn_dfe)\n.endc\n")

    # a positive symbol through the main path
    src = (f"Vinp vinp 0 DC {p.Vcm + 0.025:.4g}\n"
           f"Vinn vinn 0 DC {p.Vcm - 0.025:.4g}\n")
    _, log = sim._run(sim._assemble(p, corner, "op", ctl, dfe="cm",
                                    src_override=src), "pol_main")
    v = _parse_prints(log)
    main = v["v(outp_dfe)"] - v["v(outn_dfe)"]
    assert main > 0, "CTLE->Gm1 chain should be non-inverting differentially"

    # the tap alone, vfbp driven HIGH, no input
    src0 = f"Vinp vinp 0 DC {p.Vcm:.4g}\nVinn vinn 0 DC {p.Vcm:.4g}\n"
    _, log = sim._run(sim._assemble(p, corner, "op", ctl, dfe="pos",
                                    src_override=src0), "pol_tap")
    v = _parse_prints(log)
    tap = v["v(outp_dfe)"] - v["v(outn_dfe)"]
    assert tap > 0, "vfbp HIGH should push the summing node positive"

    # same sign => the inverted decision must drive vfbp
    assert (main > 0) == (tap > 0)
    assert "adac_p [q1b] [vfbp]" in nl.dfe_decision_path(), \
        "tap would REINFORCE the post-cursor, not cancel it"


# --------------------------------------------------------------------------
# closed-loop eye
# --------------------------------------------------------------------------
def test_eye_is_open_at_the_summing_node(sim, corner):
    """The eye that matters is the one the slicer sees -- outp_dfe/outn_dfe,
    after the summing stage, not the CTLE output."""
    r = sim.tran_eye(CtleParams(), corner)
    assert r["ok"]
    e = r["eye"]
    assert e.height_v > 0.100, e
    assert e.width_ui > 0.4, e


def test_an_oversized_tap_closes_the_eye(sim, corner):
    """With NO channel there is no post-cursor to cancel, so a large tap only
    injects an uncorrelated term and closes the eye. This is the honest
    expectation, and it is also why the optimiser drives Igf down."""
    big = sim.tran_eye(CtleParams(Igf=1.5e-3), corner)["eye"]
    small = sim.tran_eye(CtleParams(Igf=50e-6), corner)["eye"]
    # HEIGHT is the metric that responds: an uncorrelated +/-(Igf*Rsum) term
    # eats vertical opening. Width barely moves (measured 0.781 vs 0.750),
    # because the waveform still crosses the threshold cleanly at most phases
    # -- so asserting on width here would be testing the wrong thing.
    assert big.height_v < 0.85 * small.height_v, (big, small)


def test_closed_loop_deck_clocks_at_the_requested_ui(sim, corner):
    """Regression: the DFE latch clock was pinned at 200 ps while the PRBS
    followed the requested UI, so any other bit rate was silently wrong."""
    ui = 400e-12
    sim.tran_eye(CtleParams(), corner, n_bits=40, ui=ui, tag="eye_ui")
    deck = (sim.workdir / "eye_ui.sp").read_text()
    vclk = [l for l in deck.splitlines() if l.startswith("Vclk")][0]
    assert float(vclk.split("PULSE(")[1].rstrip(")").split()[-1]) == pytest.approx(ui)


# --------------------------------------------------------------------------
# evaluate() end to end
# --------------------------------------------------------------------------
def test_evaluate_populates_every_metric(sim, corner):
    m = sim.evaluate(CtleParams(), corner, do_eye=True)
    assert m.ok, m.note
    assert not m.partial
    for k, v in m.as_dict().items():
        if isinstance(v, float):
            assert math.isfinite(v), f"{k} is NaN"


def test_default_design_scores_well_at_typical(sim, corner):
    """Defaults should be a sane starting point for the optimiser."""
    m = sim.evaluate(CtleParams(), corner, do_eye=True)
    scored = sp.score(m, sp.EqualizerSpec())
    assert scored["reward"] > 0.8
    assert scored["pass"]["eye_h"] and scored["pass"]["eye_w"]


# --------------------------------------------------------------------------
# speed work: the combined deck and the trimmed library must change NOTHING
# --------------------------------------------------------------------------
@pytest.mark.parametrize("over", [
    {},
    {"RL": 120.0, "Rs": 80.0},          # forces the parallel-resistor path
    {"W": 90.0, "Cs": 3e-12},           # widest devices, biggest MIM cap
])
def test_combined_deck_is_bit_identical_to_separate_analyses(tmp_path, corner, over):
    """One launch instead of four must not change a single number.

    The combined deck runs op/ac/tran/noise from one netlist, which is only
    safe because they share a stimulus and a DFE bias. If that assumption ever
    breaks, it would show up here as a numeric difference rather than as a
    mysteriously different design.
    """
    from eqrl.spice import Simulator
    p = CtleParams(**over)
    sep = Simulator(tmp_path / "sep", timeout=600).evaluate(p, corner, combined=False)
    cmb = Simulator(tmp_path / "cmb", timeout=600).evaluate(p, corner, combined=True)
    assert sep.ok and cmb.ok, (sep.note, cmb.note)
    for k, va in sep.as_dict().items():
        vb = cmb.as_dict()[k]
        if isinstance(va, bool) or not isinstance(va, (int, float)):
            assert va == vb, f"{k}: {va} != {vb}"
            continue
        if va != va and vb != vb:
            continue
        assert va == pytest.approx(vb, rel=1e-9, nan_ok=False), f"{k}: {va} vs {vb}"


def test_combined_deck_uses_one_launch_not_four(tmp_path, corner):
    from eqrl.spice import Simulator
    s = Simulator(tmp_path / "n", timeout=600)
    s.evaluate(CtleParams(), corner, combined=True)
    assert s.n_launches == 1, f"expected 1 ngspice launch, got {s.n_launches}"


def test_a_broken_deck_is_reported_as_a_failure_not_a_bad_design(tmp_path, corner,
                                                                 monkeypatch):
    """A device outside the model bins must surface as a FAILURE with a cause,
    never as a scoreable design with NaN metrics."""
    from eqrl import netlist as nl
    from eqrl.spice import Simulator
    monkeypatch.setattr(nl, "W_MAX_UM", 1e9)      # defeat the width splitter
    m = Simulator(tmp_path / "b", timeout=600).evaluate(
        CtleParams(Wtail=200.0, Ig1=4e-3, Itail=50e-6), corner)
    assert not m.ok
    assert "model-bin-miss" in m.note or "combined-deck-failed" in m.note, m.note


def test_trimmed_library_when_validated_changes_no_number(tmp_path, corner):
    """If a trimmed library is validated, Simulator must use it AND get the
    same answers. This is the integration counterpart to trimlib.validate():
    that proves the library is equivalent, this proves the wiring picks it up.
    """
    from eqrl import trimlib
    from eqrl.spice import Simulator
    sec = corner.lib_section
    trimlib.build(sec, tmp_path)
    trimlib.validate(sec, tmp_path, corner=corner,
                     points=({},), workdir=tmp_path / "val", verbose=False)

    full = Simulator(tmp_path / "f", timeout=600, use_trimmed=False)
    trim = Simulator(tmp_path / "t", timeout=600, use_trimmed=True,
                     trim_dir=tmp_path)
    assert trim.lib_for(sec) != full.lib_for(sec), "trimmed lib was not picked up"
    assert "trimmed_" in trim.lib_for(sec)

    a = full.evaluate(CtleParams(), corner)
    b = trim.evaluate(CtleParams(), corner)
    assert a.ok and b.ok, (a.note, b.note)
    for k, va in a.as_dict().items():
        vb = b.as_dict()[k]
        if isinstance(va, bool) or not isinstance(va, (int, float)):
            assert va == vb, f"{k}: {va} != {vb}"
        elif va == va or vb == vb:
            assert va == pytest.approx(vb, rel=1e-9), f"{k}: {va} vs {vb}"


def test_an_unvalidated_trim_dir_falls_back_to_the_full_library(tmp_path, corner):
    """A trimmed library that has not earned trust must be ignored, silently
    and correctly -- slow, never wrong."""
    from eqrl.spice import Simulator
    s = Simulator(tmp_path / "x", timeout=600, use_trimmed=True,
                  trim_dir=tmp_path / "empty")
    assert "sky130.lib.spice" in s.lib_for(corner.lib_section)


def test_peaking_is_the_equalisers_not_the_cascades(tmp_path, corner):
    """With a channel in front, peaking/f_peak must still describe the
    EQUALISER. The brief's 3-12 dB boost is a property of the equaliser, not of
    channel+equaliser. Both the ac() path and the combined-deck path must
    reference to v(vinp,vinn) -- the combined path was missed once already."""
    from eqrl.spice import Simulator
    p = CtleParams(RL=895.012, Rs=1500.0, Cs=300e-15)
    off = Simulator(tmp_path/"off", timeout=600, use_channel=False)
    on  = Simulator(tmp_path/"on",  timeout=600, use_channel=True)
    for label, get in (("ac()", lambda s: s.ac(p, corner)),
                       ("combined", lambda s: s._combined(p, corner)["ac"])):
        a, b = get(off), get(on)
        assert a["ok"] and b["ok"], label
        assert a["peaking_db"] == pytest.approx(b["peaking_db"], abs=0.2), \
            f"{label}: peaking changed with the channel ({a['peaking_db']} vs {b['peaking_db']})"
        assert a["f_peak"] == pytest.approx(b["f_peak"], rel=0.05), label
