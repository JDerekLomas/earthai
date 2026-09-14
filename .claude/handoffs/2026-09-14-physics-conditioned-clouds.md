# Physics-conditioned clouds: HRRR agreement probe, literature pass, and finishing the California month

## Who reads this and what it decides
Derek reads results as pages on https://earthai-scales.vercel.app/ (not terminal output), plus a short
plain-English chat summary. His goal is **beauty with some truth**. The decision these results change:
build a **physics-conditioned generator** (a physics model's cloud fields in, an image model painting
km-scale cloud texture out — the NVIDIA CorrDiff pattern) versus staying with cheap latent-space controls
in the existing GAN. He asked for this explicitly: "do it. may as well do the actual research."

## Definition of done
1. **California month page live** at /month/ with real data (steps below).
2. **HRRR-vs-GOES agreement probe** run with controls; numbers and a short verdict on a page.
3. **Literature pass** with every citation verified by fetching it; what exists, what has code/weights,
   what it implies for building option 1; on a page.
4. Everything committed, pushed, deployed from `main`, and checked with curl.

## State at handoff (14 Sep 2026)
- **Month fetch is COMPLETE** (finished in the previous session, both exited 0): `data/goes/california_x3`
  GeoColor 5,246 of 5,736 slots, `data/goes/california_x3_ir` 5,617. The missing slots are archive gaps
  (GeoColor answered nothing at 34-36 days back when probed). No need to re-run the fetch.
- **Then build the page:** `python3 scripts/goes_qc.py --selftest`, then `--dir data/goes/california_x3 --apply`
  (and `_ir`); `python3 scripts/month_sheet.py --dir data/goes/california_x3 --lon -122` and
  `--dir data/goes/california_x3_ir --lon -122 --daylight-clips no`; `python3 scripts/sky_memory.py
  --dir data/goes/california_x3_ir --place california`. Then update hard-coded numbers in
  `site/month/index.html`: the `LAG` array, the e-folding span (full month says **6 h, ~2 independent skies
  a day, ~60-80 a month** — the page still says 12 h / ~30), and the infrared note (it sees ~71% of the
  deck, not all of it). Deploy: `cd site && npx vercel --prod --yes` (project earthai-scales).
- **GPU box** (Scaleway `earthai-gpu`, `root@51.159.165.126`, L40S, EUR1.47/h): the from-scratch arm of the
  transfer-vs-scratch A/B is training (1000 kimg cap, early_stop armed, patience 4, min 400 kimg). The
  transfer arm finished: best **FID 7.72 at 200 kimg**, `runs/ab-transfer/*/network-snapshot-000200.pkl`.
  idle_watch autostops the box after 45 idle minutes. **The HRRR probe needs no GPU; don't use the box.**
- **The card is not contended.** The parallel landshapes session finished its collection (9,477 crops at
  30 m/px) and is holding for Derek's decision on what to train, so nothing is queued behind the scratch arm.

