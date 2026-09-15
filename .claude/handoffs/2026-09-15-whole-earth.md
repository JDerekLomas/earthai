# The whole Earth, every ten minutes: a geostationary mosaic on a globe

## Posture (read first)
Derek opens a page on his laptop, sees the Earth, and it is moving: the real atmosphere of the last
weeks, every ten minutes, all of it at once. He drags to turn the globe, or just watches. He is a
designer; the object has to be beautiful before it is anything else, and honest about its gaps
(the poles, the seams). He will look at it for a minute and decide whether this is the thing to build
on. He is not a meteorologist; the page explains itself in plain sentences.

## Goal
A `site/earth/` page: a slowly turning globe (three.js, UMD from cdnjs, pinned version) textured with a
10-minute-cadence equirectangular mosaic of the five geostationary satellites, playable over several
days, plus a "sun-fixed" mode where the terminator stands still and the Earth turns under it.

## Definition of done
1. `scripts/fetch_earth.py`: for a given instant, fetch the full-disk GeoColor (and Band 13) from NASA
   GIBS for GOES-East, GOES-West and Himawari in **epsg4326** (GIBS serves these layers in
   `wmts/epsg4326/best/...`; check the capabilities XML for exact layer ids and tile matrix sets), and
   Meteosat (0 deg and 45.5E) from EUMETSAT's open WMS/WMTS (EUMETView; verify what is free without a
   key), into one equirectangular image ~4096x2048 (about 10 km/px at the equator). Blend overlaps by
   satellite zenith angle (weight = cos of the view angle, falling to zero past ~70 deg). Poles: leave
   the basemap showing, dimmed, and SAY SO on the page.
2. A first archive: 3 days at 10-minute cadence (432 mosaics), then extend if it works; GIBS keeps ~35
   days. Store `data/earth/YYYY-MM-DDTHHMMZ.jpg`; ~1 MB each.
3. Encode as an equirectangular mp4 (or a sprite of frames) the page can map onto a sphere; the page
   plays it as a texture. Both modes: Earth-fixed (the terminator sweeps) and sun-fixed (rotate the
   globe by -15 deg per hour of UTC so noon stays centred).
4. Each source's contribution visible on demand (a "seams" toggle tinting each satellite's zone), so the
   blend is inspectable.
5. Committed, pushed, deployed from `main` (`cd site && npx vercel --prod --yes`), curl-checked, and
   LOOKED AT in a browser (chrome-devtools new_page + take_screenshot). Page copy explains sources,
   cadence, the polar gap and the seams in plain sentences.

## Decisions already made / traps
- GIBS: `scripts/fetch_goes.py` has the layer names for GOES-East/West/Himawari in epsg3857; the
  epsg4326 endpoint is the same layer ids with a different tile matrix set. Full-disk GeoColor from
  GIBS exists for all three. All-day frames: GeoColor at night is an infrared rendering over city
  lights (fine; it is continuous). GIBS returns HTTP 200 with a white wedge for a broken render:
  reject any 256-px tile that is > 5% pure white (see `scripts/goes_qc.py`).
- Meteosat: if EUMETSAT's free tier is not workable within an hour, ship the three-satellite mosaic
  (Americas + Pacific) with Europe/Africa/Indian Ocean from the basemap, clearly labelled, and file
  the gap in the handoff. Do not spend the day on an API key.
- Basemap under the mosaic: NASA Blue Marble (GIBS `BlueMarble_ShadedRelief_Bathymetry`, epsg4326).
- three.js: load the UMD build from cdnjs (pin an exact version); no other CDNs work on this host.
- Work in /Users/dereklomas/sourcelibrary/earthai directly (data/ is gitignored, not in worktrees).
  Commit on `main`. Check `pgrep -f vercel` before deploying; another session may be adding an
  "always day" layer to /month/ at the same time; stay out of site/month/ and scripts/month_sheet.py.
- Style: copy tokens from `site/month/index.html`. Page under `site/earth/`. Keep the mp4 under ~60 MB
  (Vercel and phones); 2048x1024 at crf 30 is fine for a first look.

## Report back
Append a 5-line summary to this file (what shipped, sizes, which satellites are in, what is missing),
commit, and SendMessage the session named "physics-clouds" if it exists: one line, DONE + URL.
