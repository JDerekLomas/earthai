# Globe: regional cloud tiles, so zooming in gets sharper instead of blurrier

## Posture (read first)
Derek is on https://earthai-scales.vercel.app/globe/ on an M5 Pro MacBook (120 Hz, dpr 2), and on a
phone sometimes. He zooms into a storm and expects it to resolve, the way a map does; today it turns
to mush at 10 km/px and he said "I thought it would be higher res". Then: "yeah, do the tiling. can
you build this now?" He is a designer: the zoom must feel like one continuous object getting sharper,
never a pop from one picture to another, never a grid of squares loading at different times, never a
stall. He stays a minute or two; a stutter or a spinning fan ends the visit. Performance is the
feature; measure it, do not assume it.

## What exists (do not redo)
- `scripts/fetch_clouds.py`: per-satellite fetch (GOES byte-range MCMIPF bands 2+13, Himawari HSD,
  EUMETView WMS), clear-sky compositing, opacity, blend, encode. Everything is on ONE 4096x1504 grid
  (10 km/px); the raw per-satellite frames in `data/clouds/raw/<sat>/` are already at that grid, so
  native resolution has to be fetched again (GOES-East 12 Sep was 5.9 GB, ~1.5 h on this link).
- `site/globe/index.html`: one ShaderMaterial for the planet; cloud comes from two render targets
  (A = older frame, B = newer) filled by a copy pass when the video presents; `cloudAt(uv)` samples
  them. A SECOND SESSION is right now adding optical-flow warping (flow packed in the clip's chroma),
  per-second auto-spin and a zoom cap: brief `2026-09-18-globe-motion.md`. Do NOT edit
  `site/globe/index.html` or `fetch_clouds.py` until that brief has a "## Report" section; pull first.
  Build the pipeline and the tile client as separate files meanwhile (`scripts/cloud_tiles.py`,
  `site/globe/tiles.js`), then integrate. If the motion session is still running when you are ready
  to integrate, check `ListAgents`, wait with notify-when-idle, do not race it.
- The parent brief `2026-09-18-cloud-layer-globe.md` has the repo rules (own repo JDerekLomas/earthai,
  main, data/ gitignored, deploy `cd site && npx vercel --prod --yes` after `pgrep -f vercel`, no
  accounts, no GPU box, stay out of site/earth, site/night, site/index.html).

## The design (decided; build this, and say in the report where it was wrong)
1. **Tile scheme.** Equirectangular "geodetic" tiles, 512 px, level L has 2^(L+1) x 2^L tiles over
   the whole lon/lat range (so the base clip is exactly level 3 = 4096 wide; keep the +/-66 crop
   inside tiles as a mask, the scheme itself is whole-sphere). Build levels 4 (8192, ~5 km) and 5
   (16384, ~2.4 km, native for the 2 km infrared and the visible after 2x2 averaging). Level 6 is for
   later (visible only, daytime only) and should be noted, not built.
2. **Opacity at native resolution.** Same clear-sky method, run per satellite on its own window of
   the level-5 grid (do not allocate the whole 16384x6016 field per frame; a GOES disc window is about
   a third of it; work in latitude strips if memory bites; 51 GB RAM here). Keep the reprojected 2 km
   band arrays this time (16-bit PNG per satellite window) so the next satellite or day does not
   refetch. Only tiles a satellite can see (view angle < 66 deg) exist; missing tile = clear.
3. **Format: WebCodecs, not <video>.** Nine tile videos playing in sync with <video> elements drift
   and hit hardware decoder limits on phones. Each tile is one H.264 Annex B stream (`ffmpeg -f h264`,
   yuv420p, keyframe every 12 frames, no B-frames, same grey+flow-chroma packing the motion session is
   shipping, so the planet shader's warp works unchanged) plus one JSON index per level with byte
   offsets of every NAL and which frames are keyframes. The page uses `VideoDecoder`, one per visible
   tile, fed `EncodedVideoChunk`s; seeking to frame i = decode from the previous keyframe. Frame-exact,
   one clock, no drift. Feature-detect; without WebCodecs the page simply stays at the base clip.
4. **Client (`site/globe/tiles.js`).** From camera distance and the canvas size in device px, pick the
   level where one texel ~ one device pixel (the same arithmetic as the zoom cap). Project the view
   frustum onto lon/lat, list the tiles needed (typically 4–9), load their index, fetch bytes by Range
   requests around the current frame (a keyframe group at a time, ~1 s of playback), decode, and write
   each tile's frame into ITS OWN A/B render-target pair. Composite on the GPU into one level-sized
   "virtual texture" for the view: simplest working version is a per-tile draw into a single big A/B
   render target covering the visible lon/lat window, and `cloudAt` samples that window when the level
   > 3, the base clip otherwise, with a cross-fade over ~300 ms when a level becomes ready so it never
   pops. While a tile is not yet decoded, show the base clip upsampled underneath (it is always
   there), never a blank square. Cap live decoders at 9 (12 on desktop), evict by distance from view.
5. **Budget, measured in a real Chrome via the chrome-devtools MCP (`new_page` on the live URL,
   `evaluate_script` on `window.__dbg`):** rAF p95 under 10 ms at 120 Hz while zoomed with 9 tiles
   playing; bytes for a typical zoomed minute; time from zoom-stop to sharp; memory (each 512 tile =
   two 1 MB targets). Also the phone case: the 2k path must be untouched and still 60 fps.
6. **Hosting.** For the proof (one day, GOES-East, levels 4+5 over the Americas: roughly 100–150
   level-5 tiles at ~2–3 MB each per day) static files on Vercel are fine; keep every file under
   50 MB. Ten days and five satellites (~10 GB) is Cloudflare R2 with Range support; `wrangler` is not
   logged in on this machine, so do NOT attempt R2; write what the R2 layout would be in the report.

## Stages
A. Pipeline, GOES-East 12 Sep at level 5, tiles + indexes, a contact sheet of one storm at levels 3/4/5
   side by side in `docs/globe-2026-09-18/` so the gain is visible. (Fetch runs ~1.5 h; write the client
   while it runs.)
B. Client, integrated after the motion session's report lands. Deploy, measure, screenshot the Chile
   cloud streets and a Caribbean storm at maximum zoom, before/after.
C. GOES-West for the same day if A+B are clearly better and the day is not over.

## Report back
Append here: URL, tile counts and bytes per level, the measured budget numbers, what popped or
stalled and how it was hidden, what level 6 and R2 would take. Commit and push (own paths only), then
SendMessage "earth-7e": one line, DONE or BLOCKED + URL.

## Report (2026-09-19, session tiles-finish)
1. **URL.** https://earthai-scales.vercel.app/globe/ — zoom into Chile (`#lon=-75&lat=-25&zoom=0.01`) or the
   Caribbean (`#lon=-62&lat=16&zoom=0.01`). Tiles: `/globe/tiles/L5.json`, `/globe/tiles/L5/<tx>_<ty>.h264`
   (Vercel serves Range: `-r 0-100` → 206, `accept-ranges: bytes`). Before/after crops:
   `docs/globe-2026-09-18/tiles_pair_chile.jpg`, `tiles_pair_caribbean.jpg`; pipeline contact sheets (levels
   3/4/5 side by side) `tiles_levels_chile.jpg`, `tiles_levels_caribbean.jpg`.
2. **Tiles (GOES-East, 12 Sep, 144 slots, new opacity curve vis_k 0.45 / bt_k 26 matching the base clip).**
   Level 5: 112 tiles of 144 in the window, 157.7 MB, 1.41 MB/tile. Level 4: 34 of 36, 58.8 MB, 1.73 MB/tile.
   avc1.640016, keyframe every 12, no B-frames, flow in chroma (p99 23.5 level-5 texels → 4.256 levels/texel).
   Whole tile set 216.5 MB, 146 files, largest 3.6 MB — far under Vercel's 50 MB/file.
3. **Measured, headless real Chrome 1440x900 dpr 2, Metal on the M5 Pro (60 Hz vsync in headless).**
   "Sharp" = ms from the zoom stopping to every tile in view having a frame (`stats.sharpMs`); "bytes" =
   tile bytes fetched from the start of the zoom to sharp; "play" = 4.5 s of zoomed playback so the 240-sample
   rAF ring holds playback only.
   | | level | tiles/decoders | sharp | bytes to sharp | bytes for play | rAF work p95 / max | rAF gap max |
   |---|---|---|---|---|---|---|---|
   | local Chile | 5 | 8 / 8 | 87 ms | 3.29 MB | 6.37 MB | 0.7 / 1.7 ms | 16.8 ms |
   | local Caribbean | 5 | 12 / 12 | 91 ms | 5.63 MB | 8.36 MB | 0.8 / 2.0 ms | 16.8 ms |
   | live Chile | 5 | 8 / 8 | 276 ms | 3.84 MB | 6.56 MB | 1.2 / 3.0 ms | 50 ms |
   | live Caribbean | 5 | 12 / 12 | 328 ms | 5.63 MB | 8.36 MB | 1.7 / 3.1 ms | 50 ms |
   Composite and update passes max 2.4–3.3 ms; decoded frames all NV12 (hardware). Whole live session: 22 tile
   files, 24.4 MB. First present of the base clip 1.9 s from navigation (local 2.1 s). About 55% of decoded
   frames are pre-roll from the previous keyframe (`stats.dropped`) — the price of seeking with GOP 12, not a bug.
   Extrapolated: 8.4 MB per 4.5 s of zoomed playback ≈ 1.9 MB/s ≈ 110 MB for a zoomed minute at level 5 with
   12 tiles; a viewer who zooms into one storm pays 3–6 MB to get sharp and ~2 MB/s while it plays.
4. **What popped or stalled, and how it is hidden.** Nothing in the zoomed-playback window locally (gap max
   16.8 ms = one vsync); live had one 50 ms hitch (three frames) per place, during the fetch of the next
   keyframe group. Hidden by design: a fresh tile set shows the base clip until every tile in view has a
   frame, then fades in over 300 ms; the atlas window is centred on the view and fades at its edge; the
   foreshortened margins (zenith > 58°) fall back to the base clip; a level change fades out then in. Seen in
   the live Caribbean screenshot: a faint vertical seam at the far-left edge of the 1440-wide view at
   maximum zoom, where the atlas window's edge fade meets the base clip — the fade is a little too narrow
   at dpr 2; widen `edge` in `tiles.js`/the shader (follow-up, cosmetic). The ~1 s stall on first paint
   while the 8192 basemap uploads is pre-existing and not the tiles.
5. **Deploy — what actually happened.** Not deployed by this session. While the rebuild finished, the
   cloud-height session deployed straight from the working tree (`cd site && npx vercel --prod --yes`), which
   carried the tile files, HEAD's `tiles.js` and the tile block in its edited `index.html`, and its own
   4096x2272 clip. Live `L5.json` md5 equals the rebuilt local one (da2d8854…), so the live tiles are the
   new-curve set. A second deploy of HEAD would have reverted their page, so I skipped step 3 and measured
   live instead. Incident on the way: my first local "base" screenshots were HEAD's page sampling that
   2272-tall clip (it appeared as a different cloud field) — `scripts/deploy_site.sh` now refuses a
   `globe/clouds.mp4` whose height ≠ `clouds.json` height + strip, and takes `KEEP_LIVE="globe/clouds.mp4 …"`
   to ship the live copies of named assets instead of the working tree's.
6. **Level 6.** Band 2 at 0.5 km, daytime only, visible only (no infrared at that scale, so nothing at
   night and no cloud-top temperature — opacity from reflectance alone with the clear-sky composite). Tiles
   32768 wide, 2^7 × 2^6; over the same window ~450 tiles/day at ~4× the level-5 bytes (~650 MB/day for
   GOES-East). Fetch is the band-2 MCMIPF at native 0.5 km (~4× the 12 Sep 5.9 GB); the opacity step must
   work in latitude strips. Client needs nothing new except `levels:[6,5,4]` and a 24-tile decoder cap.
7. **R2.** `tiles/<sat>/<date>/L<n>/<tx>_<ty>.h264` plus `tiles/<sat>/<date>/L<n>.json`, public bucket
   with Range (R2 supports it), `Cache-Control: public, max-age=31536000, immutable` (files are dated, never
   rewritten). Ten days × five satellites ≈ 216 MB × 50 ≈ 11 GB, ~$0.17/month storage and zero egress. The
   page takes a `tilesBase` (from `clouds.json`) so the host is a config line. `wrangler` is not logged in here.
8. **Follow-ups.** (a) GOES-West's window straddles the dateline and comes out 2.7× too big — make `window()`
   in `cloud_tiles.py` wrap-aware. (b) Tiles for the other four satellites (Himawari, MTG, IODC, GOES-West).
   (c) Phones: level 4 only, untested. (d) The edge seam above. (e) The first-paint basemap stall.
   (f) Cross-session deploys: two sessions now deploy the same page from the same checkout; whoever deploys
   next should use `scripts/deploy_site.sh` (HEAD + assets, with the clip-height guard) once the height
   page is merged, not the working tree.
