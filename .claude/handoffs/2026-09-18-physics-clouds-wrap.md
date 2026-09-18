# Wrap of the physics-clouds thread (14-18 Sep 2026)

Everything from this thread is committed, deployed from `main`, and checked live. The uncommitted
files in the tree at wrap time (`scripts/fetch_clouds.py`, `site/globe/_rt/`, `.claude/worktrees/`)
belong to the cloud-layer-globe session that was running `fetch_clouds.py encode` at 22:00 on 18 Sep;
not touched.

## Live pages (https://earthai-scales.vercel.app/)
- `/month/`  California, 41+ days: real, smooth (RIFE x4), same-hour flipbooks, Always-day layer.
- `/conus/`  the lower 48 from GOES-East, growing nightly.
- `/hrrr/`   HRRR vs GOES with matched-lead persistence bars and day-block CIs; f00/f06/f12.
- `/research/` literature pass, 30 works, every citation fetched.
- `/earth/`  ten days (6-15 Sep) of the five-satellite mosaic on a globe, real + smooth, sun-fixed mode.
- `/eclipse/` 12 Aug eclipse from Meteosat-12 (top) and GOES-East (below), from the native archives.
- `/night/`  14 nights of the VIIRS Day/Night Band on a globe; 8 Sep (Kp 5.0) shows the aurora.

## Standing jobs
- launchd `com.dereklomas.earthai-goes` 04:00 daily: California + CONUS, both layers. Log
  `scratch/goes_rolling.log`. Laptop must be awake.

## Findings that decide things (details in the dated handoffs of 14-16 Sep)
- Weather memory 6 h; the deck forms/dissolves in place (advection +0.05); ocean variance 93% weather.
- HRRR analysis: mask r 0.36, IR 0.45; beats climatology and yesterday, not the last hour. At 6 h the
  physics beats persistence for high cloud (0.54 vs 0.39) but LOSES for the low deck (0.25 vs 0.33);
  wins the deck only between 6 h and a day. Condition on f06 fields, not f00.
- Nobody has published HRRR-in / GOES-frame-out; CorrDiff recipe is open and won on GOES IR.
- Traps now asserted in code: SBT113 is water vapour (use SBT114); GIBS band-13 palette double grey
  ramp; GeoColor single-frame flashes (16 in a month); RIFE directory mode skips symlinks and dies past
  ~10k outputs (chunked); a `pgrep -f vercel` guard matches its own shell (use `ps -eo args | grep -c "[v]ercel --prod"`).

## Open threads, in the order I'd take them
1. Derek's own briefs of 18 Sep (cloud-layer globe, WebCodecs regional tiles, real motion between
   frames) -- another session is on them.
2. The two experiments that decide option 1: a nowcaster baseline vs the 0.62 one-hour bar; a small
   residual-diffusion model on f06 HRRR fields vs the 0.25 bar. GPU box, an evening each.
3. Always-day on the CONUS frame; the learned always-day (IR + HRRR low cloud -> day look).
4. Native GOES archive for years of pairs (`scripts/fetch_goes_aws.py` exists, ran on Hetzner).
5. Aurora: re-run `scripts/fetch_night.py` after the next Kp>=5 night; OVATION oval overlay.
