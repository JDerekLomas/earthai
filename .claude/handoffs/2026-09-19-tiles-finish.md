# Finish the tile layer: deploy, measure, report (continuation of 2026-09-18-globe-tiles.md)

## Where it stands (2026-09-19 ~01:40, session earthai-46 signing off)
Everything is built and committed except the deploy and the report. Read `2026-09-18-globe-tiles.md` for the
design; this file is what is left.
- Code on main: `scripts/cloud_tiles.py` (fetch/opacity/flow/encode/sheet), `site/globe/tiles.js` (WebCodecs
  client), the tile block in `site/globe/index.html` (commit bf41b9e, rebased onto the globe-look merge 0423cf4),
  `scripts/deploy_site.sh` (deploys HEAD's tracked site/ plus all gitignored assets from a clean copy, because
  other sessions edit this checkout concurrently).
- Data: `data/clouds/tiles/raw5/goes19/` all 144 slots of 12 Sep at native 2 km (3.6 GB). A REBUILD of op5 →
  flow → encode → sheets on the new opacity curve (vis_k 0.45 / bt_k 26, matching the base clip the globe-look
  session shipped) is running: `data/clouds/tiles/pipeline2.log`, ends with `PIPELINE_DONE`. It rewrites
  `site/globe/tiles/L5/*.h264`, `L4/*.h264`, `L5.json`, `L4.json` and `docs/globe-2026-09-18/tiles_levels_*.jpg`.
  Do not deploy before it says PIPELINE_DONE (the old tiles are on the old curve and would not match the base).
- Measured on the OLD-curve tiles, local build, headless Chrome 1440x900 dpr 2 (`scratchpad` of the old session
  is gone; the scripts are described below): level 5 engages over Chile and the Caribbean, sharp 150–200 ms after
  the zoom stops, 3 MB fetched to first sharp view, ~8 MB after 3 s of zoomed playback, rAF work p95 1.5–2.5 ms,
  worst 8 ms; 6–8 tiles live, hardware decode (NV12). Two real bugs were found and fixed on the way: streams tagged
  full range come out of the Mac hardware decoder squeezed into 16..235 (fixed: tag tv), and the view measurement
  used a stale camera matrix for one tick after a zoom (fixed: `cam.updateMatrixWorld()` first).
- Other sessions: `cloud-height` (background, brief `2026-09-19-cloud-height.md`) is editing `fetch_clouds.py`
  and `index.html` in THIS checkout right now — that is why `git status` shows them modified; never commit or
  revert their files. `earth-7e`'s Hetzner session is fetching the ten days. The sky brief
  `2026-09-19-globe-sky.md` is written and committed but NOT dispatched: dispatch it after the tile deploy.

## Do, in order
1. Wait for PIPELINE_DONE (`until grep -q PIPELINE_DONE data/clouds/tiles/pipeline2.log; do sleep 30; done`).
   Check `grep -E "^flow:|^level" data/clouds/tiles/pipeline2.log` (expect ~112 L5 tiles ~185 MB, 34 L4 ~70 MB).
2. Serve locally: `npx http-server site -p 8791 -s` (NOT python's server: no Range support, video cannot seek).
   Probe with puppeteer-core (`/Users/dereklomas/node_modules/puppeteer-core`, Chrome at
   `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, args `--autoplay-policy=no-user-gesture-required
   --use-angle=metal --enable-gpu --ignore-gpu-blocklist`, viewport 1440x900 dpr 2): load, wait
   `__dbg.presented()>3`, `__setView(-75,-25,8,118)`, wait 1.2 s, `__pause()`, `__setView(-75,-25,0.01,null)`,
   wait for `__dbg.tiles.stats.on>=1`, read `__dbg.tiles.stats` (sharpMs, bytes, level, decoders, err), set
   `window.__mix=0.5`, screenshot; then force `U.uTileOn` to 0 (defineProperty on `.value`) and screenshot the
   base for the pair. Same for the Caribbean (-62, 16, frame 100). Then play 3 s zoomed and read
   `__dbg.rafStats()` (work p95 must stay under 4 ms). Crop the centre of each pair into
   `docs/globe-2026-09-18/tiles_pair_chile.jpg` and `tiles_pair_caribbean.jpg`.
3. Deploy: `git branch --show-current` = main, then `sh scripts/deploy_site.sh` (it refuses if a vercel deploy is
   running). Curl-check `https://earthai-scales.vercel.app/globe/tiles/L5.json` (200) and one tile with
   `-r 0-100` (expect 206). Re-run the Chile probe against the live URL and record the numbers.
4. Commit and push: `docs/globe-2026-09-18/tiles_*.jpg`, `scripts/deploy_site.sh` if not yet, this file with the
   report. Own paths by name only.
5. Report: append to `2026-09-18-globe-tiles.md` a numbered `## Report`: URL, tile counts and bytes per level,
   the measured numbers above (local and live), what popped or stalled and how it is hidden (the atlas window is
   centred on the view and fades at its edge; foreshortened margins fall back to the base clip; a fresh tile set
   shows the base clip until every tile has a frame, then fades in over 300 ms), what level 6 and R2 would take
   (level 6 = the 0.5 km band 2, daytime only, ~4x bytes; R2 layout `tiles/<sat>/<date>/L<n>/<tx>_<ty>.h264` with
   Range, indexes per level per day), and follow-ups: GOES-West's window straddles the dateline and is 2.7x too
   big (make `window()` wrap-aware), tiles for the other four satellites, phones (level 4 only, untested), the
   ~1 s stall on first paint when the 8192 basemap uploads (pre-existing, not the tiles).
6. Dispatch the sky session: from the repo root,
   `claude --bg "Read /Users/dereklomas/sourcelibrary/earthai/.claude/handoffs/2026-09-19-globe-sky.md and execute it to done. Work in /Users/dereklomas/sourcelibrary/earthai on main (cd there). First run /rename globe-sky. Pull main before every edit of site/globe/index.html; the cloud-height session edits the same page. When done, SendMessage the session named 'earthai-46' (or, if gone, append to the brief): one line, DONE or BLOCKED + URL."`
7. SendMessage `earth-7e` one line: tiles DONE + URL. If earthai-46 is gone, this file is the report.
