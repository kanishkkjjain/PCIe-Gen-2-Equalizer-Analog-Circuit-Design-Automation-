"""1-tap DFE support: PRBS stimulus, eye extraction, peak-distortion analysis.

Two complementary paths, deliberately:

* :func:`eye_from_waveform` measures an eye from a **closed-loop** SPICE
  transient (the XSPICE slicer -> 1 UI latch -> tap feedback loop). Faithful,
  but a 200-500 UI transient is expensive -- reserved for verifying a winner.

* :func:`eye_from_pulse_response` runs peak-distortion analysis on the sampled
  **pulse response**: one cheap transient gives h[k], the 1-tap DFE cancels
  h[1] exactly, and the residual ISI bounds the eye. Exact for a linear front
  end plus an ideal slicer, and cheap enough for the RL inner loop.

Note on the current state: with no channel in front of the equalizer, the
post-cursor h[1] is small, so the DFE has almost nothing to cancel and the tap
weight is not yet determined by anything physical. That is expected -- the tap
is structural until the channel lands.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

UI_DEFAULT = 200e-12          # 5.0 Gb/s -> 200 ps
NYQUIST = 2.5e9


# --------------------------------------------------------------------------
# stimulus
# --------------------------------------------------------------------------
def prbs(n_bits: int, seed: int = 0b1001011, taps: tuple[int, int] = (7, 6)) -> np.ndarray:
    """PRBS-7 bit sequence as 0/1 ints (repeats every 127 bits)."""
    order, tap = taps
    reg = seed & ((1 << order) - 1) or 1
    out = np.empty(n_bits, dtype=np.int8)
    for i in range(n_bits):
        bit = ((reg >> (order - 1)) ^ (reg >> (tap - 1))) & 1
        reg = ((reg << 1) | bit) & ((1 << order) - 1)
        out[i] = reg & 1
    return out


def nrz_pwl(bits: np.ndarray, ui: float, amp: float, vcm: float,
            trise: float | None = None, invert: bool = False) -> str:
    """Render bits as a SPICE PWL source string (NRZ, finite edge rate).

    ``amp`` is the single-ended amplitude about ``vcm``; drive the two halves of
    a differential pair with invert=False / invert=True.
    """
    tr = trise if trise is not None else 0.15 * ui
    lvl = {0: vcm - amp, 1: vcm + amp}
    if invert:
        lvl = {0: vcm + amp, 1: vcm - amp}
    pts: list[tuple[float, float]] = [(0.0, lvl[int(bits[0])])]
    t = 0.0
    for i in range(1, len(bits)):
        t = i * ui
        prev, cur = lvl[int(bits[i - 1])], lvl[int(bits[i])]
        if prev != cur:
            pts.append((t - tr / 2, prev))
            pts.append((t + tr / 2, cur))
    pts.append((len(bits) * ui, lvl[int(bits[-1])]))
    body = " ".join(f"{tt:.6g} {vv:.6g}" for tt, vv in pts)
    return f"PWL({body})"


# --------------------------------------------------------------------------
# eye measurement
# --------------------------------------------------------------------------
@dataclass
class Eye:
    height_v: float = float("nan")     # vertical opening (V, differential)
    width_ui: float = float("nan")     # horizontal opening (fraction of a UI)
    center_v: float = float("nan")
    n_ui: int = 0
    note: str = ""

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def eye_from_waveform(t: np.ndarray, v: np.ndarray, ui: float,
                      skip_ui: int = 20, n_phase: int = 64) -> Eye:
    """Fold a differential transient into an eye and measure its opening.

    height = min(upper trace) - max(lower trace) at the best sampling phase.
    width  = fraction of the UI over which the eye stays open at the decision
             threshold (0 V differential).
    """
    if t.size < 8:
        return Eye(note="waveform too short")
    t0 = skip_ui * ui                       # let the loop settle first
    m = t >= t0
    t, v = t[m], v[m]
    if t.size < 8:
        return Eye(note="nothing left after settling skip")
    n_ui = int((t[-1] - t[0]) // ui)
    if n_ui < 4:
        return Eye(note=f"only {n_ui} UI captured")

    # resample onto a uniform phase grid, one row per UI
    phase = np.linspace(0.0, 1.0, n_phase, endpoint=False)
    rows = []
    for k in range(n_ui):
        tk = t[0] + (k + phase) * ui
        rows.append(np.interp(tk, t, v))
    eye = np.asarray(rows)                  # (n_ui, n_phase)

    # Classify each UI trace ONCE, by its sign at the widest-separation phase,
    # then hold that label fixed across all phases.
    #
    # Splitting by sign *at each phase* (the obvious thing) is circular: the two
    # groups are then separated by construction, height is always > 0, and the
    # width metric can never report a closed eye -- it returns 1.000 UI for any
    # input. The label must come from the decided bit, not from the sample.
    sep = np.array([
        (eye[eye[:, j] > 0, j].min() - eye[eye[:, j] < 0, j].max())
        if (np.any(eye[:, j] > 0) and np.any(eye[:, j] < 0)) else -np.inf
        for j in range(n_phase)
    ])
    if not np.isfinite(sep).any():
        return Eye(n_ui=n_ui, note="no bipolar transitions - eye closed or DC")
    j_ref = int(np.nanargmax(sep))
    is_one = eye[:, j_ref] > 0
    if not is_one.any() or is_one.all():
        return Eye(n_ui=n_ui, note="only one symbol level present")

    heights = np.array([
        eye[is_one, j].min() - eye[~is_one, j].max() for j in range(n_phase)
    ])
    jbest = int(np.nanargmax(heights))
    height = float(heights[jbest])
    open_frac = float(np.sum(heights > 0) / n_phase)
    centre = float(np.mean(eye[:, jbest]))
    return Eye(height_v=max(0.0, height), width_ui=open_frac,
               center_v=centre, n_ui=n_ui)


def eye_from_pulse_response(t: np.ndarray, v: np.ndarray, ui: float,
                            n_taps: int = 1, sample_phase: float = 0.5) -> Eye:
    """Peak-distortion eye from a single-bit pulse response.

    Samples h[k] at one point per UI, treats the largest |h| as the cursor,
    cancels the first ``n_taps`` post-cursors (what the DFE does), and bounds
    the worst-case eye by the sum of the remaining |h|.

    This is the cheap path used inside the RL loop: one transient per candidate
    instead of a few-hundred-UI closed-loop run.
    """
    if t.size < 8:
        return Eye(note="pulse response too short")
    t_s = np.arange(t[0] + sample_phase * ui, t[-1], ui)
    h = np.interp(t_s, t, v)
    if h.size < 3:
        return Eye(note="too few UI in pulse response")
    k0 = int(np.argmax(np.abs(h)))
    cursor = abs(h[k0])
    post = np.abs(h[k0 + 1:])
    pre = np.abs(h[:k0])
    cancelled = post[:n_taps].sum()
    residual = post[n_taps:].sum() + pre.sum()
    height = 2.0 * (cursor - residual)
    return Eye(height_v=max(0.0, height), width_ui=float("nan"),
               center_v=0.0, n_ui=int(h.size),
               note=f"cursor={cursor:.4g} cancelled={cancelled:.4g} "
                    f"residual={residual:.4g}")
