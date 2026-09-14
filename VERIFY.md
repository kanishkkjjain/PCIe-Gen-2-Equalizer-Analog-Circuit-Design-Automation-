# Verify It Yourself

Everything below is runnable. Nothing here asks you to trust a number I reported —
each step regenerates it.

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b
cd ~/pcie_eq_rl
```

---

## 0. Run the test suite

The fastest check that nothing is broken. The pure tier needs no PDK and no
ngspice, so it runs anywhere in under a second:

```bash
python3 -m pytest tests/ -q -m "not needs_pdk"   # 164 tests, ~32 s, no PDK
python3 -m pytest tests/ -q                      # all 201, ~2 min with the PDK
```

Two of those tests are worth reading on their own, because they pin device-model
conventions the entire design rests on and which the documentation had wrong:

```bash
# `mult=` does NOTHING on a sky130 resistor; `m=` is the real multiplier
python3 -m pytest tests/test_spice.py::test_resistor_parallel_units_need_m_not_mult -q

# W is TOTAL width, not per-finger (W=40/nf=4 == 4x W=10/nf=1, bit-identical)
python3 -m pytest tests/test_spice.py::test_nfet_w_is_total_width -q
```

Reproduce the `mult=` result by hand in ten lines of SPICE:

```bash
cat > /tmp/rtest.sp <<'EOF'
.lib "$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.temp 27
Va a 0 DC 1
Vb b 0 DC 1
Vc c 0 DC 1
XA a 0 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=5
XB b 0 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=5 mult=4
XC c 0 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=5 m=4
.control
op
print abs(1/i(Va)) abs(1/i(Vb)) abs(1/i(Vc))
.endc
.end
EOF
ngspice -b /tmp/rtest.sp
#   one unit 1407.6 ohm | mult=4 1407.6 ohm (!) | m=4 351.9 ohm
```

Then read the model source and see why — `mult` appears only in the mismatch
terms, never in the two resistance elements:

```bash
awk '/^\.subckt  *sky130_fd_pr__res_high_po_1p41/,/^\.ends/' \
  $PDK_ROOT/sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__res_high_po_1p41.model.spice \
  | grep -nE "mult|^rend|^rhrpoly"
```

---

## 1. See the schematic

```bash
xschem netlists/eq_tb.sch        # full equalizer, DFE loop OPEN   (op/ac/noise)
xschem netlists/eq_dfe_tb.sch    # full equalizer, DFE loop CLOSED (eye) - Shift+S
xschem netlists/ctle_tb.sch      # older CTLE-only version, kept for reference
```

**Press `f` first.** The DFE tap pair (`M3`/`M4`/`Mtf`) sits at x≈1100-1320, far
right of the CTLE at x≈300-720 — off-screen until you zoom to fit.

⚠️ These schematics show the **OLD merged topology** and are stale — see
`HANDOVER.md` §11. The historical note below refers to that version:
W=30 → 300 µA (eye closed, 0.031 UI), W=5 → 50 µA (eye open, 0.797 UI).
Click `Mtf`, press `q`, change `W`.

A window opens on your Windows desktop (WSLg). Useful keys:

| Key | Action |
|---|---|
| `f` | zoom to fit |
| click device, then `q` | edit its properties (W, L, nf, …) |
| `Shift+N` | generate SPICE netlist |
| `Shift+S` | run the simulation |
| `Escape` | deselect |

**`eq_tb.sch` is the current design.** `ctle_tb.sch` predates the summer/DFE and
is CTLE-only — don't use it to judge the present circuit.

### What the schematic does NOT show
It contains the **summer only**: M3/M4 sharing RLp/RLn with the CTLE. The tap
gates `vfbp`/`vfbn` are held at a static 1.1 V, so **the DFE loop is open**.
That is correct for characterising the forward path (the tap pair still loads
the output and draws current but injects no signal), and it is what the `op`/
`ac`/`noise` analyses need.

The **slicer, the 1 UI latch and the feedback drive exist only in code**
(`netlist.py::dfe_decision_path()`, emitted as XSPICE) and appear only in the
closed-loop deck. To see the real loop:
```bash
python3 -c "import sys;sys.path.insert(0,'.');from eqrl.netlist import dfe_decision_path;print(dfe_decision_path())"
sed -n '/DFE decision path/,/model dff/p' /tmp/eqrl_dfe/eye.sp   # after a tran_eye run
```

### Why it looks label-heavy rather than wire-heavy
Nets are joined by `lab_pin` labels, not long wires. That is deliberate: a wire
that misses a pin by one grid unit produces a schematic that *looks* right and
netlists a **floating node**. This exact bug bit twice while building this file
(see §3), which is why the netlist diff below is the real check, not your eyes.

---

## 2. See the connections (the ground truth)

The Python netlist is what the framework actually simulates:

```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;print(CtleParams().full_netlist())"
```

Resolved geometry and area:

```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;p=CtleParams();\
print('geometry:',{k:round(v,3) for k,v in p.geometry().items()});\
print('area    :',{k:round(v,2) for k,v in p.area_um2().items()});\
print('tap wt  :',p.tap_weight_ratio(),'= gmF/gm1 nominal')"
```

---

## 3. Cross-check schematic against code (the real verification)

Two independent netlisters must agree. Anything they disagree on is a bug in one
of them.

```bash
rm -f ~/.xschem/simulations/eq_tb.spice
xschem -x -r -s --command 'xschem netlist; exit' netlists/eq_tb.sch </dev/null

