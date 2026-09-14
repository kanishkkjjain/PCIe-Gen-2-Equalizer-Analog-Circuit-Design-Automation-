"""The physics features and the grey-box surrogate.

The expensive failure this guards against is a surrogate that looks fine on its
own loss curve while being unusable as an RL environment -- a feature that goes
infinite somewhere in the box, a train/validation split that leaks, or a
residual head that does not actually start at the hand calculation.
"""
from __future__ import annotations

import numpy as np
import pytest

from eqrl import specs as sp
from eqrl.netlist import PARAM_BOUNDS, PARAM_NAMES
from eqrl.physics import (FEATURE_NAMES, F_PEAK_HI, F_PEAK_LO, PhysFit,
                          analytic_baseline, area_mm2, physics_features,
                          supply_power)
from eqrl.spice import MeasCond

torch = pytest.importorskip("torch", reason="surrogate needs torch")

from eqrl.surrogate import (DIRECT_TARGETS, RESIDUAL_TARGETS, Net,
                            apply_residual, residual_targets,
                            split_by_candidate)

MEAS = MeasCond.from_spec(sp.EqualizerSpec())
CORNER = {"process": "tt", "vdd": 1.8, "temp": 27.0, "rc": None}


def _sobol(n=4096, seed=0):
    from scipy.stats import qmc
    u = qmc.Sobol(d=len(PARAM_NAMES), scramble=True, seed=seed).random(n)
    from eqrl import netlist as nl
    return [nl.denorm(list(row)) for row in u]


def test_features_are_finite_everywhere_in_the_box():
    """A single inf anywhere in the box poisons the whole training run.

    Sobol rather than random: it actually visits the corners of the box, which
    is where degenerate designs (Rs*Cs -> 0, RL*Cout -> huge) live.
    """
    params = _sobol(4096)
    X = physics_features(params, CORNER, PhysFit(), MEAS)
    assert X.shape == (4096, len(FEATURE_NAMES))
    assert np.isfinite(X).all(), (
        "non-finite features at "
        f"{sorted({FEATURE_NAMES[j] for j in np.argwhere(~np.isfinite(X))[:, 1]})}")


def test_analytic_f_peak_stays_inside_the_sweep():
    """argmax over a frequency grid can return the grid EDGE for a degenerate
    design; log(20 GHz) is then a feature the network has to unlearn."""
    base = analytic_baseline(_sobol(2048), CORNER, PhysFit(), MEAS)
    fp = np.asarray(base["f_peak"])
    assert np.isfinite(fp).all()
    assert (fp >= F_PEAK_LO).all() and (fp <= F_PEAK_HI).all()


def test_area_is_computed_exactly_not_predicted():
    from eqrl.netlist import CtleParams
    p = {k: PARAM_BOUNDS[k][0] * 0.5 + PARAM_BOUNDS[k][1] * 0.5
         for k in PARAM_NAMES}
    assert area_mm2(p)[0] == pytest.approx(
        CtleParams(**p).area_um2()["total_mm2"])


def test_supply_power_counts_both_tails_and_the_mirror_reference():
    """The pair has SEPARATE per-side tails plus a reference branch.

    `vdd * (Itail + Ig1_branch)` -- the obvious formula -- is wrong by more
    than 2x, and it read as correct until it was checked against i_supply.
    """
    got = supply_power(1.8, 600e-6, 1.0e-3, 5.0)
    want = 1.8 * (2 * 600e-6 + 600e-6 / 4.0 + 1.0e-3 * 1.05)
    assert float(got) == pytest.approx(want)


def test_residual_round_trips_through_physical_units():
    Br = np.array([[6.0, 1.8e9, -4.0, 3.0e-3]])
    Yr = np.array([[5.0, 2.2e9, -3.0, 2.0e-3]])
    R = residual_targets(Yr, Br)
    assert np.allclose(apply_residual(R, Br), Yr)


