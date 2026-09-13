* ==================================================================
* PCIe Gen2 equalizer (CTLE + 1-tap DFE summer) -- auto-sized by RL (CEM)
* forward path, DFE loop OPEN
* Requested (W/L in um, R in ohm, C in F, I in A):
*   W=24.27  L=0.2  RL=782.875  Rs=2208.7
*   Cs=2.5779e-13  Itail=0.000635023  Wtail=97.93
*   Rsum=1732.91  Wg1=42.94  Ig1=0.000407005
*   tap_pct=1.379%  -> Wgf=0.5922 Igf=5.61272e-06
*   CL=4e-14  Vcm=1.1
* Realised geometry:
*   RL -> l=2.008 m=1 (782.875 ohm)
*   Rs -> l=8.39 m=1 (2208.7 ohm)
*   Cs -> 11.16 x 11.16 um (2.5779e-13 F)
*   Rsum -> l=6.261 m=1 (1732.91 ohm)
*   Iref=0.000158756  Wref=24.48
*   Gm1 tail W=62.77   GmF tail W=0.8656
*   nominal tap weight gmF/gm1 = 0.01379
* corner: tt_1.800V_27C
* ==================================================================
.lib "/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.temp 27
.param Vcm=1.1

Vdd vdd 0 DC 1.8
Vinp vinp 0 DC {Vcm} AC 0.5 0
Vinn vinn 0 DC {Vcm} AC 0.5 180
Vfbp vfbp 0 DC 1.1
Vfbn vfbn 0 DC 1.1

* --- STAGE 1: CTLE, source-degenerated diff pair, real SKY130 devices ---
* input pair
X1 outn_ctle vinp sp 0 sky130_fd_pr__nfet_01v8_lvt L=0.2 W=24.27 nf=2 m=1
X2 outp_ctle vinn sn 0 sky130_fd_pr__nfet_01v8_lvt L=0.2 W=24.27 nf=2 m=1
* degeneration: real poly resistor + real MIM cap  (b = body tie to substrate)
XRs sp sn 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=8.39 m=1
XCs sp sn sky130_fd_pr__cap_mim_m3_1 w=11.16 l=11.16 mf=1
* loads
XRLp vdd outp_ctle 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=2.008 m=1
XRLn vdd outn_ctle 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=2.008 m=1
* CL stays ideal: it models the NEXT stage's input capacitance
CLp outp_ctle 0 4e-14
CLn outn_ctle 0 4e-14
* tail: 4:1 NMOS current mirror (Iref sourced from vdd).
* Xt1/Xt2 are the SAME unit device as Xref, replicated m=4 times --
* a mirror ratios by multiplicity, not by making one device wider.
Iref vdd vbias DC 0.000158756
Xref vbias vbias 0 0 sky130_fd_pr__nfet_01v8_lvt L=0.5 W=24.48 nf=2 m=1
Xt1  sp vbias 0 0 sky130_fd_pr__nfet_01v8_lvt L=0.5 W=24.48 nf=2 m=4
Xt2  sn vbias 0 0 sky130_fd_pr__nfet_01v8_lvt L=0.5 W=24.48 nf=2 m=4
* --- STAGE 2: DFE summing stage (Gm1 + GmF share one load pair) ---
* summing-node loads -- these ARE the summer
XRsp vdd outp_dfe 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=6.261 m=1
XRsn vdd outn_dfe 0 sky130_fd_pr__res_high_po_1p41 w=1.41 l=6.261 m=1
* Gm1: main signal path, gates driven by the CTLE output (drains crossed)
XG1p outn_dfe outp_ctle sgm1 0 sky130_fd_pr__nfet_01v8_lvt L=0.18 W=42.94 nf=4 m=1
XG1n outp_dfe outn_ctle sgm1 0 sky130_fd_pr__nfet_01v8_lvt L=0.18 W=42.94 nf=4 m=1
* GmF: feedback tap, gates driven by the held decision (same crossing as Gm1)
XGFp outn_dfe vfbp sgmf 0 sky130_fd_pr__nfet_01v8_lvt L=0.18 W=0.5922 nf=1 m=1
XGFn outp_dfe vfbn sgmf 0 sky130_fd_pr__nfet_01v8_lvt L=0.18 W=0.5922 nf=1 m=1
* separate tails, both mirrored off the same vbias as the CTLE.
* Names must not collide with the CTLE's Xt1/Xt2 even case-insensitively --
* ngspice folds case, so `XT1` IS `Xt1` and the deck dies with
* "device already exists, bail out".
XTsum1 sgm1 vbias 0 0 sky130_fd_pr__nfet_01v8_lvt L=0.5 W=62.77 nf=6 m=1
XTsumf sgmf vbias 0 0 sky130_fd_pr__nfet_01v8_lvt L=0.5 W=0.8656 nf=1 m=1

.control
ac dec 40 1e6 20e9
wrdata ctle_ac.dat vdb(outp_ctle,outn_ctle) vdb(outp_dfe,outn_dfe)
.endc
.end
