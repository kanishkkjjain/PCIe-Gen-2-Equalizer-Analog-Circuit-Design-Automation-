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
* Connectivity is by NET LABEL, not by long wires: a wire that misses a pin by
* one grid unit netlists a floating node that still LOOKS correct on screen.
* Every pin below carries a label placed on its exact coordinate.
}
G {}
K {}
V {}
S {}
E {}
T {PCIe Gen2 EQUALIZER -- CTLE + 1-tap DFE summing stage
sized automatically by the RL framework; values shown are defaults} 300 -920 0 0 0.6 0.6 {}
C {sky130_fd_pr/res_high_po_1p41.sym} 320 -740 0 0 {name=RLn L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 720 -740 0 0 {name=RLp L=3.875 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -640 0 0 {name=1
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -640 0 0 {name=2
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
N 320 -710 320 -670 { lab=outn_ctle}
N 720 -710 720 -670 { lab=outp_ctle}
N 320 -820 320 -770 { lab=vdd}
N 720 -820 720 -770 { lab=vdd}
C {devices/capa.sym} 180 -640 0 0 {name=CLn value=40e-15 m=1}
C {devices/capa.sym} 860 -640 0 0 {name=CLp value=40e-15 m=1}
C {sky130_fd_pr/res_high_po_1p41.sym} 480 -560 0 0 {name=Rs L=5.218 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/cap_mim_m3_1.sym} 600 -560 0 0 {name=Cs W=12.06 L=12.06 MF=1 model=cap_mim_m3_1 spiceprefix=X}
N 320 -610 320 -590 { lab=sp}
N 320 -590 480 -590 { lab=sp}
N 480 -590 600 -590 { lab=sp}
N 720 -610 720 -530 { lab=sn}
N 480 -530 600 -530 { lab=sn}
N 600 -530 720 -530 { lab=sn}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -400 0 0 {name=t1
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -400 0 0 {name=t2
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
N 320 -590 320 -430 { lab=sp}
N 720 -530 720 -430 { lab=sn}
T {STAGE 1 - CTLE, source-degenerated pair} 300 -860 0 0 0.4 0.4 {layer=4}
T {degeneration\nRs || Cs  ->  zero at 1/(Rs*Cs)} 430 -600 0 0 0.3 0.3 {layer=4}
T {CTLE tail, 4:1 mirror (m=4)} 250 -330 0 0 0.3 0.3 {layer=4}
C {devices/isource.sym} 980 -740 0 0 {name=Iref value=150u}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 960 -400 0 0 {name=ref
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
N 980 -820 980 -770 { lab=vdd}
N 980 -710 980 -430 { lab=vbias}
T {4:1 bias mirror} 880 -330 0 0 0.3 0.3 {layer=4}
C {sky130_fd_pr/res_high_po_1p41.sym} 1200 -740 0 0 {name=Rsn L=2.085 mult=1 model=res_high_po_1p41 spiceprefix=X}
C {sky130_fd_pr/res_high_po_1p41.sym} 1600 -740 0 0 {name=Rsp L=2.085 mult=1 model=res_high_po_1p41 spiceprefix=X}
N 1200 -820 1200 -770 { lab=vdd}
N 1600 -820 1600 -770 { lab=vdd}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1180 -640 0 0 {name=G1p
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1580 -640 0 0 {name=G1n
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
N 1200 -710 1200 -670 { lab=outn_dfe}
N 1600 -710 1600 -670 { lab=outp_dfe}
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1300 -540 0 0 {name=GFp
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1480 -540 0 0 {name=GFn
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1380 -400 0 0 {name=Tsum1
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
C {sky130_fd_pr/nfet_01v8_lvt.sym} 1660 -400 0 0 {name=Tsumf
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
T {STAGE 2 - DFE summing stage (own loads Rsum)} 1150 -860 0 0 0.4 0.4 {layer=4}
T {Gm1 main path (gates <- CTLE out, drains crossed)} 1120 -470 0 0 0.3 0.3 {layer=4}
T {Gm1 tail} 1270 -330 0 0 0.3 0.3 {layer=4}
T {GmF tail} 1600 -330 0 0 0.3 0.3 {layer=4}
N 300 -820 1700 -820 { lab=vdd}
C {devices/lab_pin.sym} 320 -770 0 0 {name=l1 lab=vdd}
C {devices/lab_pin.sym} 320 -710 0 0 {name=l2 lab=outn_ctle}
C {devices/gnd.sym} 300 -740 0 0 {name=g3 lab=GND}
C {devices/lab_pin.sym} 720 -770 0 0 {name=l4 lab=vdd}
C {devices/lab_pin.sym} 720 -710 0 0 {name=l5 lab=outp_ctle}
C {devices/gnd.sym} 700 -740 0 0 {name=g6 lab=GND}
C {devices/lab_pin.sym} 320 -670 0 0 {name=l7 lab=outn_ctle}
C {devices/lab_pin.sym} 280 -640 0 0 {name=l8 lab=vinp}
C {devices/lab_pin.sym} 320 -610 0 0 {name=l9 lab=sp}
C {devices/gnd.sym} 320 -640 0 0 {name=g10 lab=GND}
C {devices/lab_pin.sym} 720 -670 0 0 {name=l11 lab=outp_ctle}
C {devices/lab_pin.sym} 680 -640 0 0 {name=l12 lab=vinn}
C {devices/lab_pin.sym} 720 -610 0 0 {name=l13 lab=sn}
C {devices/gnd.sym} 720 -640 0 0 {name=g14 lab=GND}
C {devices/lab_pin.sym} 180 -670 0 0 {name=l15 lab=outn_ctle}
C {devices/gnd.sym} 180 -610 0 0 {name=g16 lab=GND}
C {devices/lab_pin.sym} 860 -670 0 0 {name=l17 lab=outp_ctle}
C {devices/gnd.sym} 860 -610 0 0 {name=g18 lab=GND}
C {devices/lab_pin.sym} 480 -590 0 0 {name=l19 lab=sp}
C {devices/lab_pin.sym} 480 -530 0 0 {name=l20 lab=sn}
C {devices/gnd.sym} 460 -560 0 0 {name=g21 lab=GND}
C {devices/lab_pin.sym} 600 -590 0 0 {name=l22 lab=sp}
C {devices/lab_pin.sym} 600 -530 0 0 {name=l23 lab=sn}
C {devices/lab_pin.sym} 320 -430 0 0 {name=l24 lab=sp}
C {devices/lab_pin.sym} 280 -400 0 0 {name=l25 lab=vbias}
C {devices/gnd.sym} 320 -370 0 0 {name=g26 lab=GND}
C {devices/gnd.sym} 320 -400 0 0 {name=g27 lab=GND}
C {devices/lab_pin.sym} 720 -430 0 0 {name=l28 lab=sn}
C {devices/lab_pin.sym} 680 -400 0 0 {name=l29 lab=vbias}
C {devices/gnd.sym} 720 -370 0 0 {name=g30 lab=GND}
C {devices/gnd.sym} 720 -400 0 0 {name=g31 lab=GND}
C {devices/lab_pin.sym} 980 -770 0 0 {name=l32 lab=vdd}
C {devices/lab_pin.sym} 980 -710 0 0 {name=l33 lab=vbias}
C {devices/lab_pin.sym} 980 -430 0 0 {name=l34 lab=vbias}
C {devices/lab_pin.sym} 940 -400 0 0 {name=l35 lab=vbias}
C {devices/gnd.sym} 980 -370 0 0 {name=g36 lab=GND}
C {devices/gnd.sym} 980 -400 0 0 {name=g37 lab=GND}
C {devices/lab_pin.sym} 1200 -770 0 0 {name=l38 lab=vdd}
C {devices/lab_pin.sym} 1200 -710 0 0 {name=l39 lab=outn_dfe}
C {devices/gnd.sym} 1180 -740 0 0 {name=g40 lab=GND}
C {devices/lab_pin.sym} 1600 -770 0 0 {name=l41 lab=vdd}
C {devices/lab_pin.sym} 1600 -710 0 0 {name=l42 lab=outp_dfe}
C {devices/gnd.sym} 1580 -740 0 0 {name=g43 lab=GND}
C {devices/lab_pin.sym} 1200 -670 0 0 {name=l44 lab=outn_dfe}
C {devices/lab_pin.sym} 1160 -640 0 0 {name=l45 lab=outp_ctle}
C {devices/lab_pin.sym} 1200 -610 0 0 {name=l46 lab=sgm1}
C {devices/gnd.sym} 1200 -640 0 0 {name=g47 lab=GND}
C {devices/lab_pin.sym} 1600 -670 0 0 {name=l48 lab=outp_dfe}
C {devices/lab_pin.sym} 1560 -640 0 0 {name=l49 lab=outn_ctle}
C {devices/lab_pin.sym} 1600 -610 0 0 {name=l50 lab=sgm1}
C {devices/gnd.sym} 1600 -640 0 0 {name=g51 lab=GND}
C {devices/lab_pin.sym} 1320 -570 0 0 {name=l52 lab=outn_dfe}
C {devices/lab_pin.sym} 1280 -540 0 0 {name=l53 lab=vfbp}
C {devices/lab_pin.sym} 1320 -510 0 0 {name=l54 lab=sgmf}
C {devices/gnd.sym} 1320 -540 0 0 {name=g55 lab=GND}
C {devices/lab_pin.sym} 1500 -570 0 0 {name=l56 lab=outp_dfe}
C {devices/lab_pin.sym} 1460 -540 0 0 {name=l57 lab=vfbn}
C {devices/lab_pin.sym} 1500 -510 0 0 {name=l58 lab=sgmf}
C {devices/gnd.sym} 1500 -540 0 0 {name=g59 lab=GND}
C {devices/lab_pin.sym} 1400 -430 0 0 {name=l60 lab=sgm1}
C {devices/lab_pin.sym} 1360 -400 0 0 {name=l61 lab=vbias}
C {devices/gnd.sym} 1400 -370 0 0 {name=g62 lab=GND}
C {devices/gnd.sym} 1400 -400 0 0 {name=g63 lab=GND}
C {devices/lab_pin.sym} 1680 -430 0 0 {name=l64 lab=sgmf}
C {devices/lab_pin.sym} 1640 -400 0 0 {name=l65 lab=vbias}
C {devices/gnd.sym} 1680 -370 0 0 {name=g66 lab=GND}
C {devices/gnd.sym} 1680 -400 0 0 {name=g67 lab=GND}
C {devices/vdd.sym} 300 -820 0 0 {name=lvdd lab=vdd}
