# Verify It Yourself

Everything below is runnable. Nothing here asks you to trust a number I reported —
each step regenerates it.

```bash
export PDK_ROOT=/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b
cd ~/pcie_eq_rl
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

`Mtf`'s width sets the tap current (`wfb_tail = Wtail·Itap/Itail`):
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
from eqrl.netlist import CtleParams;print(CtleParams().core_netlist())"
```

Resolved geometry and area:

```bash
python3 -c "import sys;sys.path.insert(0,'.');\
from eqrl.netlist import CtleParams;p=CtleParams();\
print('geometry:',{k:round(v,3) for k,v in p.geometry().items()});\
print('area    :',{k:round(v,2) for k,v in p.area_um2().items()});\
print('tap wt  :',p.tap_weight_v()*1e3,'mV')"
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
from eqrl.netlist import CtleParams;print(CtleParams().core_netlist())"
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
ngspice ac.sp                          # interactive; then:  plot vdb(voutp,voutn) xlog
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
python3 scripts/plot_eye.py --itap 10e-6  -o /tmp/eye_good.png
python3 scripts/plot_eye.py --itap 300e-6 -o /tmp/eye_bad.png

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
for it in (10e-6,50e-6,150e-6,300e-6):
    r=s.tran_eye(CtleParams(Itap=it),pdk.typical_corner(),n_bits=160,tag=f'e{int(it*1e6)}')
    e=r['eye'];print(f\"  Itap={it*1e6:5.0f}uA  height={e.height_v*1e3:6.1f}mV  width={e.width_ui:5.3f}UI\")"
```

### Full PVT — and why "5 corners" is a miscount
The brief's `TT,SS,FF,SF,FS; VDD±5%; 0–125 °C` is 5 process × V × T = **20**
corners, not 5. sky130 keeps R/C on a *separate* axis, so the named corners move
MOSFETs only — `--grid full` adds it.

```bash
python3 scripts/pvt_sweep.py --grid spec --eye --set Itap=50e-6   #  20 corners ->  4 fail, worst f_peak 2.517 GHz
python3 scripts/pvt_sweep.py --grid full --eye --set Itap=50e-6   #  60 corners -> 18 fail, worst f_peak 2.991 GHz
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
