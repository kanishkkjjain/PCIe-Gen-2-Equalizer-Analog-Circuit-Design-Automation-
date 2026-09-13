"""Trimmed SKY130 model libraries -- the single biggest speed win available.

99.9% of every ngspice launch is spent PARSING the PDK, not solving the
circuit. Measured (HANDOVER section 9): a full 12-device AC sweep takes 7.60 s
while parsing the library with a single 1k resistor takes 7.61 s -- the circuit
solve is below the noise floor. The `tt` section alone pulls **169 files,
7.12 MB**, covering pfet, 3.3/5/20 V devices, ESD, bipolars and RF, none of
which this design instantiates.

This module writes a per-corner `.lib` containing only what the design actually
uses, and -- critically -- REFUSES TO USE IT until it has been proven to give
identical answers to the full library.

Why this is safe to do at all: the failure mode of a missing model is LOUD.
ngspice says "could not find a valid modelname" and refuses to run, which
Simulator.scan_log() turns into a hard failure. There is no path where a
missing model silently changes a number.

Why it still needs validating anyway: a missing *parameter* is NOT loud. The
r+c includes set `rm3` and `rcvia3`, which the MIM cap's parasitic resistors
read. Dropping those would leave the cap subtly wrong with no error at all.
That is exactly why `validate()` compares real measured metrics rather than
trusting the dependency analysis.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import pdk

# Devices the design instantiates. Anything not needed to define these is
# dropped. Derived from netlist.py so a device-flavour change cannot leave a
# stale list behind.
def _needed_devices() -> list[str]:
    from . import netlist as nl
    return [nl.NFET, nl.RES, nl.CAP]


# Includes that must ALWAYS be kept regardless of dependency analysis:
#   all.spice  - carries `.option scale=1.0u`, the microns->metres conversion
#                for the BSIM4 bins. Without it every device geometry is off by
#                1e6 and nothing works. This was the original blocker.
#   r+c/*      - set rm3/rcvia3/tol_m3, read by the MIM cap's parasitics, and
#                they are what actually VARY across the R/C corner axis.
ALWAYS_KEEP = ("all.spice", "r+c/")

# Metrics compared during validation, and the relative tolerance each must meet.
# These are not arbitrary: they must be tight enough that a wrong model shows up.
VALIDATE_TOL = 1e-9


class TrimmedLibError(RuntimeError):
    """Raised on any condition that would make a trimmed library unsafe."""


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError as e:
        raise TrimmedLibError(f"cannot read {path}: {e}") from e


def _resolve_includes(path: Path, seen: set[Path] | None = None) -> set[Path]:
    """Every file reachable from ``path`` through .include, transitively."""
    seen = set() if seen is None else seen
    path = Path(os.path.normpath(path))
    if path in seen or not path.is_file():
        return seen
    seen.add(path)
    for line in _read(path).splitlines():
        m = re.match(r'\s*\.include\s+"?([^"\s]+)"?', line, re.I)
        if m:
            _resolve_includes(path.parent / m.group(1), seen)
    return seen


def _strip_includes(text: str) -> str:
    """Drop .include lines before searching for a device name.

    An aggregator like corners/tt.spice mentions every device name in its
    include PATHS. Without this, such a file looks like it defines our devices
    and gets kept whole -- dragging the other 28 families back in.
    """
    return "\n".join(l for l in text.splitlines()
                     if not re.match(r'\s*\.include', l, re.I))


def _mentions(text: str, dev: str) -> bool:
    """Does this file define anything for ``dev``?

    NOT just `.subckt <dev>`. The per-device mismatch files define only
    PARAMETERS (e.g. sky130_fd_pr__nfet_01v8_lvt__toxe_slope) and contain no
    subcircuit at all -- dropping one produces
    "Undefined parameter ... Cannot compute substitute" and the deck dies.
    Measured: that is exactly what rejected all 7 sections on the first
    attempt. Matching the device name anywhere in the file (minus .include
    lines) keeps the corner file AND its mismatch companion, while still
    dropping pfet / 3v3 / 5v0 / ESD / bipolar / RF.
    """
    return re.search(re.escape(dev), _strip_includes(text), re.I) is not None


def _defines_any(files: set[Path], devices: list[str]) -> list[str]:
    return [d for d in devices if any(_mentions(_read(f), d) for f in files)]


def section_body(lib: Path, section: str) -> list[str]:
    """The raw lines of one `.lib <section> ... .endl` block."""
    body, inside = [], False
    for line in _read(lib).splitlines():
        st = line.strip().lower()
        if re.match(rf"^\.lib\s+{re.escape(section.lower())}\s*$", st):
            inside = True
            continue
        if inside and st.startswith(".endl"):
            return body
        if inside:
            body.append(line)
    raise TrimmedLibError(
        f"section {section!r} not found in {lib} -- the corner naming in "
        f"sky130 is irregular (tt+ll is 'll', not 'tt_ll'); check "
        f"pdk.Corner.lib_section")


def _defines_directly(path: Path, devices: list[str]) -> list[str]:
    """Devices this file alone defines anything for (no recursion)."""
    return [d for d in devices if _mentions(_read(path), d)]


def _filter_file(path: Path, devices: list[str], dropped: list[str],
                 missing: list[str], depth: int = 0) -> list[str]:
    """Lines to keep for ``path``.

    A file that DIRECTLY defines one of our devices is kept whole. A file that
    only aggregates .include lines is recursed into, so that keeping one useful
    child does not drag in the 28 unused device families beside it. Getting
    this backwards keeps the entire 7 MB and the trim does nothing.
    """
    if depth > 8:
        raise TrimmedLibError(f"include nesting too deep at {path}")
    if _defines_directly(path, devices):
        return [f'.include "{path}"']
    out: list[str] = []
    for line in _read(path).splitlines():
        m = re.match(r'\s*\.include\s+"?([^"\s]+)"?', line, re.I)
        if not m:
            st = line.strip()
            if st and not st.startswith("*"):
                out.append(line)          # .param / .option lines matter
            continue
        rel = m.group(1)
        child = Path(os.path.normpath(path.parent / rel))
        if not child.is_file():
            missing.append(rel)
            continue
        if any(k in rel for k in ALWAYS_KEEP):
            out.append(f'.include "{child}"')
        elif _defines_any(_resolve_includes(child), devices):
            out.extend(_filter_file(child, devices, dropped, missing, depth + 1))
        else:
            dropped.append(str(child))
    return out


def build(section: str, out_dir: str | Path,
          full_lib: Path | None = None) -> Path:
    """Write a trimmed `.lib` for one corner section. Raises on anything odd."""
    full_lib = Path(full_lib or pdk.find_sky130_lib())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    devices = _needed_devices()

    kept: list[str] = []
    dropped: list[str] = []
    missing: list[str] = []
    for line in section_body(full_lib, section):
        m = re.match(r'\s*\.include\s+"?([^"\s]+)"?', line, re.I)
        if not m:
            st = line.strip()
            if st and not st.startswith("*"):
                kept.append(line)
            continue
        rel = m.group(1)
        target = Path(os.path.normpath(full_lib.parent / rel))
        if not target.is_file():
            missing.append(rel)
            continue
        if any(k in rel for k in ALWAYS_KEEP):
            kept.append(f'.include "{target}"')
        elif _defines_any(_resolve_includes(target), devices):
            kept.extend(_filter_file(target, devices, dropped, missing))
        else:
            dropped.append(str(target))

    # ---- refuse to emit anything questionable ----
    if missing:
        raise TrimmedLibError(
            f"section {section!r} references files that do not exist: "
            f"{missing[:5]} -- refusing to build a partial library")
    if not any("all.spice" in k for k in kept):
        raise TrimmedLibError(
            f"section {section!r}: all.spice was not kept. It carries "
            f"`.option scale=1.0u`; without it every geometry is off by 1e6.")
    if not any("nfet_01v8_lvt" in k for k in kept):
        raise TrimmedLibError(
            f"section {section!r}: no nfet_01v8_lvt model was kept")
    if not any("r+c" in k for k in kept):
        raise TrimmedLibError(
            f"section {section!r}: no r+c include was kept. Those set rm3 and "
            f"rcvia3, which the MIM cap's parasitics read, and they are what "
            f"varies across the R/C corner axis. Their absence would NOT be "
            f"loud -- it would quietly change the capacitor.")
    if not dropped:
        raise TrimmedLibError(
            f"section {section!r}: nothing was dropped, so the trim achieved "
            f"nothing. That means the dependency analysis is broken.")

    out = out_dir / f"trimmed_{section}.lib.spice"
    header = (f"* TRIMMED sky130 library for section {section!r}\n"
              f"* generated by eqrl/trimlib.py from {full_lib}\n"
              f"* kept {len(kept)} lines, dropped {len(dropped)} unused includes\n"
              f"* NOT hand-edited -- regenerate, do not patch\n")
    out.write_text(header + f".lib {section}\n" + "\n".join(kept)
                   + f"\n.endl {section}\n")
    return out


def _pdk_fingerprint(full_lib: Path) -> str:
    st = full_lib.stat()
    return hashlib.sha1(
        f"{full_lib}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def manifest_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / "trimlib_manifest.json"


def load_manifest(out_dir: str | Path) -> dict:
    p = manifest_path(out_dir)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_manifest(out_dir: str | Path, data: dict) -> None:
    manifest_path(out_dir).write_text(json.dumps(data, indent=2))


def validated_lib(section: str, out_dir: str | Path) -> Path | None:
    """Path to a trimmed lib ONLY if it was validated against this exact PDK.

    Returns None -- meaning "use the full library" -- whenever anything is off:
    no manifest, a different PDK, a failed validation, or a missing file. The
    caller must treat None as "fall back", never as an error.
    """
    man = load_manifest(out_dir)
    full = Path(pdk.find_sky130_lib())
    if man.get("pdk_fingerprint") != _pdk_fingerprint(full):
        return None
    entry = man.get("sections", {}).get(section)
    if not entry or not entry.get("validated"):
        return None
    p = Path(entry["path"])
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Validation -- a trimmed library is NOT used until it earns it
# ---------------------------------------------------------------------------
# Design points chosen to exercise the parts a trim could plausibly break:
#   - defaults
#   - low RL/Rs, which force the parallel-resistor path (m>1)
#   - large Cs, which is the MIM cap the r+c includes parameterise
#   - wide devices, which force the w_mult() splitter and the widest model bin
#   - small devices, which sit in the narrow model bins
VALIDATION_POINTS: tuple[dict, ...] = (
    {},
    {"RL": 120.0, "Rs": 80.0},
    {"Cs": 3e-12, "W": 90.0},
    {"W": 2.0, "L": 0.15, "Wtail": 5.0, "Itail": 60e-6, "Ig1": 120e-6},
    {"Wtail": 180.0, "Ig1": 3e-3, "tap_pct": 25.0},
)

# Every scalar on Metrics is compared. Bit-identical is the bar: the trimmed
# library selects the SAME model cards, so any difference at all means the
# dependency analysis dropped something that mattered.
VALIDATE_RTOL = 1e-9


def validate(section: str, out_dir: str | Path, corner=None,
             points: tuple[dict, ...] = VALIDATION_POINTS,
             workdir: str | Path = "/tmp/eqrl_trimval",
             verbose: bool = True) -> dict:
    """Prove a trimmed library gives identical answers to the full one.

    Returns a report dict. Raises TrimmedLibError on ANY disagreement -- the
    caller should let that propagate rather than fall back quietly, because a
    trimmed library that is wrong but usable is the worst possible outcome.
    """
    from .netlist import CtleParams
    from .spice import Simulator

    trimmed = Path(out_dir) / f"trimmed_{section}.lib.spice"
    if not trimmed.is_file():
        raise TrimmedLibError(f"{trimmed} does not exist -- build() it first")
    full = Path(pdk.find_sky130_lib())
    if corner is None:
        corner = _corner_for_section(section)

    rows, worst = [], 0.0
    for i, over in enumerate(points):
        p = CtleParams(**over)
        sim_full = Simulator(Path(workdir) / f"p{i}_full", timeout=600)
        sim_trim = Simulator(Path(workdir) / f"p{i}_trim", timeout=600)
        sim_trim._lib = str(trimmed)
        m_full = sim_full.evaluate(p, corner, do_eye=False)
        m_trim = sim_trim.evaluate(p, corner, do_eye=False)

        if not m_full.ok:
            raise TrimmedLibError(
                f"point {i} failed on the FULL library ({m_full.note}) -- "
                f"cannot validate against a broken reference")
        if not m_trim.ok:
            raise TrimmedLibError(
                f"point {i} {over} FAILED on the trimmed library: "
                f"{m_trim.note}. The trim dropped something required.")

        diffs = {}
        for k, vf in m_full.as_dict().items():
            vt = m_trim.as_dict()[k]
            if isinstance(vf, bool) or not isinstance(vf, (int, float)):
                if vf != vt:
                    diffs[k] = (vf, vt, float("inf"))
                continue
            if vf != vf and vt != vt:        # both NaN
                continue
            if vf != vf or vt != vt:         # one NaN
                diffs[k] = (vf, vt, float("inf"))
                continue
            rel = abs(vt - vf) / max(abs(vf), 1e-30)
            worst = max(worst, rel)
            if rel > VALIDATE_RTOL:
                diffs[k] = (vf, vt, rel)
        rows.append({"point": over, "diffs": diffs})
        if verbose:
            status = "MATCH" if not diffs else f"MISMATCH {list(diffs)}"
            print(f"  [{section}] point {i} {over or 'defaults'}: {status}")
        if diffs:
            detail = "; ".join(f"{k}: full={a:.10g} trim={b:.10g} rel={r:.2e}"
                               for k, (a, b, r) in diffs.items())
            raise TrimmedLibError(
                f"trimmed library for {section!r} DISAGREES with the full "
                f"library at point {over}: {detail}")

    man = load_manifest(out_dir)
    man.setdefault("sections", {})[section] = {
        "path": str(trimmed), "validated": True,
        "worst_rel_diff": worst, "n_points": len(points),
        "corner": corner.name,
    }
    man["pdk_fingerprint"] = _pdk_fingerprint(full)
    save_manifest(out_dir, man)
    return {"section": section, "points": rows, "worst_rel_diff": worst,
            "validated": True}


def _corner_for_section(section: str):
    """A Corner whose lib_section is exactly ``section``."""
    for proc in pdk.PROCESS_CORNERS:
        for rc in (None, "ll", "hh", "hl", "lh"):
            c = pdk.Corner(proc, pdk.VDD_NOM, 27.0, rc=rc)
            if c.lib_section == section:
                return c
    raise TrimmedLibError(f"no Corner maps to section {section!r}")
