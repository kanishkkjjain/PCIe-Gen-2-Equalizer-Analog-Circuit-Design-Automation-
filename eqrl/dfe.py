"""1-tap DFE support: PRBS stimulus, eye extraction, peak-distortion analysis.

Two complementary paths, deliberately:

* :func:`eye_from_waveform` measures an eye from a **closed-loop** SPICE
  transient (the XSPICE slicer -> 1 UI latch -> tap feedback loop). Faithful,
  but a 200-500 UI transient is expensive -- reserved for verifying a winner.

(A cheap ``eye_from_pulse_response`` peak-distortion surrogate was written for
the RL inner loop and has been REMOVED: it never had a caller, and the measured
cost breakdown says the closed-loop eye is ~1.3 s of actual solve on top of a
PDK parse that every analysis pays anyway -- so the surrogate bought nothing
while being a second, untested definition of "eye".)

Note on the current state: with no channel in front of the equalizer, the
post-cursor h[1] is small, so the DFE has almost nothing to cancel and the tap
weight is not yet determined by anything physical. That is expected -- the tap
is structural until the channel lands.
"""
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
    centre = float(np.mean(eye[:, jbest]))
    return Eye(height_v=max(0.0, height),
               width_ui=_contiguous_open_fraction(heights > 0),
               center_v=centre, n_ui=n_ui)


def _contiguous_open_fraction(is_open: np.ndarray) -> float:
    """Longest CONTIGUOUS run of open phases, as a fraction of the UI.

    Counting the total number of open phases (the obvious thing) overstates the
    eye whenever it opens in two disjoint regions: a waveform that is open at
    the start and end of the UI but slammed shut across the middle scores 2/3
    of a UI when the usable opening is only 1/3. A receiver samples at ONE
    phase, so only a contiguous opening is worth anything.

    The run is measured circularly -- the eye's opening is not aligned to the
    fold's phase-0 boundary, so an opening that straddles it is still one
    opening.
    """
    n = int(is_open.size)
    if n == 0 or not is_open.any():
        return 0.0
    if is_open.all():
        return 1.0
    # doubling the array lets a wrapped run be found by a linear scan
    best = run = 0
    for v in np.concatenate([is_open, is_open]):
        run = run + 1 if v else 0
        best = max(best, run)
    return float(min(best, n)) / n