## Sibling session's handoff — read it before any training decision
`/Users/dereklomas/earth/.claude/handoffs/2026-09-13-landshapes-curation.md` (210 lines, the `earth` repo,
a different project on the same GPU box). It is the shared context for the two sessions' overlapping
findings rather than the chat thread. Most relevant sections: **Scale** (its biggest correction: imagery at
10 m/px yields texture and roads, 30-60 m/px yields landform — the same shape as our z8-vs-z9 result), a
**silent data-loss bug** from filename collisions (ours was the benign variant: 14 identical tiles, nothing
lost), and an explicit **do-not-pick-a-snapshot-by-FID** section carrying both runs' numbers. Its
`scripts/snapshot_compare.py` is the fixed-seed instrument that overturned my "softer" claim; it now has a
third column separating real detail from grain (its absolute values are implementation-specific — compare
flatness across one run's snapshots, never its numbers against ours).

## Measured facts — do not re-derive
- California infrared month: weather memory e-folds at **6 h**; advection beats persistence by only
  **+0.05 (10 min), +0.07 (30 min), +0.006 (6 h)**, deck moves 2-15 km/h (control: rigid shift recovered
  exactly). Variance over ocean: fixed map 5.0%, daily cycle 2.1%, **weather 93.0%**; over land daily
  cycle 32.4% (mostly ground heating). => the deck forms/dissolves in place far more than it moves.
- Visible (GeoColor midday): a fixed per-pixel map explains **23.9%** of cloud variance; cloud frequency
  75% at 400-900 km offshore, 45% within 50 km, 16-25% inland.
- Infrared vs the low deck: +17 grey levels over clear sea (2.8x its spread), but **28.7%** of visibly
  cloudy pixels are indistinguishable from clear ocean in IR.
- Terrain on the exact frame grid: `data/terrain/california_{elev.npy,hillshade.png,land.png,terrain.json}`,
  lat 31.96-45.08, 1,906 m/px, z6 x0=8 y0=23 span 3 (lon -135 to -118.125).
- **HRRR on AWS Open Data**, bucket `noaa-hrrr-bdp-pds`, daily prefixes `hrrr.YYYYMMDD/conus/`, back to
  **2014-07-30**, hourly, 3 km. `hrrr.tHHz.wrfsfcf00.grib2` is ~150 MB but its `.idx` gives byte offsets, so
  fetch single fields by HTTP range. Fields confirmed present: LCDC/MCDC/HCDC, TCDC boundary-layer and
  entire-atmosphere, HGT cloud base/top/ceiling, TMP surface, UGRD/VGRD 10 m, DSWRF, VIS, and **simulated
  brightness temperatures SBT113, SBT114, SBT123, SBT124** (a physics-rendered satellite view).

## The HRRR probe — spec
- **Question:** does HRRR put cloud where GOES saw cloud, hourly, at the scale of our frame?
- **Pairs:** the ~40 California days × 24 analysis hours (f00), against GOES frames at the same instant.
- **Grid:** reproject HRRR's Lambert conformal grid onto the 768 px web-mercator frame. Report what
  fraction of the frame HRRR covers — its western edge may not reach -135.
- **Comparisons:** (a) SBT113 against GOES Band 13 IR — same physical quantity. (b) LCDC / boundary-layer
  TCDC against the GeoColor cloud mask (build_dataset.stats test) in daylight over ocean. Report by
  distance-from-coast band.
- **Controls, required:** a shuffled-time floor (HRRR hour t vs GOES from a different day); a persistence
  bar HRRR must beat (GOES 24 h earlier). A physics skeleton is only worth building on if it beats both.
- **Known trap:** the GIBS Band 13 JPEG is colour-mapped, not Kelvin (grey ramp, colours for cold tops).
  Use rank correlation, or invert the published GIBS colormap XML — do not treat grey level as temperature.
- GRIB decoding: check for `cfgrib`/`eccodes` or `pygrib`; install into a venv if absent.

## The literature pass — spec
- Topics: generative downscaling from NWP (CorrDiff and successors); generative nowcasting from satellite
  or radar (DGMR, MetNet, diffusion nowcasting); learned global models only as context (GraphCast,
  Pangu-Weather, GenCast — ~25 km, no texture); simulated satellite imagery from NWP; aesthetic or
  artistic cloud synthesis. My recall of these is from memory with a cutoff — treat it as leads, not facts.
- Rules: fetch every paper/repo before citing; mark anything unverified; record code/weights availability
  and licence; for each, say concretely what it means for building option 1 on HRRR + GOES.
- Agents are authorized for this (Derek asked). Global rules apply: at most ~8 subagents, Sonnet, each
  writes results to a file and prints only heads/counts.

## Triage key (fixed 14 Sep, morning)
`TRIAGE_KEY` on the earthai-scales Vercel project was stored EMPTY, so `/api/verdicts` returned 401 to
everyone and no triage verdict could be saved server-side. Cause: `vercel env add` on CLI 54.x stores an
empty value when the value comes from stdin or a file redirect (reproduced: pulled length 0 both ways).
Fixed by deleting the empty rows and creating the var through the REST API (`POST /v10/projects/{id}/env`
with the CLI's token from `~/Library/Application Support/com.vercel.cli/auth.json`), then redeploying.
Verified: pull length 32, GET with the key 200, wrong key 401. **To get the key again:** `cd site &&
npx vercel env pull .env.probe --environment=production` — it is Encrypted, not Sensitive, so pull reveals
it; read it, delete the file. Derek's link is `https://earthai-scales.vercel.app/triage/#k=<key>`. Never
re-add it with `vercel env add`. The page merges browser-only verdicts up on the next successful load, so
anything Derek marked while it was broken syncs itself.

## Conventions and traps from this project
- Every measurement gets a control shaped like the real input. The recurring bug this week was a proxy
  failing hardest on the most interesting subject (darkness read as missing data, three times).
- ssh with a detached process can hang the connection: redirect `</dev/null`, use `setsid nohup`, and
  verify from a separate ssh.
- Commit and push as you go; deploy only from `main`; pages copy the IBM Plex tokens from
  `site/month/index.html`. Ask before spending money — HRRR, GOES and terrain data are all free.
