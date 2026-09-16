# The real night: VIIRS Day/Night Band on a globe, aurora when there is one

## Posture (read first)
Derek opens a NEW page, https://earthai-scales.vercel.app/night/, and sees the Earth at night as it
actually was: moonlit cloud, city lights, ships, fires, and on active nights the aurora oval. He
turns it, steps night by night through the last two weeks, and looks for the aurora. He is a
designer; it must be beautiful first and honest second: every night is one picture per place
(the polar orbiter passes once, ~01:30 local), NOT a ten-minute film, and the page says so.
The existing globe at /earth/ is STABLE and must not be touched: separate page, separate data
directory, separate scripts. Copy from it, never edit it.

## What is known (checked 16 Sep 2026)
- NASA GIBS (epsg4326 WMTS, same endpoint `scripts/fetch_earth.py::gibs_tiles` already uses) serves
  these daily layers: `VIIRS_NOAA21_DayNightBand_At_Sensor_Radiance`, `VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance`,
  `VIIRS_SNPP_DayNightBand_At_Sensor_Radiance`, and `VIIRS_NOAA21_DayNightBand` (a stretched
  rendering). Read the capabilities XML (`https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml`,
  5 MB, saved at /private/tmp/claude-501/-Users-dereklomas-sourcelibrary-earthai/a186a6ab-291b-42da-b853-70da70f144ae/scratchpad/caps4326.xml if still there)
  for each layer's TileMatrixSet, format and date range. The radiance layer is the honest one
  (aurora, moonlight and cities at their real relative brightness); the stretched one is prettier.
  Try both; decide by looking at a strong-aurora night if one exists in the window.
- NASA's Worldview shows the same layers; use it to sanity-check one night by eye
  (https://worldview.earthdata.nasa.gov/, layer "Day/Night Band").
- Geomagnetic activity: `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
  (recent days, 3-hourly Kp) and the 27-day / monthly archives under `https://services.swpc.noaa.gov/`
  (find the daily geomagnetic indices file). Kp >= 5 is a storm; the oval reaches the northern US
  around Kp 7. List the nights in the fetched window by their max Kp and show it on the page.
- OVATION aurora forecast, the model oval right now: `https://services.swpc.noaa.gov/json/ovation_aurora_latest.json`
  (lon/lat/probability grid). Optional overlay for "tonight"; not an archive.
- Globe code: `site/earth/index.html` (three.js UMD 0.152.2 from cdnjs; texture from a video). For
  this page a night is one still, so texture from a JPG per night with a stepper (and a slow
  crossfade between nights, ~1 s) is enough; no video.
- Day side: GeoColor's day half from the same date (GIBS epsg4326 GeoColor, see fetch_earth) so
  the globe has a lit hemisphere, OR just render the night mosaic over a dim Blue Marble with the
  terminator drawn. Start with the second (one source, no blending); the first is the stretch.

## Definition of done
1. `scripts/fetch_night.py`: for each of the last 14 nights, a 4096x2048 equirectangular mosaic of
   the Day/Night Band (NOAA-21 first, gap-fill from NOAA-20 and SNPP where a swath is missing) into
   `data/night/YYYY-MM-DD.jpg`, plus `night.json` with per-night coverage share and the max Kp.
2. `site/night/index.html`: the globe, a night stepper (keys and buttons), the Kp for each night,
   an "aurora" marker on nights with Kp >= 5, and 2-3 sentences of plain English on what the
   picture is (one pass per place around 01:30 local, why the day side is drawn not photographed,
   what the bright things are). Credit NASA/NOAA VIIRS via GIBS.
3. If any night in the window had Kp >= 5, a still crop of the oval on the page; if none, say so and
   name the brightest night by Kp.
4. Committed, pushed, deployed from `main` (check `ps -eo args | grep -c "[v]ercel --prod"` is 0
   first; another detached job may be deploying /earth/ this morning), curl-checked, and LOOKED AT
   with headless Chrome (auto-memory `earthai-browser-check-video-pages`: the extension wedges on
   video pages; puppeteer-core in site/node_modules works).

## Rules
- Work in /Users/dereklomas/sourcelibrary/earthai directly (data/ is gitignored, no worktree).
- Do NOT edit site/earth/, scripts/fetch_earth.py, or scripts/earth_qc.py. Import from them if useful.
- Page style: tokens from site/month/index.html; link the new page from site/index.html's footer
  line that lists the other pages, and from site/earth/index.html's links row ONLY if that is a
  one-line anchor addition (it is the stable page).
- Report back: append a 5-line summary to this file (what shipped, which nights, max Kp seen, what
  is missing), commit, SendMessage the session named "physics-clouds" if it exists (one line).