# (a) any unnamed net means something is DISCONNECTED
grep -oE "net[0-9]+" ~/.xschem/simulations/eq_tb.spice | sort -u
#     ^ empty output = good

# (b) compare device-by-device
grep -E "^X|^CL" ~/.xschem/simulations/eq_tb.spice | grep -v '^\*'
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;print(CtleParams().full_netlist())"
```

**Expected differences that are NOT bugs:**
- `XM1` vs `X1` — instance naming only.
- `XRs sn sp GND` vs `XRs sp sn 0` — resistor terminals reversed. A resistor is
  symmetric, so this is electrically identical.
- `GND` vs `0` — same node.
- xschem omits `w=1.41` on resistors (the symbol fixes width by variant); the
  Python netlist passes it explicitly. Same value.

**Anything else is a real disconnect.**

---

## 4. Run the test setups

Every analysis writes its actual deck to disk. These *are* the testbenches —
read them, edit them, run them standalone.

```bash
python3 scripts/smoke_test.py          # writes decks to /tmp/eqrl_smoke/
ls /tmp/eqrl_smoke/                    # op.sp ac.sp tran.sp noise.sp + .dat
cat /tmp/eqrl_smoke/ac.sp              # the exact AC testbench used
```

Run one by hand:

```bash
cd /tmp/eqrl_smoke
cp $PDK_ROOT/sky130A/libs.tech/ngspice/spinit .spiceinit   # REQUIRED
ngspice -b ac.sp                       # batch
ngspice ac.sp                          # interactive; then:  plot vdb(outp_ctle,outn_ctle) xlog
```

Run the schematic's own netlist:

```bash
cd ~/.xschem/simulations
cp $PDK_ROOT/sky130A/libs.tech/ngspice/spinit .spiceinit
ngspice -b eq_tb.spice
```

> `.spiceinit` is **required for correctness**, not speed. Without it,
> xschem-generated netlists fail with `Formula() error` /
> "No compatibility mode selected".

---

## 5. Verify the plots

```bash
# eye diagram (closed-loop DFE, real XSPICE slicer + 1 UI latch)
# tap weight is ONE variable (tap_pct), scaling the GmF branch's width and
# current together. `--igf` used to exist and crashed once Igf became a
# read-only property -- HANDOVER 10 bug 26.
python3 scripts/plot_eye.py --tap-pct 1.0  -o /tmp/eye_small_tap.png
python3 scripts/plot_eye.py --tap-pct 30.0 -o /tmp/eye_big_tap.png
python3 scripts/plot_eye.py -o /tmp/eye.png          # the default 5% tap

# --tap-pct 0 is HONOURED, not treated as "not supplied"
python3 scripts/plot_eye.py --tap-pct 0 -o /tmp/eye_no_tap.png

# any wrdata dump; auto-detects AC vs transient
python3 scripts/plot_wave.py /tmp/eqrl_smoke/ac.dat   --open
python3 scripts/plot_wave.py /tmp/eqrl_smoke/tran.dat --open
```

`--open` pushes the PNG to Windows via `explorer.exe`.

---

## 6. Reproduce the headline claims

Each of these regenerates a number quoted in `HANDOVER.md`.

### The tap weight is real (expect ~644 mV, ~89% efficiency)
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
print(Simulator('/tmp/v1').tap_weight(CtleParams(), pdk.typical_corner()))"
```

