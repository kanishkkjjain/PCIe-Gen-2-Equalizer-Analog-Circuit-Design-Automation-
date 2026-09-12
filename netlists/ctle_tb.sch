v {xschem version=3.4.4 file_version=1.2
* PCIe Gen2 CTLE - 1-stage source-degenerated differential pair - SKY130
* Values mirror eqrl/netlist.py::CtleParams defaults exactly:
*   W=20u L=0.18u nf=2 | RL=1200 | Rs=1500 | Cs=300f | CL=40f | Itail=600u
*   Vcm=0.9 VDD=1.8 corner=tt
* Netlist it with Shift-N and diff against CtleParams().core_netlist()
}
G {}
K {}
V {}
S {}
E {}

T {CTLE - source-degenerated differential pair
Zero at 1/(Rs*Cs) gives the HF peaking.
DC gain = gm*RL/(1+gm*Rs/2), HF gain = gm*RL} 160 -640 0 0 0.4 0.4 {}

T {VDD rail} 530 -545 0 0 0.3 0.3 {layer=4}
T {degeneration
Rs || Cs} 500 -230 0 0 0.3 0.3 {layer=4}
T {tail sinks} 480 -175 0 0 0.3 0.3 {layer=4}

* ---------------- VDD rail ----------------
N 320 -530 720 -530 { lab=vdd}

* ---------------- left branch: voutn ----------------
N 320 -470 320 -430 { lab=voutn}
N 180 -430 320 -430 { lab=voutn}

* ---------------- right branch: voutp ----------------
N 720 -470 720 -430 { lab=voutp}
N 720 -430 860 -430 { lab=voutp}

* ---------------- bulk ties ----------------
N 320 -400 360 -400 { lab=GND}
N 720 -400 760 -400 { lab=GND}

* ---------------- gates ----------------
N 240 -400 280 -400 { lab=vinp}
N 640 -400 680 -400 { lab=vinn}

* ---------------- sp rail (M1 source side) ----------------
N 320 -370 320 -340 { lab=sp}
N 320 -340 600 -340 { lab=sp}
N 320 -340 320 -210 { lab=sp}

* ---------------- sn rail (M2 source side) ----------------
N 720 -370 720 -280 { lab=sn}
N 480 -280 720 -280 { lab=sn}
N 720 -280 720 -210 { lab=sn}

* ---------------- grounds ----------------
N 320 -150 320 -120 { lab=GND}
N 720 -150 720 -120 { lab=GND}
N 180 -370 180 -340 { lab=GND}
N 860 -370 860 -340 { lab=GND}

* ================= DEVICES =================
C {sky130_fd_pr/nfet_01v8_lvt.sym} 300 -400 0 0 {name=M1
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

C {sky130_fd_pr/nfet_01v8_lvt.sym} 700 -400 0 0 {name=M2
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

C {devices/res.sym}  320 -500 0 0 {name=RLn value=1200 m=1}
C {devices/res.sym}  720 -500 0 0 {name=RLp value=1200 m=1}
C {devices/res.sym}  480 -310 0 0 {name=Rs  value=1500 m=1}
C {devices/capa.sym} 600 -310 0 0 {name=Cs  value=300e-15 m=1}
C {devices/capa.sym} 180 -400 0 0 {name=CLn value=40e-15 m=1}
C {devices/capa.sym} 860 -400 0 0 {name=CLp value=40e-15 m=1}

C {devices/isource.sym} 320 -180 0 0 {name=It1 value=600u}
C {devices/isource.sym} 720 -180 0 0 {name=It2 value=600u}

* ================= SUPPLIES / STIMULUS =================
C {devices/vdd.sym} 520 -530 0 0 {name=l1 lab=vdd}

C {devices/vsource.sym} 980 -460 0 0 {name=Vdd value=1.8 only_toplevel=true}
C {devices/vdd.sym}     980 -490 0 0 {name=l2 lab=vdd}
C {devices/gnd.sym}     980 -430 0 0 {name=l3 lab=GND}

C {devices/vsource.sym} 100 -300 0 0 {name=Vinp value="DC 0.9 AC 0.5 0" only_toplevel=true}
C {devices/lab_pin.sym} 100 -330 0 0 {name=p1 sig_type=std_logic lab=vinp}
C {devices/gnd.sym}     100 -270 0 0 {name=l4 lab=GND}

C {devices/vsource.sym} 100 -160 0 0 {name=Vinn value="DC 0.9 AC 0.5 180" only_toplevel=true}
C {devices/lab_pin.sym} 100 -190 0 0 {name=p2 sig_type=std_logic lab=vinn}
C {devices/gnd.sym}     100 -130 0 0 {name=l5 lab=GND}

* ================= NET LABELS =================
C {devices/lab_pin.sym} 240 -400 0 0 {name=p3 sig_type=std_logic lab=vinp}
C {devices/lab_pin.sym} 640 -400 0 0 {name=p4 sig_type=std_logic lab=vinn}
C {devices/lab_pin.sym} 180 -430 0 0 {name=p5 sig_type=std_logic lab=voutn}
C {devices/lab_pin.sym} 860 -430 0 0 {name=p6 sig_type=std_logic lab=voutp}
C {devices/lab_pin.sym} 400 -340 0 0 {name=p7 sig_type=std_logic lab=sp}
C {devices/lab_pin.sym} 640 -280 0 0 {name=p8 sig_type=std_logic lab=sn}

* ================= GROUNDS =================
C {devices/gnd.sym} 360 -400 0 0 {name=l6 lab=GND}
C {devices/gnd.sym} 760 -400 0 0 {name=l7 lab=GND}
C {devices/gnd.sym} 320 -120 0 0 {name=l8 lab=GND}
C {devices/gnd.sym} 720 -120 0 0 {name=l9 lab=GND}
C {devices/gnd.sym} 180 -340 0 0 {name=l10 lab=GND}
C {devices/gnd.sym} 860 -340 0 0 {name=l11 lab=GND}

* ================= PDK CORNER =================
C {sky130_fd_pr/corner.sym} 1080 -600 0 0 {name=CORNER only_toplevel=true corner=tt}

* ================= SIMULATION COMMANDS =================
C {devices/code.sym} 1080 -300 0 0 {name=STIMULI
only_toplevel=true
place=end
value=".control
save all
op
echo ---------- BIAS (want vds > vdsat) ----------
print @m.xm1.msky130_fd_pr__nfet_01v8_lvt[id]
print @m.xm1.msky130_fd_pr__nfet_01v8_lvt[gm]
print @m.xm1.msky130_fd_pr__nfet_01v8_lvt[vds]
print @m.xm1.msky130_fd_pr__nfet_01v8_lvt[vdsat]
ac dec 40 1e6 20e9
let g = vdb(voutp,voutn)
echo ---------- AC (expect peaking ~10.1 dB) ----------
print g[0]
print maximum(g)
plot g
.endc
"}
