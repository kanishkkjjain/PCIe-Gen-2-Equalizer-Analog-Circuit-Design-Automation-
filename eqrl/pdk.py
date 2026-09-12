"""SKY130 PDK discovery and PVT-corner helpers.

The volare-installed PDK ships an ngspice-ready model library that defines the
process corners as ``.lib <corner>`` sections inside a single file:

    <PDK_ROOT>/sky130A/libs.tech/ngspice/sky130.lib.spice

A netlist selects a corner with, e.g., ``.lib "<path>/sky130.lib.spice" tt``.
Supply voltage and temperature are *not* part of that section; they are applied
separately (VDD source value and the ``.temp`` card), which is exactly what we
need to walk the PVT grid the spec calls for.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Process corners required by the spec (TT, SS, FF, SF, FS).
PROCESS_CORNERS = ("tt", "ss", "ff", "sf", "fs")

# Nominal 1.8 V supply for the sky130 01v8 devices, with the +/-5% spec points.
VDD_NOM = 1.8
VDD_CORNERS = {"nom": VDD_NOM, "lo": VDD_NOM * 0.95, "hi": VDD_NOM * 1.05}

# Temperature corners (deg C): the 0..125 span plus room temperature.
TEMP_CORNERS = (0.0, 27.0, 125.0)


def _default_search_roots() -> list[Path]:
    roots = []
    env = os.environ.get("PDK_ROOT")
    if env:
        roots.append(Path(env))
    home = Path.home()
    roots += [
        home / ".volare" / "volare" / "sky130" / "versions",
        home / ".volare",
        home / ".ciel" / "sky130" / "versions",
        Path("/usr/local/share/pdk"),
        Path("/opt/pdk"),
    ]
    return roots


def find_sky130_lib() -> Path:
    """Return the path to sky130.lib.spice, searching common install roots."""
    override = os.environ.get("SKY130_LIB")
    if override and Path(override).is_file():
        return Path(override)
    for root in _default_search_roots():
        if not root.exists():
            continue
        hits = sorted(root.rglob("sky130A/libs.tech/ngspice/sky130.lib.spice"))
        if hits:
            return hits[0]
    raise FileNotFoundError(
        "Could not locate sky130.lib.spice. Set SKY130_LIB or PDK_ROOT. "
        "Install with: volare enable --pdk sky130 <version>"
    )


# Resistor/capacitor corners. These are a SEPARATE axis from the MOSFET process
# corners: `.lib ss` and `.lib tt` include the *same* res_typical__cap_typical
# models, so tt/ss/ff/sf/fs move transistors ONLY.
#
# Measured impact on this CTLE (identical MOSFET models, only R/C varied):
#     ll -> f_peak 2.824 GHz   tt -> 2.376 GHz   hh -> 2.117 GHz
# a 29.7% spread that VIOLATES the 2.5 GHz limit at `ll`. With ideal R/C this
# spread was structurally 0.00%, so the failure was invisible.
RC_CORNERS = (None, "ll", "hh", "hl", "lh")   # None = typical R and C


@dataclass(frozen=True)
class Corner:
    """A single PVT operating point."""

    process: str              # one of PROCESS_CORNERS
    vdd: float                # supply voltage (V)
    temp: float               # temperature (deg C)
    rc: str | None = None     # one of RC_CORNERS; None = typical R/C

    @property
    def lib_section(self) -> str:
        """The ``.lib`` section name selecting this process + R/C combination.

        Naming in sky130.lib.spice is irregular: typical-MOS + non-typical R/C
        sections are named by the R/C code alone (`ll`, `hh`, ...), not `tt_ll`.
        """
        if self.rc is None:
            return self.process
        if self.process == "tt":
            return self.rc
        return f"{self.process}_{self.rc}"

    @property
    def name(self) -> str:
        rc = f"_{self.rc}" if self.rc else ""
        return f"{self.process}{rc}_{self.vdd:.3f}V_{self.temp:.0f}C"


def typical_corner() -> Corner:
    """The nominal design corner used during the RL search."""
    return Corner(process="tt", vdd=VDD_NOM, temp=27.0)


GRID_MODES = ("quick", "spec", "full")


def pvt_grid(mode: str = "spec", full: bool | None = None) -> list[Corner]:
    """PVT corner list.

    The brief says "Meet across PVT (TT,SS,FF,SF,FS; VDD+-5%; 0-125C)". That is
    5 PROCESS corners x V x T = **20 corners minimum**, not 5 -- and it says
    nothing about R/C.

    | mode    | corners | what it is                                          |
    |---------|---------|-----------------------------------------------------|
    | "quick" |    9    | smoke test during development                       |
    | "spec"  |   20    | exactly the brief's cross-product, R/C typical      |
    | "full"  |   60    | + the R/C axis (ll/hh)                              |

    **Why "spec" is not enough for real confidence.** In most PDKs a corner named
    "SS" means slow transistors *and* slow R/C. sky130 does not work that way:
    `.lib ss` and `.lib tt` include the identical `res_typical__cap_typical`
    models, so the named corners move MOSFETs ONLY. R/C is a separate axis that
    physically exists and, measured on this CTLE, moves f_peak by 29.7% --
    more than the process corners do.

    Measured on the Itap=50uA design: worst-case f_peak is 2.517 GHz on the
    "spec" grid but 2.991 GHz on "full". Reporting only the spec grid
    understates the worst case by 19%.

    ``full=`` is a deprecated alias kept so existing callers keep working:
    full=True -> "full", full=False -> "quick".
    """
    if full is not None:
        mode = "full" if full else "quick"
    if mode not in GRID_MODES:
        raise ValueError(f"mode must be one of {GRID_MODES}, got {mode!r}")

    corners: list[Corner] = []
    if mode == "quick":
        for p in PROCESS_CORNERS:
            corners.append(Corner(p, VDD_NOM, 27.0))
        corners.append(Corner("ss", VDD_CORNERS["lo"], 125.0))  # slow/hot/low-V
        corners.append(Corner("ff", VDD_CORNERS["hi"], 0.0))    # fast/cold/high-V
        corners.append(Corner("tt", VDD_NOM, 27.0, rc="ll"))
        corners.append(Corner("tt", VDD_NOM, 27.0, rc="hh"))
        return corners

    # "spec" and "full" share the process x V x T cross-product; "full" adds R/C.
    rc_axis = (None,) if mode == "spec" else (None, "ll", "hh")
    for p in PROCESS_CORNERS:
        for v in (VDD_CORNERS["lo"], VDD_CORNERS["hi"]):
            for t in (0.0, 125.0):
                for rc in rc_axis:
                    corners.append(Corner(p, v, t, rc=rc))
    return corners


def corner_lib_line(process: str) -> str:
    """Build the ``.lib`` include line selecting a process corner."""
    lib = find_sky130_lib()
    return f'.lib "{lib}" {process}'
