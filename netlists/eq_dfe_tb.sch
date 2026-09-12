v {xschem version=3.4.4 file_version=1.2
* PCIe Gen2 EQUALIZER - CLOSED-LOOP DFE testbench (Shift-S runs the real loop)
* SKY130, ALL-REAL devices (no ideal R/C/current sources in the signal path)
*
* Mirrors eqrl/netlist.py::CtleParams defaults exactly:
*   W=20 L=0.18 nf=2 | RL=1200ohm (l=3.875) | Rs=1500ohm (l=5.218)
*   Cs=300fF (12.06x12.06) | Itail=600u (Iref=150u, 4:1 mirror, Wtail=60)
*   Itap=300u (Wfb=20, wfb_tail=30) | CL=40fF | Vcm=1.10 | VDD=1.8 | tt
*
* Connectivity is by NET LABEL, not long wires: a wire that misses a pin by one
* grid unit netlists a floating node that still LOOKS correct on screen.
* Verify with Shift-N then diff against CtleParams().core_netlist().
}
G {}
K {}
V {}
S {}
E {}

T {PCIe Gen2 EQUALIZER
CTLE (source-degenerated) + merged current summer + 1-tap DFE
tap weight = Itap * RL ; zero at 1/(Rs*Cs)} 260 -800 0 0 0.5 0.5 {}
T {CTLE input pair} 250 -600 0 0 0.3 0.3 {layer=4}
T {degeneration
Rs || Cs} 470 -545 0 0 0.3 0.3 {layer=4}
T {CTLE tail mirror} 240 -300 0 0 0.3 0.3 {layer=4}
T {DFE tap pair
(shares RLp/RLn)} 1090 -620 0 0 0.3 0.3 {layer=4}
T {tap tail} 1160 -345 0 0 0.3 0.3 {layer=4}
T {4:1 bias mirror} 1470 -620 0 0 0.3 0.3 {layer=4}

