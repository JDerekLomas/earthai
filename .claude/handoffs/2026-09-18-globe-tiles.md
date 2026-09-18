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
