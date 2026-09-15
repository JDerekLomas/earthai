# Always day: a permanent-noon rendering of the California month (and CONUS) from infrared

## Posture (read first)
Derek sits at his laptop and opens https://earthai-scales.vercel.app/month/ (and /conus/). He presses
play and watches a sky move for a minute or two. Today the GeoColor clip changes character every dusk
and dawn: blue-grey night clouds over city lights, then the terminator sweeps across, then daylight.
He wants a clip that reads as ONE continuous daytime sky, day and night, with the clouds we actually
have. He will judge it by eye, in the same player, next to the real GeoColor. He is a designer and
researcher, not a meteorologist; every number on a page needs a plain-English sentence.

## Goal
A deterministic, no-AI "always day" layer: infrared cloud (Band 13, which we have every 10 minutes
around the clock) rendered as daytime-looking cloud over a cloud-free daytime Earth. Same player,
a third layer button ("Always day") on /month/ and later /conus/.

## Definition of done
1. `scripts/always_day.py --dir data/goes/california_x3_ir --place california` writes
   `data/goes/california_x3_day/*.jpg` (same stamps as the IR frames), then `month_sheet.py` runs on
   that directory like any other layer (whole month, days, hours).
2. A positive control: for ~20 random DAYTIME instants, the always-day frame vs the real GeoColor frame
   -- report the cloud-mask agreement (reuse `build_dataset.stats` cloud test, or a simple brightness
   threshold) and show 4 side-by-side pairs as a strip on the page. Honest cost stated: infrared misses
   ~29% of the low deck (measured, `.claude/handoffs/2026-09-14-hrrr-probe-results.md`), so the deck
   will look thinner than in GeoColor, especially at night.
3. Layer button on /month/ ("Always day"), the strip and one paragraph explaining what it is and is not.
4. Committed, pushed, deployed from `main` (`cd site && npx vercel --prod --yes`), curl-checked, and the
   page looked at in a browser (chrome-devtools new_page + take_screenshot) -- a page shipped
   yesterday said "not built yet" for an hour because nobody looked.

## How (decisions already made)
- Infrared JPEG -> temperature: use `IRPalette` from `scripts/hrrr_agreement.py` (it drops the palette's
  ambiguous cold greys and self-checks; do NOT re-invert the colormap yourself).
- Cloud opacity from temperature needs a per-pixel CLEAR-SKY reference, because the low deck sits within
  a few degrees of the sea: build `T_clear[solar_hour][pixel]` = a high percentile (e.g. 90th) of T over
  the month at that solar hour (clear = warmest), then opacity = clip((T_clear - T) / 12 K, 0, 1) with
  a gentle curve; cold tops (< -30 C) fully white, mid cloud light grey, low deck white-grey. Tune by eye
  against daytime GeoColor -- that is what the control is for.
- Basemap: a cloud-free daytime Earth on EXACTLY the frame grid (z6, x0=8, y0=23, span 3, 768 px for
  California; see `data/terrain/california_terrain.json`). Sources: NASA GIBS `BlueMarble_ShadedRelief_Bathymetry`
  or `MODIS_Terra_CorrectedReflectance_TrueColor` best-day; or EOX Sentinel-2 cloudless (used elsewhere
  in the site, CC BY-NC-SA). `scripts/fetch_goes.py::fetch()` already stitches GIBS tiles for a layer name.
  Cache it as `data/basemap/california_day.jpg`.
- Composite: out = basemap * (1 - a) + cloud_colour * a, cloud_colour from a light warm white; add a
  faint shadow (darken basemap slightly under thick cloud) so it reads as depth. Keep it simple first.
- Solar time: lon -122 for California (see `month_sheet.py::solar`).
- Do NOT touch `site/month/california_x3*` outputs or `scripts/month_sheet.py` internals beyond adding
  the new directory; add the layer button by copying the existing two.
- Work in /Users/dereklomas/sourcelibrary/earthai directly (data/ is gitignored and not in any worktree).
  Commit on `main`. Do not run `vercel` while another session's deploy is in flight (check
  `pgrep -f vercel`). Another session may be adding a whole-Earth page under site/earth/ at the same
  time; stay out of its files.
- Page style: copy the tokens and markup from `site/month/index.html`. Every number in a sentence.

## What NOT to redo
- The IR->temperature inversion, the QC, the month tool, the palette trap analysis -- all exist.
- No AI model in this pass. The learned version (IR + HRRR low cloud -> daytime look) is a later task.

## Report back
When done: write a 5-line summary at the end of this file (what shipped, the control numbers, what
looked wrong), commit, and message the session named "physics-clouds" via SendMessage if it exists
(one line: DONE + URL), else just finish.