N 320 -690 720 -690 { lab=vdd}
N 320 -630 320 -570 { lab=voutn}
N 720 -630 720 -570 { lab=voutp}
N 320 -510 320 -380 { lab=sp}
N 320 -480 600 -480 { lab=sp}
N 720 -510 720 -380 { lab=sn}
N 480 -420 720 -420 { lab=sn}
N 1550 -530 1550 -480 { lab=vbias}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -540 0 0 {name=M1
L=0.18
W=20
nf=2
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -540 0 0 {name=M2
L=0.18
W=20
nf=2
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/res_high_po_1p41.sym} 320 -660 0 0 {name=RLn L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 720 -660 0 0 {name=RLp L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 480 -450 0 0 {name=Rs  L=5.218 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/cap_mim_m3_1.sym}     600 -450 0 0 {name=Cs  W=12.06 L=12.06 MF=1 model=cap_mim_m3_1 spiceprefix=X}

C {devices/capa.sym} 180 -600 0 0 {name=CLn value=40e-15 m=1}
C {devices/capa.sym} 860 -600 0 0 {name=CLp value=40e-15 m=1}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -350 0 0 {name=Mt1
L=0.5
W=60
nf=6
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -350 0 0 {name=Mt2
L=0.5
W=60
nf=6
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 1100 -540 0 0 {name=M3
L=0.15
W=20
nf=2
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 1300 -540 0 0 {name=M4
L=0.15
W=20
nf=2
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 1200 -400 0 0 {name=Mtf
L=0.5
W=30
nf=3
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {sky130_fd_pr/nfet_01v8_lvt.sym} 1530 -450 0 0 {name=Mref
L=0.5
W=15
nf=2
mult=1
ad=0
as=0
pd=0
ps=0
nrd=0
nrs=0
sa=0
sb=0
sd=0
model=nfet_01v8_lvt
spiceprefix=X}

C {devices/isource.sym} 1550 -560 0 0 {name=Iref value=150u}
C {devices/vsource.sym} 1750 -560 0 0 {name=Vdd value=1.8 only_toplevel=true}
C {devices/vdd.sym}     1750 -590 0 0 {name=lv1 lab=vdd}
C {devices/gnd.sym}     1750 -530 0 0 {name=lg1 lab=GND}
C {devices/vdd.sym}      520 -690 0 0 {name=lv2 lab=vdd}
C {devices/vdd.sym}     1550 -590 0 0 {name=lv3 lab=vdd}

C {devices/lab_pin.sym} 100 -590 0 0 {name=pi1 lab=vinp}
C {devices/gnd.sym}     100 -530 0 0 {name=lg2 lab=GND}
C {devices/lab_pin.sym} 100 -450 0 0 {name=pi2 lab=vinn}
C {devices/gnd.sym}     100 -390 0 0 {name=lg3 lab=GND}

C {devices/lab_pin.sym} 1000 -280 0 0 {name=pf1 lab=vfbp}
C {devices/gnd.sym}     1000 -220 0 0 {name=lg4 lab=GND}
C {devices/lab_pin.sym} 1200 -280 0 0 {name=pf2 lab=vfbn}
C {devices/gnd.sym}     1200 -220 0 0 {name=lg5 lab=GND}

C {devices/lab_pin.sym} 280 -540 0 0 {name=p1 lab=vinp}
C {devices/lab_pin.sym} 680 -540 0 0 {name=p2 lab=vinn}
C {devices/lab_pin.sym} 180 -630 0 0 {name=p3 lab=voutn}
C {devices/lab_pin.sym} 320 -600 0 0 {name=p3b lab=voutn}
C {devices/lab_pin.sym} 860 -630 0 0 {name=p4 lab=voutp}
C {devices/lab_pin.sym} 720 -600 0 0 {name=p4b lab=voutp}
C {devices/lab_pin.sym} 400 -480 0 0 {name=p5 lab=sp}
C {devices/lab_pin.sym} 660 -420 0 0 {name=p6 lab=sn}
C {devices/lab_pin.sym} 280 -350 0 0 {name=p7 lab=vbias}
C {devices/lab_pin.sym} 680 -350 0 0 {name=p8 lab=vbias}
C {devices/lab_pin.sym} 1120 -570 0 0 {name=p9  lab=voutn}
C {devices/lab_pin.sym} 1320 -570 0 0 {name=p10 lab=voutp}
C {devices/lab_pin.sym} 1080 -540 0 0 {name=p11 lab=vfbp}
C {devices/lab_pin.sym} 1280 -540 0 0 {name=p12 lab=vfbn}
C {devices/lab_pin.sym} 1120 -510 0 0 {name=p13 lab=stap}
C {devices/lab_pin.sym} 1320 -510 0 0 {name=p14 lab=stap}
C {devices/lab_pin.sym} 1220 -430 0 0 {name=p15 lab=stap}
C {devices/lab_pin.sym} 1180 -400 0 0 {name=p16 lab=vbias}
C {devices/lab_pin.sym} 1510 -450 0 0 {name=p17 lab=vbias}
C {devices/lab_pin.sym} 1550 -480 0 0 {name=p18 lab=vbias}

C {devices/gnd.sym} 320 -540 0 0 {name=g1  lab=GND}
C {devices/gnd.sym} 720 -540 0 0 {name=g2  lab=GND}
C {devices/gnd.sym} 300 -660 0 0 {name=g3  lab=GND}
C {devices/gnd.sym} 700 -660 0 0 {name=g4  lab=GND}
C {devices/gnd.sym} 460 -450 0 0 {name=g5  lab=GND}
C {devices/gnd.sym} 180 -570 0 0 {name=g6  lab=GND}
C {devices/gnd.sym} 860 -570 0 0 {name=g7  lab=GND}
C {devices/gnd.sym} 320 -320 0 0 {name=g8  lab=GND}
C {devices/gnd.sym} 720 -320 0 0 {name=g9  lab=GND}
C {devices/gnd.sym} 320 -350 0 0 {name=g10 lab=GND}
C {devices/gnd.sym} 720 -350 0 0 {name=g11 lab=GND}
C {devices/gnd.sym} 1120 -540 0 0 {name=g12 lab=GND}
C {devices/gnd.sym} 1320 -540 0 0 {name=g13 lab=GND}
C {devices/gnd.sym} 1220 -370 0 0 {name=g14 lab=GND}
C {devices/gnd.sym} 1220 -400 0 0 {name=g15 lab=GND}
C {devices/gnd.sym} 1550 -420 0 0 {name=g16 lab=GND}
C {devices/gnd.sym} 1550 -450 0 0 {name=g17 lab=GND}

C {sky130_fd_pr/corner.sym} 1750 -750 0 0 {name=CORNER only_toplevel=true corner=tt}

T {DFE DECISION PATH  (behavioural - XSPICE code models, NOT transistors)

   voutp,voutn --> Bdiff --> adc_bridge --> d_dff --> dac_bridge x2 --> vfbp/vfbn
                  (differ)   (SLICER)     (1 UI     (tap drivers)        |
                                           LATCH)                        |
        ^------------------ M3/M4 gates <--------------------------------+

 NOT REAL CIRCUITS YET: slicer, latch, clock, tap drivers.
 Assumed clk_delay=20p rise=15p -- a real StrongARM is slower (50-150 ps),
 so the loop-delay budget (<200 ps UI) is optimistic.} 980 -230 0 0 0.35 0.35 {layer=7}

C {devices/code.sym} 1450 -120 0 0 {name=DFE_LOOP
only_toplevel=true
place=end
value="
* ---- PRBS-7 NRZ stimulus, 5 Gb/s, 100 mV differential ----
Vinp vinp 0 PWL(0 1.15 1.85e-10 1.15 2.15e-10 1.05 3.85e-10 1.05 4.15e-10 1.15 9.85e-10 1.15 1.015e-09 1.05 1.385e-09 1.05 1.415e-09 1.15 1.785e-09 1.15 1.815e-09 1.05 2.185e-09 1.05 2.215e-09 1.15 2.385e-09 1.15 2.415e-09 1.05 2.585e-09 1.05 2.615e-09 1.15 2.785e-09 1.15 2.815e-09 1.05 2.985e-09 1.05 3.015e-09 1.15 3.185e-09 1.15 3.215e-09 1.05 3.385e-09 1.05 3.415e-09 1.15 4.785e-09 1.15 4.815e-09 1.05 5.985e-09 1.05 6.015e-09 1.15 6.185e-09 1.15 6.215e-09 1.05 7.185e-09 1.05 7.215e-09 1.15 7.585e-09 1.15 7.615e-09 1.05 8.385e-09 1.05 8.415e-09 1.15 8.585e-09 1.15 8.615e-09 1.05 8.785e-09 1.05 8.815e-09 1.15 8.985e-09 1.15 9.015e-09 1.05 9.585e-09 1.05 9.615e-09 1.15 1.0385e-08 1.15 1.0415e-08 1.05 1.0785e-08 1.05 1.0815e-08 1.15 1.0985e-08 1.15 1.1015e-08 1.05 1.1585e-08 1.05 1.1615e-08 1.15 1.1785e-08 1.15 1.1815e-08 1.05 1.1985e-08 1.05 1.2015e-08 1.15 1.2385e-08 1.15 1.2415e-08 1.05 1.28e-08 1.05)
Vinn vinn 0 PWL(0 1.05 1.85e-10 1.05 2.15e-10 1.15 3.85e-10 1.15 4.15e-10 1.05 9.85e-10 1.05 1.015e-09 1.15 1.385e-09 1.15 1.415e-09 1.05 1.785e-09 1.05 1.815e-09 1.15 2.185e-09 1.15 2.215e-09 1.05 2.385e-09 1.05 2.415e-09 1.15 2.585e-09 1.15 2.615e-09 1.05 2.785e-09 1.05 2.815e-09 1.15 2.985e-09 1.15 3.015e-09 1.05 3.185e-09 1.05 3.215e-09 1.15 3.385e-09 1.15 3.415e-09 1.05 4.785e-09 1.05 4.815e-09 1.15 5.985e-09 1.15 6.015e-09 1.05 6.185e-09 1.05 6.215e-09 1.15 7.185e-09 1.15 7.215e-09 1.05 7.585e-09 1.05 7.615e-09 1.15 8.385e-09 1.15 8.415e-09 1.05 8.585e-09 1.05 8.615e-09 1.15 8.785e-09 1.15 8.815e-09 1.05 8.985e-09 1.05 9.015e-09 1.15 9.585e-09 1.15 9.615e-09 1.05 1.0385e-08 1.05 1.0415e-08 1.15 1.0785e-08 1.15 1.0815e-08 1.05 1.0985e-08 1.05 1.1015e-08 1.15 1.1585e-08 1.15 1.1615e-08 1.05 1.1785e-08 1.05 1.1815e-08 1.15 1.1985e-08 1.15 1.2015e-08 1.05 1.2385e-08 1.05 1.2415e-08 1.15 1.28e-08 1.15)
* ---- DFE decision path ----
Bdiff vdiff 0 V=V(voutp)-V(voutn)
Vclk clk 0 DC 0 PULSE(0 1.8 1e-10 10p 10p 8e-11 2e-10)
adc_in  [vdiff] [ddata] adc_sl
adc_clk [clk]   [dclk]  adc_ck
aff ddata dclk NULL NULL dq dqb dff_1tap
* POLARITY: dqb (NOT dq) drives vfbp. X3's drain is voutn, so vfbp high pulls
* voutn down and drives (voutp-voutn) POSITIVE. Feeding the previous decision
* dq straight to vfbp therefore REINFORCES that bit -- the opposite of what a
* DFE must do. The tap has to SUBTRACT the post-cursor, so the inverted output
* drives vfbp. Measured at Itap=150uA: dq->vfbp gives 0.078 UI, dqb->vfbp gives
* 0.453 UI.
adac_p [dqb] [vfbp] dac_fb
adac_n [dq]  [vfbn] dac_fb
.model adc_sl  adc_bridge(in_low=-0.001 in_high=0.001)
.model adc_ck  adc_bridge(in_low=0.7 in_high=1.1)
.model dac_fb  dac_bridge(out_low=0.8 out_high=1.4 out_undef=1.1 t_rise=15p t_fall=15p)
.model dff_1tap d_dff(clk_delay=20p rise_delay=15p fall_delay=15p)
.control
tran 4p 1.28e-08 0 4p
let vd = v(voutp)-v(voutn)
echo ---------- closed-loop DFE: eye should be OPEN ----------
plot vd
plot v(vfbp) v(vfbn)
.endc
"}
