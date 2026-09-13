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
* channel: PRESENT (lossy FR4-like, see netlist.channel_netlist)
* ==================================================================
* auto-generated CTLE deck  corner=tt_1.800V_27C
.lib "/home/kanishk/.volare/volare/sky130/versions/c6d73a35f524070e85faff4a6a9eef49553ebc2b/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.temp 27
.param Vcm={Vcm}

.options abstol=1e-10 vntol=1e-6

.param Vcm=1.1
Vdd vdd 0 DC 1.8
Vinp chsrc_p 0 DC 1.1 AC 0.5 0
Vinn chsrc_n 0 DC 1.1 AC 0.5 180
* --- lossy channel: 40 sections/leg, 1200 ps, skin-effect series R ---
Rsrc_p chsrc_p chp_in 50
Rsrc_n chsrc_n chn_in 50
Rchp0dc chp_in p0x 0.02
Rchp0s0 p0x p0k0 4
Lchp0s0 p0x p0k0 4.5e-10
Rchp0s1 p0k0 p0k1 12
Lchp0s1 p0k0 p0k1 6e-11
Lchp0m p0k1 chp0 1.5e-09
Cchp0 chp0 0 6e-13
Rchp1dc chp0 p1x 0.02
Rchp1s0 p1x p1k0 4
Lchp1s0 p1x p1k0 4.5e-10
Rchp1s1 p1k0 p1k1 12
Lchp1s1 p1k0 p1k1 6e-11
Lchp1m p1k1 chp1 1.5e-09
Cchp1 chp1 0 6e-13
Rchp2dc chp1 p2x 0.02
Rchp2s0 p2x p2k0 4
Lchp2s0 p2x p2k0 4.5e-10
Rchp2s1 p2k0 p2k1 12
Lchp2s1 p2k0 p2k1 6e-11
Lchp2m p2k1 chp2 1.5e-09
Cchp2 chp2 0 6e-13
Rchp3dc chp2 p3x 0.02
Rchp3s0 p3x p3k0 4
Lchp3s0 p3x p3k0 4.5e-10
Rchp3s1 p3k0 p3k1 12
Lchp3s1 p3k0 p3k1 6e-11
Lchp3m p3k1 chp3 1.5e-09
Cchp3 chp3 0 6e-13
Rchp4dc chp3 p4x 0.02
Rchp4s0 p4x p4k0 4
Lchp4s0 p4x p4k0 4.5e-10
Rchp4s1 p4k0 p4k1 12
Lchp4s1 p4k0 p4k1 6e-11
Lchp4m p4k1 chp4 1.5e-09
Cchp4 chp4 0 6e-13
Rchp5dc chp4 p5x 0.02
Rchp5s0 p5x p5k0 4
Lchp5s0 p5x p5k0 4.5e-10
Rchp5s1 p5k0 p5k1 12
Lchp5s1 p5k0 p5k1 6e-11
Lchp5m p5k1 chp5 1.5e-09
Cchp5 chp5 0 6e-13
Rchp6dc chp5 p6x 0.02
Rchp6s0 p6x p6k0 4
Lchp6s0 p6x p6k0 4.5e-10
Rchp6s1 p6k0 p6k1 12
Lchp6s1 p6k0 p6k1 6e-11
Lchp6m p6k1 chp6 1.5e-09
Cchp6 chp6 0 6e-13
Rchp7dc chp6 p7x 0.02
Rchp7s0 p7x p7k0 4
Lchp7s0 p7x p7k0 4.5e-10
Rchp7s1 p7k0 p7k1 12
Lchp7s1 p7k0 p7k1 6e-11
Lchp7m p7k1 chp7 1.5e-09
Cchp7 chp7 0 6e-13
Rchp8dc chp7 p8x 0.02
Rchp8s0 p8x p8k0 4
Lchp8s0 p8x p8k0 4.5e-10
Rchp8s1 p8k0 p8k1 12
Lchp8s1 p8k0 p8k1 6e-11
Lchp8m p8k1 chp8 1.5e-09
Cchp8 chp8 0 6e-13
Rchp9dc chp8 p9x 0.02
Rchp9s0 p9x p9k0 4
Lchp9s0 p9x p9k0 4.5e-10
Rchp9s1 p9k0 p9k1 12
Lchp9s1 p9k0 p9k1 6e-11
Lchp9m p9k1 chp9 1.5e-09
Cchp9 chp9 0 6e-13
Rchp10dc chp9 p10x 0.02
Rchp10s0 p10x p10k0 4
Lchp10s0 p10x p10k0 4.5e-10
Rchp10s1 p10k0 p10k1 12
Lchp10s1 p10k0 p10k1 6e-11
Lchp10m p10k1 chp10 1.5e-09
Cchp10 chp10 0 6e-13
Rchp11dc chp10 p11x 0.02
Rchp11s0 p11x p11k0 4
Lchp11s0 p11x p11k0 4.5e-10
Rchp11s1 p11k0 p11k1 12
Lchp11s1 p11k0 p11k1 6e-11
Lchp11m p11k1 chp11 1.5e-09
Cchp11 chp11 0 6e-13
Rchp12dc chp11 p12x 0.02
Rchp12s0 p12x p12k0 4
Lchp12s0 p12x p12k0 4.5e-10
Rchp12s1 p12k0 p12k1 12
Lchp12s1 p12k0 p12k1 6e-11
Lchp12m p12k1 chp12 1.5e-09
Cchp12 chp12 0 6e-13
Rchp13dc chp12 p13x 0.02
Rchp13s0 p13x p13k0 4
Lchp13s0 p13x p13k0 4.5e-10
Rchp13s1 p13k0 p13k1 12
Lchp13s1 p13k0 p13k1 6e-11
Lchp13m p13k1 chp13 1.5e-09
Cchp13 chp13 0 6e-13
Rchp14dc chp13 p14x 0.02
Rchp14s0 p14x p14k0 4
Lchp14s0 p14x p14k0 4.5e-10
Rchp14s1 p14k0 p14k1 12
Lchp14s1 p14k0 p14k1 6e-11
Lchp14m p14k1 chp14 1.5e-09
Cchp14 chp14 0 6e-13
Rchp15dc chp14 p15x 0.02
Rchp15s0 p15x p15k0 4
Lchp15s0 p15x p15k0 4.5e-10
Rchp15s1 p15k0 p15k1 12
Lchp15s1 p15k0 p15k1 6e-11
Lchp15m p15k1 chp15 1.5e-09
Cchp15 chp15 0 6e-13
Rchp16dc chp15 p16x 0.02
Rchp16s0 p16x p16k0 4
Lchp16s0 p16x p16k0 4.5e-10
Rchp16s1 p16k0 p16k1 12
Lchp16s1 p16k0 p16k1 6e-11
Lchp16m p16k1 chp16 1.5e-09
Cchp16 chp16 0 6e-13
Rchp17dc chp16 p17x 0.02
Rchp17s0 p17x p17k0 4
Lchp17s0 p17x p17k0 4.5e-10
Rchp17s1 p17k0 p17k1 12
Lchp17s1 p17k0 p17k1 6e-11
Lchp17m p17k1 chp17 1.5e-09
Cchp17 chp17 0 6e-13
Rchp18dc chp17 p18x 0.02
Rchp18s0 p18x p18k0 4
Lchp18s0 p18x p18k0 4.5e-10
Rchp18s1 p18k0 p18k1 12
Lchp18s1 p18k0 p18k1 6e-11
Lchp18m p18k1 chp18 1.5e-09
Cchp18 chp18 0 6e-13
Rchp19dc chp18 p19x 0.02
Rchp19s0 p19x p19k0 4
Lchp19s0 p19x p19k0 4.5e-10
Rchp19s1 p19k0 p19k1 12
Lchp19s1 p19k0 p19k1 6e-11
Lchp19m p19k1 chp19 1.5e-09
Cchp19 chp19 0 6e-13
Rchp20dc chp19 p20x 0.02
Rchp20s0 p20x p20k0 4
Lchp20s0 p20x p20k0 4.5e-10
Rchp20s1 p20k0 p20k1 12
Lchp20s1 p20k0 p20k1 6e-11
Lchp20m p20k1 chp20 1.5e-09
Cchp20 chp20 0 6e-13
Rchp21dc chp20 p21x 0.02
Rchp21s0 p21x p21k0 4
Lchp21s0 p21x p21k0 4.5e-10
Rchp21s1 p21k0 p21k1 12
Lchp21s1 p21k0 p21k1 6e-11
Lchp21m p21k1 chp21 1.5e-09
Cchp21 chp21 0 6e-13
Rchp22dc chp21 p22x 0.02
Rchp22s0 p22x p22k0 4
Lchp22s0 p22x p22k0 4.5e-10
Rchp22s1 p22k0 p22k1 12
Lchp22s1 p22k0 p22k1 6e-11
Lchp22m p22k1 chp22 1.5e-09
Cchp22 chp22 0 6e-13
Rchp23dc chp22 p23x 0.02
Rchp23s0 p23x p23k0 4
Lchp23s0 p23x p23k0 4.5e-10
Rchp23s1 p23k0 p23k1 12
Lchp23s1 p23k0 p23k1 6e-11
Lchp23m p23k1 chp23 1.5e-09
Cchp23 chp23 0 6e-13
Rchp24dc chp23 p24x 0.02
Rchp24s0 p24x p24k0 4
Lchp24s0 p24x p24k0 4.5e-10
Rchp24s1 p24k0 p24k1 12
Lchp24s1 p24k0 p24k1 6e-11
Lchp24m p24k1 chp24 1.5e-09
Cchp24 chp24 0 6e-13
Rchp25dc chp24 p25x 0.02
Rchp25s0 p25x p25k0 4
Lchp25s0 p25x p25k0 4.5e-10
Rchp25s1 p25k0 p25k1 12
Lchp25s1 p25k0 p25k1 6e-11
Lchp25m p25k1 chp25 1.5e-09
Cchp25 chp25 0 6e-13
Rchp26dc chp25 p26x 0.02
Rchp26s0 p26x p26k0 4
Lchp26s0 p26x p26k0 4.5e-10
Rchp26s1 p26k0 p26k1 12
Lchp26s1 p26k0 p26k1 6e-11
Lchp26m p26k1 chp26 1.5e-09
Cchp26 chp26 0 6e-13
Rchp27dc chp26 p27x 0.02
Rchp27s0 p27x p27k0 4
Lchp27s0 p27x p27k0 4.5e-10
Rchp27s1 p27k0 p27k1 12
Lchp27s1 p27k0 p27k1 6e-11
Lchp27m p27k1 chp27 1.5e-09
Cchp27 chp27 0 6e-13
Rchp28dc chp27 p28x 0.02
Rchp28s0 p28x p28k0 4
Lchp28s0 p28x p28k0 4.5e-10
Rchp28s1 p28k0 p28k1 12
Lchp28s1 p28k0 p28k1 6e-11
Lchp28m p28k1 chp28 1.5e-09
Cchp28 chp28 0 6e-13
Rchp29dc chp28 p29x 0.02
Rchp29s0 p29x p29k0 4
Lchp29s0 p29x p29k0 4.5e-10
Rchp29s1 p29k0 p29k1 12
Lchp29s1 p29k0 p29k1 6e-11
Lchp29m p29k1 chp29 1.5e-09
Cchp29 chp29 0 6e-13
Rchp30dc chp29 p30x 0.02
Rchp30s0 p30x p30k0 4
Lchp30s0 p30x p30k0 4.5e-10
Rchp30s1 p30k0 p30k1 12
Lchp30s1 p30k0 p30k1 6e-11
Lchp30m p30k1 chp30 1.5e-09
Cchp30 chp30 0 6e-13
Rchp31dc chp30 p31x 0.02
Rchp31s0 p31x p31k0 4
Lchp31s0 p31x p31k0 4.5e-10
Rchp31s1 p31k0 p31k1 12
Lchp31s1 p31k0 p31k1 6e-11
Lchp31m p31k1 chp31 1.5e-09
Cchp31 chp31 0 6e-13
Rchp32dc chp31 p32x 0.02
Rchp32s0 p32x p32k0 4
Lchp32s0 p32x p32k0 4.5e-10
Rchp32s1 p32k0 p32k1 12
Lchp32s1 p32k0 p32k1 6e-11
Lchp32m p32k1 chp32 1.5e-09
Cchp32 chp32 0 6e-13
Rchp33dc chp32 p33x 0.02
Rchp33s0 p33x p33k0 4
Lchp33s0 p33x p33k0 4.5e-10
Rchp33s1 p33k0 p33k1 12
Lchp33s1 p33k0 p33k1 6e-11
Lchp33m p33k1 chp33 1.5e-09
Cchp33 chp33 0 6e-13
Rchp34dc chp33 p34x 0.02
Rchp34s0 p34x p34k0 4
Lchp34s0 p34x p34k0 4.5e-10
Rchp34s1 p34k0 p34k1 12
Lchp34s1 p34k0 p34k1 6e-11
Lchp34m p34k1 chp34 1.5e-09
Cchp34 chp34 0 6e-13
Rchp35dc chp34 p35x 0.02
Rchp35s0 p35x p35k0 4
Lchp35s0 p35x p35k0 4.5e-10
Rchp35s1 p35k0 p35k1 12
Lchp35s1 p35k0 p35k1 6e-11
Lchp35m p35k1 chp35 1.5e-09
Cchp35 chp35 0 6e-13
Rchp36dc chp35 p36x 0.02
Rchp36s0 p36x p36k0 4
Lchp36s0 p36x p36k0 4.5e-10
Rchp36s1 p36k0 p36k1 12
Lchp36s1 p36k0 p36k1 6e-11
Lchp36m p36k1 chp36 1.5e-09
Cchp36 chp36 0 6e-13
Rchp37dc chp36 p37x 0.02
Rchp37s0 p37x p37k0 4
Lchp37s0 p37x p37k0 4.5e-10
Rchp37s1 p37k0 p37k1 12
Lchp37s1 p37k0 p37k1 6e-11
Lchp37m p37k1 chp37 1.5e-09
Cchp37 chp37 0 6e-13
Rchp38dc chp37 p38x 0.02
Rchp38s0 p38x p38k0 4
Lchp38s0 p38x p38k0 4.5e-10
Rchp38s1 p38k0 p38k1 12
Lchp38s1 p38k0 p38k1 6e-11
Lchp38m p38k1 chp38 1.5e-09
Cchp38 chp38 0 6e-13
Rchp39dc chp38 p39x 0.02
Rchp39s0 p39x p39k0 4
Lchp39s0 p39x p39k0 4.5e-10
Rchp39s1 p39k0 p39k1 12
Lchp39s1 p39k0 p39k1 6e-11
Lchp39m p39k1 vinp 1.5e-09
Cchp39 vinp 0 6e-13
Rchn0dc chn_in n0x 0.02
Rchn0s0 n0x n0k0 4
Lchn0s0 n0x n0k0 4.5e-10
Rchn0s1 n0k0 n0k1 12
Lchn0s1 n0k0 n0k1 6e-11
Lchn0m n0k1 chn0 1.5e-09
Cchn0 chn0 0 6e-13
Rchn1dc chn0 n1x 0.02
Rchn1s0 n1x n1k0 4
Lchn1s0 n1x n1k0 4.5e-10
Rchn1s1 n1k0 n1k1 12
Lchn1s1 n1k0 n1k1 6e-11
Lchn1m n1k1 chn1 1.5e-09
Cchn1 chn1 0 6e-13
Rchn2dc chn1 n2x 0.02
Rchn2s0 n2x n2k0 4
Lchn2s0 n2x n2k0 4.5e-10
Rchn2s1 n2k0 n2k1 12
Lchn2s1 n2k0 n2k1 6e-11
Lchn2m n2k1 chn2 1.5e-09
Cchn2 chn2 0 6e-13
Rchn3dc chn2 n3x 0.02
Rchn3s0 n3x n3k0 4
Lchn3s0 n3x n3k0 4.5e-10
Rchn3s1 n3k0 n3k1 12
Lchn3s1 n3k0 n3k1 6e-11
Lchn3m n3k1 chn3 1.5e-09
Cchn3 chn3 0 6e-13
Rchn4dc chn3 n4x 0.02
Rchn4s0 n4x n4k0 4
Lchn4s0 n4x n4k0 4.5e-10
Rchn4s1 n4k0 n4k1 12
Lchn4s1 n4k0 n4k1 6e-11
Lchn4m n4k1 chn4 1.5e-09
Cchn4 chn4 0 6e-13
Rchn5dc chn4 n5x 0.02
Rchn5s0 n5x n5k0 4
Lchn5s0 n5x n5k0 4.5e-10
Rchn5s1 n5k0 n5k1 12
Lchn5s1 n5k0 n5k1 6e-11
Lchn5m n5k1 chn5 1.5e-09
Cchn5 chn5 0 6e-13
Rchn6dc chn5 n6x 0.02
Rchn6s0 n6x n6k0 4
Lchn6s0 n6x n6k0 4.5e-10
Rchn6s1 n6k0 n6k1 12
Lchn6s1 n6k0 n6k1 6e-11
Lchn6m n6k1 chn6 1.5e-09
Cchn6 chn6 0 6e-13
Rchn7dc chn6 n7x 0.02
Rchn7s0 n7x n7k0 4
Lchn7s0 n7x n7k0 4.5e-10
Rchn7s1 n7k0 n7k1 12
Lchn7s1 n7k0 n7k1 6e-11
Lchn7m n7k1 chn7 1.5e-09
Cchn7 chn7 0 6e-13
Rchn8dc chn7 n8x 0.02
Rchn8s0 n8x n8k0 4
Lchn8s0 n8x n8k0 4.5e-10
Rchn8s1 n8k0 n8k1 12
Lchn8s1 n8k0 n8k1 6e-11
Lchn8m n8k1 chn8 1.5e-09
Cchn8 chn8 0 6e-13
Rchn9dc chn8 n9x 0.02
Rchn9s0 n9x n9k0 4
Lchn9s0 n9x n9k0 4.5e-10
Rchn9s1 n9k0 n9k1 12
Lchn9s1 n9k0 n9k1 6e-11
Lchn9m n9k1 chn9 1.5e-09
Cchn9 chn9 0 6e-13
Rchn10dc chn9 n10x 0.02
Rchn10s0 n10x n10k0 4
Lchn10s0 n10x n10k0 4.5e-10
Rchn10s1 n10k0 n10k1 12
Lchn10s1 n10k0 n10k1 6e-11
Lchn10m n10k1 chn10 1.5e-09
Cchn10 chn10 0 6e-13
Rchn11dc chn10 n11x 0.02
Rchn11s0 n11x n11k0 4
Lchn11s0 n11x n11k0 4.5e-10
Rchn11s1 n11k0 n11k1 12
Lchn11s1 n11k0 n11k1 6e-11
Lchn11m n11k1 chn11 1.5e-09
Cchn11 chn11 0 6e-13
Rchn12dc chn11 n12x 0.02
Rchn12s0 n12x n12k0 4
Lchn12s0 n12x n12k0 4.5e-10
Rchn12s1 n12k0 n12k1 12
Lchn12s1 n12k0 n12k1 6e-11
Lchn12m n12k1 chn12 1.5e-09
Cchn12 chn12 0 6e-13
Rchn13dc chn12 n13x 0.02
Rchn13s0 n13x n13k0 4
Lchn13s0 n13x n13k0 4.5e-10
Rchn13s1 n13k0 n13k1 12
Lchn13s1 n13k0 n13k1 6e-11
Lchn13m n13k1 chn13 1.5e-09
Cchn13 chn13 0 6e-13
Rchn14dc chn13 n14x 0.02
Rchn14s0 n14x n14k0 4
Lchn14s0 n14x n14k0 4.5e-10
Rchn14s1 n14k0 n14k1 12
Lchn14s1 n14k0 n14k1 6e-11
Lchn14m n14k1 chn14 1.5e-09
Cchn14 chn14 0 6e-13
Rchn15dc chn14 n15x 0.02
Rchn15s0 n15x n15k0 4
Lchn15s0 n15x n15k0 4.5e-10
Rchn15s1 n15k0 n15k1 12
Lchn15s1 n15k0 n15k1 6e-11
Lchn15m n15k1 chn15 1.5e-09
Cchn15 chn15 0 6e-13
Rchn16dc chn15 n16x 0.02
Rchn16s0 n16x n16k0 4
Lchn16s0 n16x n16k0 4.5e-10
Rchn16s1 n16k0 n16k1 12
Lchn16s1 n16k0 n16k1 6e-11
Lchn16m n16k1 chn16 1.5e-09
Cchn16 chn16 0 6e-13
Rchn17dc chn16 n17x 0.02
Rchn17s0 n17x n17k0 4
Lchn17s0 n17x n17k0 4.5e-10
Rchn17s1 n17k0 n17k1 12
Lchn17s1 n17k0 n17k1 6e-11
Lchn17m n17k1 chn17 1.5e-09
Cchn17 chn17 0 6e-13
Rchn18dc chn17 n18x 0.02
Rchn18s0 n18x n18k0 4
Lchn18s0 n18x n18k0 4.5e-10
Rchn18s1 n18k0 n18k1 12
Lchn18s1 n18k0 n18k1 6e-11
Lchn18m n18k1 chn18 1.5e-09
Cchn18 chn18 0 6e-13
Rchn19dc chn18 n19x 0.02
Rchn19s0 n19x n19k0 4
Lchn19s0 n19x n19k0 4.5e-10
Rchn19s1 n19k0 n19k1 12
Lchn19s1 n19k0 n19k1 6e-11
Lchn19m n19k1 chn19 1.5e-09
Cchn19 chn19 0 6e-13
Rchn20dc chn19 n20x 0.02
Rchn20s0 n20x n20k0 4
Lchn20s0 n20x n20k0 4.5e-10
Rchn20s1 n20k0 n20k1 12
Lchn20s1 n20k0 n20k1 6e-11
Lchn20m n20k1 chn20 1.5e-09
Cchn20 chn20 0 6e-13
Rchn21dc chn20 n21x 0.02
Rchn21s0 n21x n21k0 4
Lchn21s0 n21x n21k0 4.5e-10
Rchn21s1 n21k0 n21k1 12
Lchn21s1 n21k0 n21k1 6e-11
Lchn21m n21k1 chn21 1.5e-09
Cchn21 chn21 0 6e-13
Rchn22dc chn21 n22x 0.02
Rchn22s0 n22x n22k0 4
Lchn22s0 n22x n22k0 4.5e-10
Rchn22s1 n22k0 n22k1 12
Lchn22s1 n22k0 n22k1 6e-11
Lchn22m n22k1 chn22 1.5e-09
Cchn22 chn22 0 6e-13
Rchn23dc chn22 n23x 0.02
Rchn23s0 n23x n23k0 4
Lchn23s0 n23x n23k0 4.5e-10
Rchn23s1 n23k0 n23k1 12
Lchn23s1 n23k0 n23k1 6e-11
Lchn23m n23k1 chn23 1.5e-09
Cchn23 chn23 0 6e-13
Rchn24dc chn23 n24x 0.02
Rchn24s0 n24x n24k0 4
Lchn24s0 n24x n24k0 4.5e-10
Rchn24s1 n24k0 n24k1 12
Lchn24s1 n24k0 n24k1 6e-11
Lchn24m n24k1 chn24 1.5e-09
Cchn24 chn24 0 6e-13
Rchn25dc chn24 n25x 0.02
Rchn25s0 n25x n25k0 4
Lchn25s0 n25x n25k0 4.5e-10
Rchn25s1 n25k0 n25k1 12
Lchn25s1 n25k0 n25k1 6e-11
Lchn25m n25k1 chn25 1.5e-09
Cchn25 chn25 0 6e-13
Rchn26dc chn25 n26x 0.02
Rchn26s0 n26x n26k0 4
Lchn26s0 n26x n26k0 4.5e-10
Rchn26s1 n26k0 n26k1 12
Lchn26s1 n26k0 n26k1 6e-11
Lchn26m n26k1 chn26 1.5e-09
Cchn26 chn26 0 6e-13
Rchn27dc chn26 n27x 0.02
Rchn27s0 n27x n27k0 4
Lchn27s0 n27x n27k0 4.5e-10
Rchn27s1 n27k0 n27k1 12
Lchn27s1 n27k0 n27k1 6e-11
Lchn27m n27k1 chn27 1.5e-09
Cchn27 chn27 0 6e-13
Rchn28dc chn27 n28x 0.02
Rchn28s0 n28x n28k0 4
Lchn28s0 n28x n28k0 4.5e-10
Rchn28s1 n28k0 n28k1 12
Lchn28s1 n28k0 n28k1 6e-11
Lchn28m n28k1 chn28 1.5e-09
Cchn28 chn28 0 6e-13
Rchn29dc chn28 n29x 0.02
Rchn29s0 n29x n29k0 4
Lchn29s0 n29x n29k0 4.5e-10
Rchn29s1 n29k0 n29k1 12
Lchn29s1 n29k0 n29k1 6e-11
Lchn29m n29k1 chn29 1.5e-09
Cchn29 chn29 0 6e-13
Rchn30dc chn29 n30x 0.02
Rchn30s0 n30x n30k0 4
Lchn30s0 n30x n30k0 4.5e-10
Rchn30s1 n30k0 n30k1 12
Lchn30s1 n30k0 n30k1 6e-11
Lchn30m n30k1 chn30 1.5e-09
Cchn30 chn30 0 6e-13
Rchn31dc chn30 n31x 0.02
Rchn31s0 n31x n31k0 4
Lchn31s0 n31x n31k0 4.5e-10
Rchn31s1 n31k0 n31k1 12
Lchn31s1 n31k0 n31k1 6e-11
Lchn31m n31k1 chn31 1.5e-09
Cchn31 chn31 0 6e-13
Rchn32dc chn31 n32x 0.02
Rchn32s0 n32x n32k0 4
Lchn32s0 n32x n32k0 4.5e-10
Rchn32s1 n32k0 n32k1 12
Lchn32s1 n32k0 n32k1 6e-11
Lchn32m n32k1 chn32 1.5e-09
Cchn32 chn32 0 6e-13
Rchn33dc chn32 n33x 0.02
Rchn33s0 n33x n33k0 4
Lchn33s0 n33x n33k0 4.5e-10
Rchn33s1 n33k0 n33k1 12
Lchn33s1 n33k0 n33k1 6e-11
Lchn33m n33k1 chn33 1.5e-09
Cchn33 chn33 0 6e-13
Rchn34dc chn33 n34x 0.02
Rchn34s0 n34x n34k0 4
Lchn34s0 n34x n34k0 4.5e-10
Rchn34s1 n34k0 n34k1 12
Lchn34s1 n34k0 n34k1 6e-11
Lchn34m n34k1 chn34 1.5e-09
Cchn34 chn34 0 6e-13
Rchn35dc chn34 n35x 0.02
Rchn35s0 n35x n35k0 4
Lchn35s0 n35x n35k0 4.5e-10
Rchn35s1 n35k0 n35k1 12
Lchn35s1 n35k0 n35k1 6e-11
Lchn35m n35k1 chn35 1.5e-09
Cchn35 chn35 0 6e-13
Rchn36dc chn35 n36x 0.02
Rchn36s0 n36x n36k0 4
Lchn36s0 n36x n36k0 4.5e-10
Rchn36s1 n36k0 n36k1 12
Lchn36s1 n36k0 n36k1 6e-11
Lchn36m n36k1 chn36 1.5e-09
Cchn36 chn36 0 6e-13
Rchn37dc chn36 n37x 0.02
Rchn37s0 n37x n37k0 4
Lchn37s0 n37x n37k0 4.5e-10
Rchn37s1 n37k0 n37k1 12
Lchn37s1 n37k0 n37k1 6e-11
Lchn37m n37k1 chn37 1.5e-09
Cchn37 chn37 0 6e-13
Rchn38dc chn37 n38x 0.02
Rchn38s0 n38x n38k0 4
Lchn38s0 n38x n38k0 4.5e-10
Rchn38s1 n38k0 n38k1 12
Lchn38s1 n38k0 n38k1 6e-11
Lchn38m n38k1 chn38 1.5e-09
Cchn38 chn38 0 6e-13
Rchn39dc chn38 n39x 0.02
Rchn39s0 n39x n39k0 4
Lchn39s0 n39x n39k0 4.5e-10
Rchn39s1 n39k0 n39k1 12
Lchn39s1 n39k0 n39k1 6e-11
Lchn39m n39k1 vinn 1.5e-09
Cchn39 vinn 0 6e-13
Rterm vinp vinn 100
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
wrdata ctle_ac.dat vdb(outp_ctle,outn_ctle) vdb(outp_dfe,outn_dfe) vdb(vinp,vinn)
.endc

.end
