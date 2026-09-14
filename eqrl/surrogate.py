"""A learned stand-in for ngspice, built on top of the hand equations.

An RL policy needs hundreds of thousands of environment steps.  Every step of
`env.CtleEnv` is a SPICE simulation at ~4.4 s, so a policy trained directly
against SPICE is not a 24-hour proposition -- it is a 2-year one.  The standard
answer (MBTD3, DAC 2024; AnaCraft, TCAD 2025) is to learn a surrogate from a
bounded set of simulations, train the policy inside it, and verify the result
against real SPICE.  That is what this is.

**Why grey-box rather than a plain network.**  Measured on this circuit's own
collected data, the hand equations track SPICE well in ORDER but badly in
VALUE: peaking correlates at rho = +0.88 while reading 1.77x too high, dc gain
at +0.89, power at +0.81.  A consistent multiplicative bias is the cheapest
thing in the world for a residual model to remove, and it means the network
starts out already knowing the shape of the response surface instead of having
to discover, from a few thousand points, that the boost is only realised when
f_p1 sits below f_p2.

So the scored metrics that HAVE a usable closed form are predicted as

    y_hat = f_analytic(u, corner) * exp(g_theta(features))      (ratio scale)
    y_hat = f_analytic(u, corner) +     g_theta(features)       (dB scale)

with the final layer of ``g_theta`` zero-initialised, so before any training the
model reproduces the hand calculation exactly.  Training can then only improve
on it.  The eye has no closed form and is predicted directly -- but from the
same physics features, including the one that actually decides it
(``Rs*Cs`` measured in UI, which sets whether the tail settles within a symbol).

`area_mm2` is computed exactly and never predicted.  Predicting something
exactly computable only adds error.

Everything here is honest about what it cannot do: `validate()` writes the
three-way comparison (residual vs plain network vs hand equations alone) to
``validation.json``, and if the physics is not helping, that file says so.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import specs as sp
from .cache import git_sha, write_atomic
from .netlist import PARAM_NAMES
from .physics import (ANALYTIC_TARGETS, FEATURE_NAMES, PhysFit,
                      analytic_baseline, area_mm2, physics_features)
from .spice import MeasCond, Metrics

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "surrogate"

#: metrics predicted as a correction to a closed form. Order matters: it is the
#: column order of the residual head.
RESIDUAL_TARGETS = ("peaking_db", "f_peak", "dc_gain_db", "power_w",
                    "vn_in_rms")
#: ...of those, the ones already on a dB (log) scale, where the residual is
#: additive rather than multiplicative.
DB_TARGETS = ("peaking_db", "dc_gain_db")

#: Metrics with no usable closed form, predicted directly (standardised).
#:
#: The last four are never scored. They are kept as auxiliary tasks: predicting
#: the bias point forces the shared trunk to represent it, which is what the
#: scored heads depend on anyway.
#:
#: `tap_ratio` is deliberately NOT here despite being the obvious fifth. It
#: spans 0.006 to 1.5e5 in the collected data -- seven orders of magnitude --
#: so after linear standardisation a handful of outliers dominate the direct
#: heads' loss entirely: including it drove held-out R^2 to -6.1 on itself and
#: dragged every other direct head down with it. It is diagnostic, not scored,
#: and not worth a log-transform special case.
DIRECT_TARGETS = ("gain_nyq_db", "hd3_db", "eye_height_v",
                  "eye_width_ui", "eye_improvement", "eye_openness_in",
                  "gm_m1", "gm_g1", "out_cm_v")

#: booleans predicted as logits
FLAG_TARGETS = ("saturated",)

TARGETS = RESIDUAL_TARGETS + DIRECT_TARGETS


def _torch():
    try:
        import torch
        return torch
    except ImportError as e:                            # pragma: no cover
        raise ImportError(
            "the surrogate needs torch. Install the CPU wheel:\n"
            "  pip install --break-system-packages torch "
            "--index-url https://download.pytorch.org/whl/cpu") from e


# ---------------------------------------------------------------------------
# dataset assembly
# ---------------------------------------------------------------------------
def split_by_candidate(records, frac_val: float = 0.2, seed: int = 0):
    """Train/validation split keyed on the DESIGN, never on the row.

    The four corners of one design are four rows with almost identical
    features. Splitting by row puts some of them in train and some in
    validation, and the held-out score then measures memorisation rather than
    generalisation -- the model has already seen that design at another corner.
    """
    keys = sorted({tuple(r["key"]) for r in records})
    rng = np.random.default_rng(seed)
    rng.shuffle(keys)
    n_val = max(1, int(len(keys) * frac_val))
    val = set(keys[:n_val])
    tr = [r for r in records if tuple(r["key"]) not in val]
    va = [r for r in records if tuple(r["key"]) in val]
    return tr, va, val


def assemble(records, fit: PhysFit, meas) -> dict:
    """records -> feature matrix, analytic baseline, targets and masks."""
    params = [r["params"] for r in records]
    cfs = [r["corner_fields"] for r in records]
    X = physics_features(params, cfs, fit, meas)
    base = analytic_baseline(params, cfs, fit, meas)

    n = len(records)
    Yr = np.full((n, len(RESIDUAL_TARGETS)), np.nan)
    Br = np.full((n, len(RESIDUAL_TARGETS)), np.nan)
    for j, k in enumerate(RESIDUAL_TARGETS):
        Yr[:, j] = [r["metrics"].get(k, np.nan) for r in records]
        Br[:, j] = np.asarray(base[k], float)
    Yd = np.full((n, len(DIRECT_TARGETS)), np.nan)
    for j, k in enumerate(DIRECT_TARGETS):
        Yd[:, j] = [r["metrics"].get(k, np.nan) for r in records]
    Yf = np.zeros((n, len(FLAG_TARGETS)))
    for j, k in enumerate(FLAG_TARGETS):
        Yf[:, j] = [1.0 if r["metrics"].get(k) else 0.0 for r in records]

    return {"X": X, "Yr": Yr, "Br": Br, "Yd": Yd, "Yf": Yf,
            "keys": [tuple(r["key"]) for r in records],
            "params": params, "corner_fields": cfs}


#: below this much measured peaking there is no peak to locate, and SPICE
#: reports f_peak at the sweep START (1 MHz) rather than a real maximum.
PEAK_FLOOR_DB = 0.5


def residual_targets(Yr: np.ndarray, Br: np.ndarray,
                     peaking: np.ndarray | None = None) -> np.ndarray:
    """What the residual head must learn: y - base (dB) or log(y / base).

    ``peaking`` masks the f_peak target where there IS no peak. A flat design
    reports f_peak = 1 MHz (the sweep start), which makes the residual target
    log(1e6 / 1.8e9) ~ -7.5 -- an outlier an order of magnitude larger than any
    real correction, and it dominates the f_peak loss. Both the real and the
    predicted design fail the fpeak band in that case regardless, so masking
    costs no pass/fail agreement.
    """
    R = np.full_like(Yr, np.nan)
    for j, k in enumerate(RESIDUAL_TARGETS):
        y, b = Yr[:, j], Br[:, j]
        if k in DB_TARGETS:
            R[:, j] = y - b
        else:
            with np.errstate(all="ignore"):
                ok = (y > 0) & (b > 0)
                R[ok, j] = np.log(y[ok] / b[ok])
        if k == "f_peak" and peaking is not None:
            R[~(np.asarray(peaking, float) >= PEAK_FLOOR_DB), j] = np.nan
    return R


#: Largest correction the residual heads may apply on a ratio scale, as a
#: natural log. 2.0 is a factor of 7.4 either way -- far beyond the ~1.8x bias
#: actually measured, but bounded.
#:
#: The bound is not cosmetic. Where a target is masked (f_peak on a design with
#: no peak) the head gets no gradient and its output on those rows is
#: arbitrary; un-clipped, exp() turned that into an f_peak of 4e13 Hz and a
#: held-out R^2 of -6859. A prediction may be wrong, but it must stay in the
#: range where "wrong" still means something.
MAX_LOG_RESIDUAL = 2.0


def apply_residual(R: np.ndarray, Br: np.ndarray) -> np.ndarray:
    """Inverse of `residual_targets`: predicted residual -> physical units."""
    Y = np.empty_like(R)
    for j, k in enumerate(RESIDUAL_TARGETS):
        if k in DB_TARGETS:
            Y[:, j] = Br[:, j] + R[:, j]
        else:
            Y[:, j] = Br[:, j] * np.exp(np.clip(R[:, j], -MAX_LOG_RESIDUAL,
                                                MAX_LOG_RESIDUAL))
    return Y


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------
class Net:
    """One ensemble member: shared trunk, three heads.

    Wrapped rather than subclassing nn.Module at import time so this file can
    be imported (and most of it tested) without torch installed.
    """

    def __init__(self, n_in: int, hidden: int = 128, seed: int = 0):
        torch = _torch()
        nn = torch.nn
        torch.manual_seed(seed)
        self.torch = torch
        self.trunk = nn.Sequential(
            nn.Linear(n_in, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU())
        self.head_res = nn.Linear(hidden, len(RESIDUAL_TARGETS))
        self.head_dir = nn.Linear(hidden, len(DIRECT_TARGETS))
        self.head_flag = nn.Linear(hidden, len(FLAG_TARGETS))
        # Zero-initialised residual head: before training the model IS the hand
        # calculation. Training is then strictly a correction to physics, and a
        # model that has learned nothing is no worse than the equations.
        nn.init.zeros_(self.head_res.weight)
        nn.init.zeros_(self.head_res.bias)
        self.modules = nn.ModuleList([self.trunk, self.head_res,
                                      self.head_dir, self.head_flag])

    def __call__(self, x):
        h = self.trunk(x)
        return self.head_res(h), self.head_dir(h), self.head_flag(h)

    def parameters(self):
        return self.modules.parameters()

    def state_dict(self):
        return self.modules.state_dict()

    def load_state_dict(self, sd):
        self.modules.load_state_dict(sd)

    def eval(self):
        self.modules.eval()

    def train(self):
        self.modules.train()


class Surrogate:
    """An ensemble of `Net`s plus everything needed to use it standalone."""

    def __init__(self, fit: PhysFit, x_mean, x_std, d_mean, d_std,
                 members=None, meas=None, config=None, r_std=None):
        self.fit = fit
        # Residual columns live on wildly different scales -- peaking is dB
        # (+-10), f_peak is a log ratio (+-1), power a log ratio (+-0.5). Summed
        # at equal weight in the loss, f_peak (the weakest head, and the spec
        # with the worst pass agreement) is simply drowned. Each column is
        # trained standardised and rescaled here.
        self.r_std = (np.ones(len(RESIDUAL_TARGETS), np.float32)
                      if r_std is None else np.asarray(r_std, np.float32))
        self.x_mean = np.asarray(x_mean, np.float32)
        self.x_std = np.asarray(x_std, np.float32)
        self.d_mean = np.asarray(d_mean, np.float32)
        self.d_std = np.asarray(d_std, np.float32)
        self.members = members or []
        for m in self.members:          # once, not on every forward pass
            m.eval()
        self.meas = meas or MeasCond()
        self.config = config or {}

    # -- prediction --------------------------------------------------------
    def predict_raw(self, params, corner_fields) -> dict:
        """-> dict of arrays in physical units, plus per-target std."""
        torch = _torch()
        X = physics_features(params, corner_fields, self.fit, self.meas)
        base = analytic_baseline(params, corner_fields, self.fit, self.meas)
        Br = np.column_stack([np.asarray(base[k], float)
                              for k in RESIDUAL_TARGETS])
        Xs = (X - self.x_mean) / self.x_std
        xt = torch.tensor(Xs, dtype=torch.float32)

        res, dirs, flags = [], [], []
        with torch.no_grad():
            for m in self.members:
                r, d, f = m(xt)
                res.append(r.numpy())
                dirs.append(d.numpy())
                flags.append(f.numpy())
        R = np.mean(res, 0) * self.r_std
        R_sd = (np.std(res, 0) * self.r_std if len(res) > 1
                else np.zeros_like(R))
        D = np.mean(dirs, 0) * self.d_std + self.d_mean
        D_sd = (np.std(dirs, 0) * self.d_std if len(dirs) > 1
                else np.zeros_like(D))
        F = 1.0 / (1.0 + np.exp(-np.mean(flags, 0)))

        Yr = apply_residual(R, Br)
        out = {k: Yr[:, j] for j, k in enumerate(RESIDUAL_TARGETS)}
        out.update({k: D[:, j] for j, k in enumerate(DIRECT_TARGETS)})
        out.update({k: F[:, j] for j, k in enumerate(FLAG_TARGETS)})
        # A dB residual is ADDITIVE, so its spread is the residual spread
        # itself; a log-ratio residual is multiplicative, so its spread scales
        # with the predicted value.
        out["_std"] = {**{k: (R_sd[:, j] if k in DB_TARGETS
                              else np.abs(Yr[:, j]) * R_sd[:, j])
                          for j, k in enumerate(RESIDUAL_TARGETS)},
                       **{k: D_sd[:, j]
                          for j, k in enumerate(DIRECT_TARGETS)}}
        out["area_mm2"] = area_mm2(params)
        return out

    def predict_metrics(self, params, corner_fields, raw=None) -> list:
        """-> a list of real `Metrics`, so `specs.score` can be used unchanged.

        Using the same scorer as the search is the point: a second scorer
        written for the surrogate would be a second place for the definition of
        'good' to drift, and this project has been bitten by duplicated facts
        three times already.
        """
        # `raw` lets a caller that already has the prediction avoid paying for
        # a second forward pass -- the env needs both the metrics and the
        # ensemble spread, and computing them separately doubled its step cost.
        p = raw if raw is not None else self.predict_raw(params, corner_fields)
        n = len(p["peaking_db"])
        out = []
        for i in range(n):
            m = Metrics(ok=True, channel_present=True,
                        saturated=bool(p["saturated"][i] >= 0.5))
            for k in RESIDUAL_TARGETS + DIRECT_TARGETS:
                setattr(m, k, float(p[k][i]))
            m.area_mm2 = float(p["area_mm2"][i])
            m.peak_gain_db = m.dc_gain_db + m.peaking_db
            # eye openness at the slicer, implied by the predicted improvement
            m.eye_openness_out = float(m.eye_openness_in * m.eye_improvement)
            m.tail_saturated = m.saturated
            m.summer_saturated = m.saturated
            out.append(m)
        return out

    # -- persistence -------------------------------------------------------
    def save(self, directory) -> Path:
        torch = _torch()
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        self.fit.save(d / "physfit.json")
        np.savez(d / "stats.npz", x_mean=self.x_mean, x_std=self.x_std,
                 d_mean=self.d_mean, d_std=self.d_std, r_std=self.r_std)
        for i, m in enumerate(self.members):
            torch.save(m.state_dict(), d / f"member_{i}.pt")
        (d / "config.json").write_text(json.dumps(
            {**self.config, "n_members": len(self.members),
             "n_features": len(FEATURE_NAMES),
             "feature_names": list(FEATURE_NAMES),
             "residual_targets": list(RESIDUAL_TARGETS),
             "direct_targets": list(DIRECT_TARGETS),
             "flag_targets": list(FLAG_TARGETS),
             "meas": {k: getattr(self.meas, k) for k in
                      ("ui_s", "tx_swing_pp_v", "hd3_freq", "hd3_amp_pp_v",
                       "noise_f_lo", "noise_f_hi")},
             "git_sha": git_sha(), "saved": time.time()}, indent=1))
        return d

    @classmethod
    def load(cls, directory) -> "Surrogate":
        torch = _torch()
        d = Path(directory)
        cfg = json.loads((d / "config.json").read_text())

        # A model saved before the target lists changed loads with a torch
        # shape-mismatch dump that says nothing about the cause. The config
        # records what it was built for, so say it plainly instead.
        for field, current in (("residual_targets", RESIDUAL_TARGETS),
                               ("direct_targets", DIRECT_TARGETS),
                               ("flag_targets", FLAG_TARGETS),
                               ("feature_names", FEATURE_NAMES)):
            saved = cfg.get(field)
            if saved is not None and list(saved) != list(current):
                raise ValueError(
                    f"{d} was trained with a different {field} and cannot be "
                    f"loaded by this code.\n  saved:   {list(saved)}\n"
                    f"  current: {list(current)}\n"
                    f"Retrain: python3 scripts/train_surrogate.py")

        st = np.load(d / "stats.npz")
        fit = PhysFit.load(d / "physfit.json")
        meas = MeasCond(**cfg["meas"])
        members = []
        for i in range(cfg["n_members"]):
            net = Net(cfg["n_features"], seed=i)
            net.load_state_dict(torch.load(d / f"member_{i}.pt",
                                           weights_only=True))
            net.eval()
            members.append(net)
        return cls(fit, st["x_mean"], st["x_std"], st["d_mean"], st["d_std"],
                   members, meas, cfg,
                   r_std=st["r_std"] if "r_std" in st else None)


def latest_path() -> Path | None:
    p = MODEL_DIR / "LATEST"
    if not p.exists():
        return None
    d = Path(p.read_text().strip())
    if not d.is_absolute():
        d = ROOT / d
    return d if d.exists() else None


def load_latest() -> Surrogate:
    d = latest_path()
    if d is None:
        raise FileNotFoundError(
            f"no trained surrogate. Run: python3 scripts/train_surrogate.py")
    return Surrogate.load(d)
