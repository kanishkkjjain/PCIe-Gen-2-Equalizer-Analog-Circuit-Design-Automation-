"""`netlists/eq_core.sch` really is the circuit the code builds.

WHY THIS TEST EXISTS. Three schematics in `netlists/` are hand-drawn, were
correct when drawn, and now describe the OLD merged-summer topology in which the
DFE tap pair shared the CTLE's load resistors. Nothing broke when the code moved
to a separate summing stage, because nothing ever compared them -- a picture
cannot fail a test it is not given. They are labelled STALE in README and
HANDOVER, which is honest but is not a mechanism.

`eq_core.sch` is drawn for the demo and is shown as proof of what was built, so
it gets the mechanism: xschem netlists it, and the result is diffed
device-for-device against `CtleParams.full_netlist()`. Change the topology in
`netlist.py` and this fails, rather than the picture going quietly wrong.

CONNECTIVITY ONLY. The schematic carries the `CtleParams()` defaults while a
real design carries whatever the RL sized, so device VALUES are deliberately not
compared -- only which device connects to which node. That is exactly the claim
the schematic makes.

Marked `needs_pdk`: it shells out to xschem and needs the sky130 symbol library.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eqrl import netlist as nl

SCH = ROOT / "netlists" / "eq_core.sch"

#: Devices whose two signal terminals are physically interchangeable, so a
#: difference in their ORDER is not a difference in the circuit. xschem emits
#: this resistor's pins as (M, P, B) while `netlist.py` writes (P, M, B); a poly
#: resistor's two ends are symmetric, so both decks describe the same device.
#: Listed explicitly rather than handled by a blanket "sort all nodes", which
#: would also hide a real drain/source or gate/drain swap.
SYMMETRIC_2T = {"sky130_fd_pr__res_high_po_1p41"}

pytestmark = pytest.mark.needs_pdk


def _canon(inst: str, model: str, nodes: tuple[str, ...]) -> tuple:
    """One device, as connectivity a comparison can be made on."""
    nodes = tuple("0" if n.upper() == "GND" else n for n in nodes)
    if model in SYMMETRIC_2T and len(nodes) == 3:
        nodes = tuple(sorted(nodes[:2])) + nodes[2:]
    return (inst, model, nodes)


def _from_code() -> dict:
    """What `netlist.py` emits: `<inst> <nodes...> <model> <params...>`."""
    out = {}
    for line in nl.CtleParams().full_netlist().splitlines():
        s = line.strip()
        if not s or s.startswith(("*", ".")):
            continue
        f = s.split()
        inst = f[0]
        if inst.startswith("C"):                 # ideal cap: C<n> a b <value>
            out[inst] = _canon(inst, "capacitor", tuple(f[1:3]))
        elif inst.startswith("I"):               # isource: I<n> a b DC <value>
            out[inst] = _canon(inst, "isource", tuple(f[1:3]))
        else:                                    # X<n> <nodes...> <model> ...
            model = next(x for x in f if x.startswith("sky130_fd_pr__"))
            out[inst] = _canon(inst, model, tuple(f[1:f.index(model)]))
    return out


def _from_xschem(tmp_path: Path) -> dict:
    """What xschem makes of the schematic, with continuation lines joined."""
    subprocess.run(
        ["xschem", "-n", "-s", "-x", "-q", "-o", str(tmp_path), str(SCH)],
        check=True, capture_output=True, timeout=180)
    deck = (tmp_path / "eq_core.spice").read_text()

    joined: list[str] = []
    for raw in deck.splitlines():
        if raw.startswith("+"):
            joined[-1] += " " + raw[1:].strip()
        else:
            joined.append(raw.strip())

    out = {}
    for s in joined:
        if not s or s.startswith(("*", ".", "**")):
            continue
        f = s.split()
        inst = f[0]
        if inst.startswith("C"):
            out[inst] = _canon(inst, "capacitor", tuple(f[1:3]))
        elif inst.startswith("I"):
            out[inst] = _canon(inst, "isource", tuple(f[1:3]))
        else:
            model = next(x for x in f if x.startswith("sky130_fd_pr__"))
            out[inst] = _canon(inst, model, tuple(f[1:f.index(model)]))
    return out


@pytest.fixture(scope="module")
def decks(tmp_path_factory):
    if shutil.which("xschem") is None:
        pytest.skip("xschem is not installed")
    if not SCH.exists():
        pytest.fail(f"{SCH} is missing -- the demo's schematic")
    return _from_code(), _from_xschem(tmp_path_factory.mktemp("sch"))


# --------------------------------------------------------------------------
def test_the_schematic_has_exactly_the_devices_the_code_builds(decks):
    code, sch = decks
    assert set(sch) == set(code), (
        f"device sets differ.\n"
        f"  in the schematic only: {sorted(set(sch) - set(code))}\n"
        f"  in the netlist only:   {sorted(set(code) - set(sch))}")


def test_every_device_connects_to_the_same_nodes(decks):
    """The whole point. A gate wired to the wrong side of a differential pair
    is invisible in a picture and fatal in a circuit."""
    code, sch = decks
    wrong = {k: (code[k][2], sch[k][2]) for k in sorted(code)
             if k in sch and code[k][2] != sch[k][2]}
    assert not wrong, "\n".join(
        [f"{len(wrong)} device(s) wired differently (netlist -> schematic):"]
        + [f"  {k}: {a} -> {b}" for k, (a, b) in wrong.items()])


def test_the_models_match(decks):
    code, sch = decks
    wrong = {k: (code[k][1], sch[k][1]) for k in sorted(code)
             if k in sch and code[k][1] != sch[k][1]}
    assert not wrong, f"different device models: {wrong}"


def test_the_two_stages_have_separate_loads(decks):
    """The specific way the old schematics are wrong, pinned directly.

    In the merged topology the tap pair hung off the CTLE's `RLp/RLn` and there
    was no `Rsum`. Here the CTLE loads drive `out*_ctle` and the summer's own
    loads drive `out*_dfe`, and no device bridges the two.
    """
    _, sch = decks
    for r, node in (("XRLp", "outp_ctle"), ("XRLn", "outn_ctle"),
                    ("XRsp", "outp_dfe"), ("XRsn", "outn_dfe")):
        assert r in sch, f"{r} missing -- this is the merged-summer topology"
        assert node in sch[r][2], f"{r} does not drive {node}: {sch[r][2]}"


def test_the_gm1_gates_are_crossed(decks):
    """`XG1p` takes `outp_ctle` and drives `outn_dfe`. Getting this backwards
    inverts the stage, and it is the single easiest thing to mis-draw."""
    _, sch = decks
    assert sch["XG1p"][2][:3] == ("outn_dfe", "outp_ctle", "sgm1"), sch["XG1p"]
    assert sch["XG1n"][2][:3] == ("outp_dfe", "outn_ctle", "sgm1"), sch["XG1n"]


def test_no_node_is_left_floating(decks):
    """Every bulk terminal is tied by a gnd symbol placed on its exact pin
    coordinate. Miss one and the device silently gets a private node.

    The pattern is `net<n>`, verified by deleting X1's bulk tie and reading
    what xschem emitted: `X1 outn_ctle vinp sp net1 ...`. This test first looked
    for `#net<n>`, which xschem never produces -- it would have passed forever
    while checking nothing, which is the failure mode this whole file exists to
    prevent.
    """
    _, sch = decks
    auto = re.compile(r"^#?net\d+$")
    dangling = {k: v[2] for k, v in sch.items()
                if any(auto.match(n) for n in v[2])}
    assert not dangling, f"unconnected pins: {dangling}"