### R/C corners move f_peak ~30% (this is why ideal passives were unacceptable)
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v2');p=CtleParams()
for c in ('ll','tt','hh'):
    r=s.ac(p,pdk.Corner(c,1.8,27.0),tag='ac_'+c)
    print(f\"  {c}: f_peak={r['f_peak']/1e9:.3f} GHz  peaking={r['peaking_db']:.2f} dB\")"
```
Expect **ll 2.824 / tt 2.376 / hh 2.117 GHz** — identical MOSFET models, only R
and C differ. `ll` violates the 2.5 GHz spec.

### The tail really does need Vcm = 1.10 V
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v3')
for vcm in (0.90,1.05,1.10):
    o=s.op(CtleParams(Vcm=vcm),pdk.typical_corner(),tag=f'op{int(vcm*100)}')
    print(f\"  Vcm={vcm}: tail vds={o['vds_tail']*1e3:.0f}mV vdsat={o['vdsat_tail']*1e3:.0f}mV \"
          f\"sat={o['tail_saturated']} id={o['id_tail']*1e6:.0f}uA\")"
```
At 0.90 V the tail is **in triode** and delivers ~477 µA instead of 600 µA.

### PDK parse dominates runtime (4 ms vs ~7 s)
```bash
cd /tmp && printf '* rc\nV1 a 0 DC 1\nR1 a b 1k\nC1 b 0 1n\n.control\nop\n.endc\n.end\n' > nopdk.sp
time ngspice -b nopdk.sp >/dev/null 2>&1                 # ~4 ms
time ngspice -b /tmp/eqrl_smoke/op.sp >/dev/null 2>&1    # ~7 s
```

### Eye collapses as the tap grows (no channel -> correct tap is ~0)
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v4',timeout=600)
for it in (1.0,5.0,10.0,30.0):
    r=s.tran_eye(CtleParams(tap_pct=it),pdk.typical_corner(),n_bits=160,tag=f'e{int(it)}')
    e=r['eye'];print(f\"  tap={it:5.1f}%  height={e.height_v*1e3:6.1f}mV  width={e.width_ui:5.3f}UI\")"
```

### Full PVT — and why "5 corners" is a miscount
The brief's `TT,SS,FF,SF,FS; VDD±5%; 0–125 °C` is 5 process × V × T = **20**
corners, not 5. sky130 keeps R/C on a *separate* axis, so the named corners move
MOSFETs only — `--grid full` adds it.

```bash
python3 scripts/pvt_sweep.py --grid spec --eye --set tap_pct=5.0   # the brief's 20 corners
python3 scripts/pvt_sweep.py --grid full --eye --set tap_pct=5.0   # + the R/C axis, 60 corners
```
Reporting only the spec grid understates the worst case by 19%.

### Per-corner evaluation (older one-liner form)
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk,specs as sp;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v5');p=CtleParams();spec=sp.EqualizerSpec()
for c in pdk.pvt_grid():
    m=s.evaluate(p,c,do_hd3=False,do_noise=False)
    ok=spec.fpeak_lo<=m.f_peak<=spec.fpeak_hi
    print(f\"  {c.name:24s} {m.f_peak/1e9:6.3f}GHz  {'ok' if ok else 'FAIL'}\")"
```

### Prove that NO speed optimisation is in use
A full 12-device AC analysis should cost the same as parsing the library with a
single resistor — i.e. the circuit solve is free and everything is parse time.
```bash
cd /tmp && printf '* p\n.lib "%s" tt\n.temp 27\nV1 a 0 DC 1\nR1 a 0 1k\n.control\nop\n.endc\n.end\n' \
  "$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice" > parseonly.sp
time ngspice -b parseonly.sp            >/dev/null 2>&1   # ~7.61 s
time ngspice -b /tmp/eqrl_smoke/ac.sp   >/dev/null 2>&1   # ~7.60 s  <- same!
```

### Prove ngspice's noise integration is the broken part
The spectrum is clean at every corner; only `inoise_total` is `-nan`.
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v8')
for c in ('tt','ss','fs'):
    r=s.noise(CtleParams(),pdk.Corner(c,1.8,27.),tag=c)
    ng=r.get('ngspice_inoise_total')
    print(f\"  {c}: ours={r['vn_in_rms']*1e3:.3f} mVrms  ngspice={'nan(broken)' if (ng is None or ng!=ng) else f'{ng*1e3:.3f}'}\")"
```
Expect ours ≈ 0.50 mVrms at all three; ngspice returns `nan` at `ss` and `fs`.
Where ngspice *does* work (`tt`) the two agree to 1.4 %.

### Prove peaking is tunable across the required band
Rs sets the boost, Cs sets the zero. Hold Rs, sweep Cs:
```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
s=Simulator('/tmp/v9')
for cs in (500,800,1200):
    r=s.ac(CtleParams(Rs=1500.,Cs=cs*1e-15),pdk.typical_corner(),tag=f'c{cs}')
    print(f\"  Cs={cs}fF -> f_peak {r['f_peak']/1e9:.2f} GHz, peaking {r['peaking_db']:.2f} dB\")"
```
Expect 2.00 / 1.59 / 1.26 GHz at 10.3 / 11.2 / 11.7 dB — all inside spec.

---

## 7. Sanity checks that should FAIL

If these pass, something is wrong with the framework, not the design.

```bash
# tiny load resistor -> PEAK GAIN must collapse (8.91 -> ~4.76 dB).
# NOTE: `peaking` actually RISES (9.03 -> ~11.55 dB), because a small RL pushes
# the output pole up so more of the degeneration boost is realised even as
# absolute gain falls. Watch peak_gain_db, not peaking_db.
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
r=Simulator('/tmp/v6').ac(CtleParams(RL=100.0),pdk.typical_corner());\
print('peak_gain',round(r['peak_gain_db'],2),'dB   peaking',round(r['peaking_db'],2),'dB')"

# starving the tail breaks saturation: expect sat=False, vds 266mV < vdsat 891mV,
# and only ~776uA delivered instead of the requested 2mA
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl import pdk;from eqrl.netlist import CtleParams;from eqrl.spice import Simulator;\
o=Simulator('/tmp/v7').op(CtleParams(Itail=2e-3,Wtail=5.0),pdk.typical_corner());\
print('tail saturated:',o['tail_saturated'],' vds',round(o['vds_tail']*1e3),'mV vdsat',round(o['vdsat_tail']*1e3),'mV')"
```

---

## 8. If something looks wrong

| Symptom | Likely cause |
|---|---|
| `could not find a valid modelname` | W/L scaling broken, or device missing from the loaded corner |
| `Formula() error` / "No compatibility mode" | missing `.spiceinit` in the working directory |
| `Undefined parameter [mc_mm_switch]` | used `.include` on a corner file instead of `.lib` |
| xschem: "Unable to open file … /home/…/home/…" | `~/.xschem/xschemrc` was *copied* instead of sourcing the PDK one |
| xschem: symbols not found | same cause — `xschemrc` must `source` the PDK file in place |
| `net1`, `net2` in a netlist | a wire misses a pin → floating node |
| metric is `nan` | analysis failed; check the deck in `/tmp/eqrl_*/` and run it by hand |
| every candidate scores 0.2 | `op()` probe mismatch — check `netlist.NFET` matches the device |

---

## 9. The two-stage topology — check the summing node is real

The single claim that matters: **both transconductors drain into the same load
pair.** If `GmF` had its own loads it would be a separate stage and nothing
would sum.

```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;print(CtleParams().summer_netlist())"
```

`XG1n`, `XGFn` and `XRsp` must all name `outp_dfe`; `XG1p`, `XGFp`, `XRsn` must
all name `outn_dfe`. Node-by-node:

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0,'.')
from eqrl.netlist import CtleParams, NFET, RES, CAP
PORTS={NFET:["d","g","s","b"],RES:["r0","r1","b"],CAP:["c0","c1"]}
net={}
for line in CtleParams().full_netlist().splitlines():
    if not line or line.startswith("*"): continue
    t=line.split(); body=[x for x in t[1:] if "=" not in x]
    if t[0].startswith("X"): nodes,pn=body[:-1],PORTS.get(body[-1],[])
    elif t[0].startswith("C"): nodes,pn=body[:2],["+","-"]
    elif t[0].startswith("I"): nodes,pn=body[:2],["from","to"]
    else: nodes,pn=body,[]
    for i,nd in enumerate(nodes):
        net.setdefault(nd,[]).append(f"{t[0]}.{pn[i] if i<len(pn) else i}")
for nd in sorted(net,key=lambda x:(-len(net[x]),x)):
    print(f"{nd:10s} {', '.join(net[nd])}")
EOF
```

Expected: `outp_dfe` = `XRsp.r1, XG1n.d, XGFn.d`, and only four one-connection
nodes (`vinp`, `vinn`, `vfbp`, `vfbn`) — those are **ports**, not floating nets.

## 10. The channel — measure its loss yourself

```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import channel_netlist;print(channel_netlist())" | head -5
```

Its measured response (`HANDOVER.md` §10b): −0.93 / −4.95 / −11.10 / −16.56 dB
at 0.1 / 1 / 2.5 / 5 GHz. To confirm the equaliser's own peaking is reported
independently of it — the `ac()` result must be the **same** either way:

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0,'.')
from eqrl import pdk
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator
p, c = CtleParams(), pdk.typical_corner()
for ch in (False, True):
    a = Simulator(f"/tmp/v_ch{ch}", use_channel=ch).ac(p, c)
    print(f"channel={'ON ' if ch else 'OFF'} peaking {a['peaking_db']:.2f} dB @ {a['f_peak']/1e9:.3f} GHz")
EOF
```

## 11. The DFE earns its keep (or does not) — sweep the tap

Scaled the reference's way: `tap_pct` moves **both** width and current.

```bash
python3 scripts/pvt_sweep.py --grid quick --eye --set tap_pct=1.0
python3 scripts/pvt_sweep.py --grid quick --eye --set tap_pct=5.0
python3 scripts/pvt_sweep.py --grid quick --eye --set tap_pct=30.0
```

Expect the eye to peak near 5 % and collapse by 30 % — the same shape the
reference design reports (`HANDOVER.md` §17).

## 12. The analytic starting point beats the box centre

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0,'.')
from eqrl import pdk, analytic, netlist as nl, specs as sp
from eqrl.netlist import CtleParams
from eqrl.spice import Simulator
sim, spec = Simulator("/tmp/v_an", use_channel=True), sp.EqualizerSpec()
for name, p in (("analytic",   analytic.initial_design(spec, sim)),
                ("box centre", CtleParams(**nl.denorm([0.5]*len(nl.PARAM_NAMES))))):
    sc = sp.score(sim.evaluate(p, pdk.typical_corner(), do_eye=True), spec)
    print(f"{name:12s} reward {sc['reward']:.3f}  pass {sum(sc['pass'].values())}/{len(sc['pass'])}")
EOF
```

Measured **at the typical corner only**, which flatters both — the real
comparison is across PVT and is what `scripts/seed_benchmark.py` reports:

```bash
python3 scripts/seed_benchmark.py     # ~1.7 min, writes results/seed_vs_search.json
```

| design point | ungated reward | brief specs over 20 corners |
|---|---|---|
| blind start | 0.4631 | 3 / 8 |
| analytic seed | 0.9050 | 5 / 8 (fails eye_h, eye_w, hd3) |
| after CEM | 0.9873 | 8 / 8 — passes |

The `after CEM` row is checked against `report.json`: reward, binding spec and
all eight margins must agree to 1e-9, or the harness is measuring under
different conditions and the other two rows mean nothing.

## 13. The reward can actually see a spec boundary

The bug that stalled a whole run (`HANDOVER.md` §10 bug 20) — a design 10 MHz
over the `f_peak` limit used to score 0.99972 against 1.00000 for one that
passes, which is below sampling noise:

```bash
python3 -m pytest tests/test_pure.py -q -k "boundary or all_pass_design or more_specs"
```

## 14. Reproduce the headline result

```bash
python3 scripts/build_trimlib.py --grid spec        # ~11 min, one-time
python3 scripts/run_design.py --robust --pvt-grid spec --eye --channel \
        --pop 48 --iters 15 --seed 0
```

Then check the exported deck reproduces its own report.

⚠️ **Check the EYE deck, not just the AC deck.** `ac()` divides out
`vdb(vinp,vinn)`, so peaking and `f_peak` are channel-invariant *by
construction* — the AC deck agrees to 0.000 % whether or not it contains the
channel, so it **cannot** detect a missing one. That is exactly how defect D1
shipped (HANDOVER §11), and why this comparison exists:

```bash
cd results && ngspice -b ctle_final_eye.sp && python3 -c "
import numpy as np, json
from pathlib import Path; import sys; sys.path.insert(0,'..')
from eqrl import dfe
d=np.loadtxt('eye.dat')
e=dfe.eye_from_waveform(d[:,0], d[:,1], 200e-12)
r=json.load(open('report.json'))['typ_metrics']
print('deck  ', round(e.height_v*1e3,1),'mV', round(e.width_ui,3),'UI')
print('report', round(r['eye_height_v']*1e3,1),'mV', round(r['eye_width_ui'],3),'UI')"
```

Measured after the D1 fix: deck and report agree to **0.00 %**
(202.1 mV × 0.609 UI both, re-verified against the current `report.json`).
Before the fix the deck read **591.8 mV × 0.750 UI** — 3.1× optimistic —
because it had no channel, and it exited 0 while doing so.

The AC comparison below should also hold:

```bash
cd results && ngspice -b ctle_final.sp && python3 -c "
import numpy as np, json
d=np.loadtxt('ctle_ac.dat'); g=d[:,1]-d[:,5]; i=int(np.argmax(g))
r=json.load(open('report.json'))['typ_metrics']
print('deck  ', round(d[i,0]/1e9,4),'GHz', round(g[i]-g[0],2),'dB')
print('report', round(r['f_peak']/1e9,4),'GHz', round(r['peaking_db'],2),'dB')"
```

Measured agreement: **0.000 %**.