def test_the_residual_head_starts_at_the_hand_calculation():
    """Zero-initialised final layer: before training the model IS the physics.

    This is the whole claim of the grey-box design -- training can only improve
    on the equations, never start below them.
    """
    net = Net(len(FEATURE_NAMES), seed=0)
    x = torch.randn(32, len(FEATURE_NAMES))
    r, _, _ = net(x)
    assert torch.allclose(r, torch.zeros_like(r), atol=1e-12)
    Br = np.full((32, len(RESIDUAL_TARGETS)), 3.0)
    assert np.allclose(apply_residual(r.detach().numpy(), Br), Br)


def test_the_split_never_puts_one_design_on_both_sides():
    """The 4 corners of a design are 4 near-identical rows.

    Splitting by ROW puts some in train and some in validation, and the
    held-out score then measures memorisation, not generalisation.
    """
    recs = []
    for i in range(50):
        for c in ("tt", "ss", "ff", "ll"):
            recs.append({"key": [round(i * 0.01, 5)] * 11, "corner": c,
                         "metrics": {"ok": True}})
    tr, va, val_keys = split_by_candidate(recs, 0.2, seed=0)
    ktr = {tuple(r["key"]) for r in tr}
    kva = {tuple(r["key"]) for r in va}
    assert ktr & kva == set(), "a design appears in both splits"
    assert len(ktr) + len(kva) == 50
    # and every corner of a held-out design is held out together
    assert len(va) == len(kva) * 4


def test_physfit_falls_back_to_square_law_without_data():
    """An unfitted PhysFit must still produce usable, finite estimates."""
    f = PhysFit()
    gm = f.gm_m1(20.0, 0.18, 600e-6, 27.0)
    assert np.isfinite(gm).all() and float(gm) > 0
    assert f.n_fit == 0


def test_the_channel_model_reproduces_the_measured_response():
    """The pulse-response features are only worth having if the channel in
    them is the channel the netlist builds.

    Independent measurement of the built netlist gives -16.8 dB at the 2.5 GHz
    Nyquist frequency and 10.6 dB of tilt from DC. This is an ABCD cascade of
    the same ladder, so it must agree.
    """
    from eqrl.physics import channel_H
    f = np.array([1e6, 2.5e9])
    db = 20 * np.log10(np.abs(channel_H(f)))
    assert db[1] == pytest.approx(-16.8, abs=0.15), f"Nyquist loss {db[1]:.2f} dB"
    assert (db[0] - db[1]) == pytest.approx(10.6, abs=0.2), "tilt"


def test_pulse_taps_are_finite_and_ordered_over_the_box():
    """ISI taps must exist for every design in the box, including degenerate
    ones -- an inf here poisons six features at once."""
    from eqrl.physics import PhysFit, eye_features
    from eqrl import netlist as nl
    params = _sobol(512, seed=3)
    fit = PhysFit()
    P = {k: np.array([p[k] for p in params]) for k in PARAM_NAMES}
    gm = fit.gm_m1(P["W"], P["L"], P["Itail"], 27.0)
    gmg = fit.gm_g1(P["Wg1"], P["Ig1"], 27.0)
    cout = fit.cout(P["W"], P["L"], P["Wg1"])
    ey = eye_features(gm, P["RL"], P["Rs"], P["Cs"], cout, gmg, P["Rsum"],
                      P["tap_pct"], MEAS.ui_s)
    for k, v in ey.items():
        assert np.isfinite(np.asarray(v)).all(), f"{k} not finite"
    assert (np.asarray(ey["eye_est"]) >= 0).all()
    assert (np.asarray(ey["openness_est"]) <= 1.0).all()


def test_tap_ratio_is_not_a_prediction_target():
    """It spans seven orders of magnitude in the collected data, so after
    linear standardisation its outliers own the direct heads' loss -- measured,
    including it drove its own held-out R^2 to -6.1 and dragged the other
    direct heads down with it. It is diagnostic and never scored."""
    from eqrl.surrogate import DIRECT_TARGETS, RESIDUAL_TARGETS
    assert "tap_ratio" not in DIRECT_TARGETS + RESIDUAL_TARGETS
