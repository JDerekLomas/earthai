# HRRR agreement probe, literature pass, California month: results (14 Sep 2026)

Continues `2026-09-14-physics-conditioned-clouds.md`. All four items of that handoff's definition
of done are live at https://earthai-scales.vercel.app/ : `/month/` (full clean month),
`/hrrr/` (the probe), `/research/` (the literature pass), deployed from `main`.

## Results, in one paragraph each
- **Month.** QC quarantined 2 GeoColor + 14 infrared white-wedge frames. On the complete month the
  infrared weather anomaly e-folds at **6 h** (~2 independent skies a day, 60-80 a month); ocean
  variance is 93% weather; advection beats persistence by +0.05..+0.07. Page numbers updated.
- **HRRR vs GOES (941 hourly pairs, 305 daylight pairs; HRRR covers 66% of the frame, its western
  edge cuts through).** Cloud-mask anomaly r over daylight ocean: **0.36** (low cloud), 0.40 (total);
  shuffled-day floor 0.00; GOES 24 h earlier 0.10; GOES 1 h earlier 0.62. Infrared anomaly r **0.45**
  (ocean 0.28, land 0.43); floor 0.01; 24 h 0.19; 1 h 0.75. Agreement rises with cell size
  (0.45 -> 0.58 at 122 km) and toward the coast (0.47 within 50 km, 0.25 at 400 km). HRRR runs ~10%
  cloudier over ocean and ~4 K warm. **Verdict: HRRR knows today's sky at the scale of the deck's
  edge and above, not pixel by pixel; beats climatology and yesterday, nowhere near the last hour.**
- **Literature.** Nobody has published HRRR-in / GOES-frame-out. CorrDiff's recipe is open (Apache,
  physicsnemo; CorrDiff-Mini ~10 A100-h); it already won on GOES infrared nowcasting (Chase et al.
  2025); NWP-fields-to-satellite-image diffusion exists once, small (Hatanaka 2023). No released
  weights do the job. MetNet-3 does NOT take NWP input (brief was wrong). Notes: `docs/research/lit-2026-09-14/`.

## Traps found (both now asserted in code, both documented in the scripts)
1. **HRRR `SBT113` is NOT band 13.** It is GOES-11 channel 3, the 6.7 um water-vapour channel
   (200-250 K everywhere). The 10.7 um window that matches GOES-R band 13 is **`SBT114`**. A first
   run read 42 K cold. `scripts/fetch_hrrr.py` fetches SBT114 (and SBT113 as a second channel).
2. **The GIBS Band 13 palette has two grey ramps that collide.** Main ramp -18.9..56.7 C; a coarse
   one at -79.6..-70.6 C whose greys (230,204,177,155,129,102,76,54,27,5) sit on the main ramp, so
   JPEG noise turned a tenth of warm land into -75 C cloud tops. `IRPalette` drops those ten entries
   and self-checks every kept colour under +-2 noise (worst 2 C; the bug was 93 C).
3. HRRR "boundary layer cloud layer" TCDC is byte-identical to LCDC over this window; not fetched.

## Data on disk (gitignored, free to re-fetch)
- `data/hrrr/california/*.npz` 959 hours (2026-09-13T23Z not in the archive), 312 MB, crop window
  cols 0:363 rows 357:950 of the HRRR grid; keys SBT114 SBT113 LCDC MCDC HCDC TCDC i0 j0.
- `data/hrrr/gibs_band13_colormap.xml` the palette the inversion reads (fetch again from
  https://gibs.earthdata.nasa.gov/colormaps/v1.3/Clean_Longwave_Infrared_Window_Band.xml if lost).
- Analysis writes `site/hrrr/california/agreement.json` + the figures; ~10 min on the laptop.

## Next, if option 1 goes ahead
- **The probe that matters: HRRR f06 / f12 forecasts vs GOES**, same controls. The analysis hour
  assimilates satellite data, so today's 0.36-0.45 is an upper bound; the generator needs the physics
  where persistence has died. `fetch_hrrr.py` only needs the `wrfsfcf00` -> `wrfsfcfNN` filename.
- Years of pairs: HRRR back to 2014-07-30 in the same bucket; native GOES-West on AWS (not verified
  this session); SEVIR for the eastern US.
- Conditioning stack is built: `scripts/hrrr_grid.py` puts any HRRR field on the frame grid.
- GPU box untouched by this work (earth-96 is queued for it after the scratch arm).
