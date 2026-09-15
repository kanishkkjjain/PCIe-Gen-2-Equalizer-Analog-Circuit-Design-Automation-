v {xschem version=3.4.4 file_version=1.2
* PCIe Gen2 EQUALIZER -- the circuit the RL framework sizes.
*
* CTLE (source-degenerated diff pair) -> DFE SUMMING STAGE (Gm1 + GmF on their
* OWN load pair Rsum). The two stages have SEPARATE loads. The older
* eq_tb/eq_dfe_tb/ctle_tb schematics in this directory draw a merged summer in
* which the tap pair shared the CTLE's RLp/RLn, and are stale.
*
* CONNECTIVITY IS THE POINT OF THIS FILE. It is checked device-for-device
* against eqrl/netlist.py::CtleParams.full_netlist() by
* tests/test_schematic_sch.py, which netlists this schematic with xschem and
* diffs the result. Values here are the CtleParams() defaults; the sizing a
* given design actually used is in that design's report and netlist, not here.
*
* Wires carry the connections inside each stage. Net labels are used only where
* a wire would have to cross the drawing: the shared tail nodes (sgm1, sgmf),
* the bias rail, and the signals passing between the two stages.
}
G {}
K {}
V {}
S {}
E {}
T {PCIe Gen2 equalizer: 1-stage CTLE + 1-tap DFE summing stage} 300 -780 0 0 0.6 0.6 {}
T {sized automatically by the RL framework -- values shown are the defaults} 300 -745 0 0 0.35 0.35 {layer=4}
C {sky130_fd_pr/res_high_po_1p41.sym} 320 -600 0 0 {name=RLn L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 720 -600 0 0 {name=RLp L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -460 0 0 {name=1
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -460 0 0 {name=2
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
N 320 -700 320 -630 {}
N 320 -570 320 -490 {}
N 720 -700 720 -630 {}
N 720 -570 720 -490 {}
N 190 -460 280 -460 {}
N 590 -460 680 -460 {}
C {devices/capa.sym} 100 -460 0 0 {name=CLn value=40e-15 m=1}
C {devices/capa.sym} 880 -460 0 0 {name=CLp value=40e-15 m=1}
N 100 -490 320 -490 {}
N 880 -490 720 -490 {}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -260 0 0 {name=t1
L=0.5
W=15
nf=2
mult=4
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -260 0 0 {name=t2
L=0.5
W=15
nf=2
mult=4
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
N 320 -430 320 -290 {}
N 720 -430 720 -290 {}
C {sky130_fd_pr/res_high_po_1p41.sym} 450 -370 0 0 {name=Rs L=5.218 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/cap_mim_m3_1.sym} 570 -370 0 0 {name=Cs W=12.06 L=12.06 MF=1 model=cap_mim_m3_1 spiceprefix=X}
N 320 -400 450 -400 {}
N 450 -400 570 -400 {}
N 450 -340 570 -340 {}
N 570 -340 720 -340 {}
T {STAGE 1 - CTLE, source-degenerated differential pair} 200 -660 0 0 0.45 0.45 {layer=4}
T {Rs || Cs sets the CTLE zero} 330 -330 0 0 0.35 0.35 {layer=4}
T {tail: 4:1 current mirror (m=4)} 180 -195 0 0 0.35 0.35 {layer=4}
C {devices/isource.sym} 950 -600 0 0 {name=Iref value=150u}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 930 -260 0 0 {name=ref
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
N 950 -700 950 -630 {}
N 950 -570 950 -290 {}
N 950 -450 1030 -450 {}
N 1030 -450 1030 -150 {}
T {4:1 bias mirror} 880 -195 0 0 0.35 0.35 {layer=4}
C {sky130_fd_pr/res_high_po_1p41.sym} 1170 -600 0 0 {name=Rsn L=2.085 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 1970 -600 0 0 {name=Rsp L=2.085 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1150 -460 0 0 {name=G1p
L=0.18
W=40
nf=4
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1950 -460 0 0 {name=G1n
L=0.18
W=40
nf=4
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
N 1170 -700 1170 -630 {}
N 1170 -570 1170 -490 {}
N 1970 -700 1970 -630 {}
N 1970 -570 1970 -490 {}
N 1040 -460 1130 -460 {}
N 1840 -460 1930 -460 {}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1420 -460 0 0 {name=GFp
L=0.18
W=2
nf=1
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1680 -460 0 0 {name=GFn
L=0.18
W=2
nf=1
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
N 1170 -490 1440 -490 {}
N 1700 -490 1970 -490 {}
N 1310 -460 1400 -460 {}
N 1570 -460 1660 -460 {}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1550 -260 0 0 {name=Tsum1
L=0.5
W=100
nf=10
mult=2
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 2050 -260 0 0 {name=Tsumf
L=0.5
W=5
nf=1
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
T {STAGE 2 - DFE summing stage, its own load pair Rsum} 1160 -660 0 0 0.45 0.45 {layer=4}
T {Gm1: main path, gates from the CTLE output, drains crossed} 1180 -335 0 0 0.35 0.35 {layer=4}
T {Gm1 tail} 1450 -195 0 0 0.35 0.35 {layer=4}
T {GmF tail (tap)} 1930 -195 0 0 0.35 0.35 {layer=4}
N 300 -700 1970 -700 {}
N 190 -150 2070 -150 {}
N 280 -260 280 -150 {}
N 680 -260 680 -150 {}
N 910 -260 910 -150 {}
N 1530 -260 1530 -150 {}
N 2030 -260 2030 -150 {}
C {devices/gnd.sym} 300 -600 0 0 {name=g1 lab=GND}
C {devices/gnd.sym} 700 -600 0 0 {name=g2 lab=GND}
C {devices/gnd.sym} 320 -460 0 0 {name=g3 lab=GND}
C {devices/gnd.sym} 720 -460 0 0 {name=g4 lab=GND}
C {devices/lab_pin.sym} 190 -460 0 0 {name=l5 lab=vinp}
C {devices/lab_pin.sym} 590 -460 0 0 {name=l6 lab=vinn}
C {devices/lab_pin.sym} 320 -490 0 0 {name=l7 lab=outn_ctle}
C {devices/lab_pin.sym} 720 -490 0 0 {name=l8 lab=outp_ctle}
C {devices/gnd.sym} 100 -430 0 0 {name=g9 lab=GND}
C {devices/gnd.sym} 880 -430 0 0 {name=g10 lab=GND}
C {devices/gnd.sym} 320 -260 0 0 {name=g11 lab=GND}
C {devices/gnd.sym} 720 -260 0 0 {name=g12 lab=GND}
C {devices/gnd.sym} 320 -230 0 0 {name=g13 lab=GND}
C {devices/gnd.sym} 720 -230 0 0 {name=g14 lab=GND}
C {devices/gnd.sym} 430 -370 0 0 {name=g15 lab=GND}
C {devices/lab_pin.sym} 320 -400 0 0 {name=l16 lab=sp}
C {devices/lab_pin.sym} 720 -340 0 0 {name=l17 lab=sn}
C {devices/gnd.sym} 950 -260 0 0 {name=g18 lab=GND}
C {devices/gnd.sym} 950 -230 0 0 {name=g19 lab=GND}
C {devices/lab_pin.sym} 950 -450 0 0 {name=l20 lab=vbias}
C {devices/gnd.sym} 1150 -600 0 0 {name=g21 lab=GND}
C {devices/gnd.sym} 1950 -600 0 0 {name=g22 lab=GND}
C {devices/gnd.sym} 1170 -460 0 0 {name=g23 lab=GND}
C {devices/gnd.sym} 1970 -460 0 0 {name=g24 lab=GND}
C {devices/lab_pin.sym} 1040 -460 0 0 {name=l25 lab=outp_ctle}
C {devices/lab_pin.sym} 1840 -460 0 0 {name=l26 lab=outn_ctle}
C {devices/lab_pin.sym} 1170 -490 0 0 {name=l27 lab=outn_dfe}
C {devices/lab_pin.sym} 1970 -490 0 0 {name=l28 lab=outp_dfe}
C {devices/lab_pin.sym} 1170 -430 0 0 {name=l29 lab=sgm1}
C {devices/lab_pin.sym} 1970 -430 0 0 {name=l30 lab=sgm1}
C {devices/gnd.sym} 1440 -460 0 0 {name=g31 lab=GND}
C {devices/gnd.sym} 1700 -460 0 0 {name=g32 lab=GND}
C {devices/lab_pin.sym} 1310 -460 0 0 {name=l33 lab=vfbp}
C {devices/lab_pin.sym} 1570 -460 0 0 {name=l34 lab=vfbn}
C {devices/lab_pin.sym} 1440 -430 0 0 {name=l35 lab=sgmf}
C {devices/lab_pin.sym} 1700 -430 0 0 {name=l36 lab=sgmf}
C {devices/gnd.sym} 1570 -260 0 0 {name=g37 lab=GND}
C {devices/gnd.sym} 2070 -260 0 0 {name=g38 lab=GND}
C {devices/gnd.sym} 1570 -230 0 0 {name=g39 lab=GND}
C {devices/gnd.sym} 2070 -230 0 0 {name=g40 lab=GND}
C {devices/lab_pin.sym} 1570 -290 0 0 {name=l41 lab=sgm1}
C {devices/lab_pin.sym} 2070 -290 0 0 {name=l42 lab=sgmf}
C {devices/vdd.sym} 300 -700 0 0 {name=lvdd lab=vdd}
C {devices/lab_pin.sym} 600 -150 0 0 {name=l43 lab=vbias}
