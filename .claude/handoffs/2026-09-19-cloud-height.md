# Globe: cloud height as a channel — so the clouds have a top, a shadow, and a kind

## Posture (read first)
Derek looks at https://earthai-scales.vercel.app/globe/ on a retina MacBook and wants the clouds to look like
cloud: a storm's anvil should stand above the deck around it, cirrus should read cold and thin, marine
stratocumulus should sit flat on the sea. Today every cloud is one white sheet lit by sun angle plus a shadow
offset of fixed length. He said "quality and beauty. get on it." He does not want anything invented: the height
is a real measurement (infrared brightness temperature), and the page says so.

## What exists (do not redo)
- `scripts/fetch_clouds.py`: per-satellite fetch into `data/clouds/raw/<sat>/<slot>_{vis,bt}.png` (16-bit, on the
  4096x1504 grid; `load16`/`save16` with `BT_SCALE`), clear-sky compositing, `opacity`, `blend`, `encode`. The
  encode packs opacity into luma 60..190 and optical flow into the chroma planes, with a 16-row calibration strip
  (`Y_LO, Y_HI, CHROMA, STRIP, PATCHES`, `yuv_frame`); the page solves the browser's YUV matrix from the strip.
  Read the encode section and the page's copy pass before you design anything.
- `scripts/cloud_tiles.py` makes the zoomed tiles from the native 2 km bands (`data/clouds/tiles/raw5/goes19/`,
  op5 memmap). Its encode is raw-plane (WebCodecs reads Y and UV directly, tagged tv range). Not yours to change
  in this brief beyond what step 4 says.
- `site/globe/index.html`: `cloudAt(uv, fine)` returns opacity; `main()` lights it (reflectance tone, relief,
  shadow offset `sh`, day/night). Another session is rebuilding the sky, atmosphere, Sun and Moon at the same
  time (brief `2026-09-19-globe-sky.md`); it owns the atmosphere shell, the ground/sea lighting and `sunVec`.
  You own everything about cloud: how it is read, its shadow, its colour by height, its edge. Pull before every
  edit of the page; keep your work in named functions (`cloudHeightAt`, `cloudShade`) so merges are trivial.

## The channel
1. **Height from brightness temperature.** Per frame, for every cloudy pixel: cloud-top height in km from the
   band-13 brightness temperature against a lapse-rate atmosphere: h = (T_surface - BT) / 6.5 K/km, with
   T_surface = the clear-sky reference already computed in `clearsky` (per pixel, per time of day), clamped
   0..16 km. Where opacity < 0.1, height is 0. Thin cirrus reads too low by this method (its BT is a mix of
   cloud and ground); accept that and say so. Write it as one more grey PNG per blended frame
   (`data/clouds/height/<slot>.png`, uint8 = km x 16). Blend across satellites with the same weights as opacity.
2. **Carry it in the SAME clip.** The frame is 4096x1504 + strip. Add the height field at half resolution
   (2048x752) as extra rows below the opacity: frame height 1504 + 752 + 16 = 2272, under the 2304 hardware
   ceiling for level 5.1. Height goes in luma 60..190 like opacity; its chroma rows stay at 128 (no flow needed:
   the page can warp height with the opacity's own flow). Update `yuv_frame`, the manifest (`code.height_rows`),
   and the page's copy pass so the height lands in a second small render target (or the same target's alpha at
   half res: decide, and say which). Measure bytes before and after; expect +15-25%.
3. **Use it in the shader.** (a) Shadow length and offset scale with height (today `sh` is a fixed ~0.65 deg);
   a 12 km anvil throws a shadow several times longer than a 1 km deck at a low sun. (b) Colour: cloud above
   ~8 km slightly cooler and less bright at the top (ice), low cloud warmer and flatter; at the terminator, high
   cloud stays lit after low cloud has gone dark (it is higher, so the sun reaches it longer: compute the
   terminator shift from height, ~1 deg per 10 km, and use it). (c) A cheap relief from the height gradient
   toward the sun, in addition to the existing opacity relief, so anvils have edges. Keep it subtle; Derek said
   beautiful, not stylised.
4. **Tiles (only if 1-3 are clearly good and shipped):** the tile pipeline can write height in the tile's chroma V
   plane instead of flow-y if flow-y proves dispensable, or as a 4th plane; write what it would take in the
   report, do not build it unless time allows.

## Rules
- Repo JDerekLomas/earthai, main, in /Users/dereklomas/sourcelibrary/earthai (data/ is gitignored, not a
  worktree). Pull first. Commit your own paths by name. No GPU box, no accounts.
- The one-day raw data on this laptop is 12 Sep. The ten-day rebuild is running on a Hetzner box (session
  earth-7e's brief `2026-09-18-ten-days-hetzner.md`); design the height step so it slots into `fetch_clouds.py`
  as a normal stage (`height` between `blend` and `encode`) and runs on the ten days when they land.
- Deploy from main: `pgrep -f vercel`, then `cd site && npx vercel --prod --yes`; `site/globe/tiles/` (260 MB)
  deploys from disk, leave it. Curl-check. Look at it: a Caribbean storm's anvil at 17:00Z zoomed, the Chile
  stratocumulus, and the terminator crossing high cloud, each before/after.

## Report back
Append here, numbered: what shipped and the URL; the height method's known errors; clip bytes before/after;
rAF work p95; screenshots' paths; what next. Then SendMessage "earthai-46" one line DONE/BLOCKED + URL.

## Report (2026-09-19, session cloud-height)
1. **Shipped.** `fetch_clouds.py height` (new stage between `blend` and `encode`: `--start --end --sats --hold-min`,
   same hold and view weights as blend, writes `data/clouds/height/<slot>.png` uint8 = km x 16), `encode` carries the
   field when every frame has one (manifest `code.height_rows` 752, `height_km_max` 16, `height_lapse_k_per_km` 6.5,
   `height_min_op` 0.1; a clip without it still encodes and the page still reads it). Clip is 4096x2272 High 5.1
   (2k: 2048x1144). Page: copy pass reads the height rows (left half of the rows, same luma code, bilinear 2x) into the
   cloud target's ALPHA (decided: same target, not a second one — every existing cloud tap gets height for free and
   the warp is shared); shader `cloudOH()` returns (opacity, km), `cloudAt`/`cloudHeightAt` wrap it; `cloudShadow()`
   three probes at 1.2/4/10 km along the sun's ground direction, a probe shades where the cloud there is at least
   that high, and also shades the LOWER cloud beside an anvil; `cloudShade()` colour by height (ice tops cooler,
   -5% brightness above ~8 km; low decks warmer), terminator pushed back by acos(R/(R+h)) (~3 deg at 10 km, not the
   brief's 1 deg: the geometric value); relief from the height gradient added to the opacity relief; low cloud's
   relief x0.75. Shadows drawn at 3x true length (`SHADOW_DRAWN_X`), said on the page. Copy: new table row "cloud
   height", new note "the height" with both known errors, lede updated. Fallback: no height rows -> old fixed shadow.
   URL: https://earthai-scales.vercel.app/globe/
2. **Height method, known errors.** (a) Thin/semi-transparent cirrus reads LOW (BT is a cloud/ground mix): a 12 km
   veil comes out 4-6 km. (b) Standard lapse rate 6.5 K/km vs the real profile: +/-1-2 km, worse in inversions
   (marine stratocumulus under an inversion reads ~1 km when its top is ~1.5). (c) T_surface is the per-time-of-day
   clear-sky reference, so a place never clear in the day inherits the reference's cold bias (reads lower). (d) The
   field is 20 km/px (half res) so a lone tower under 20 km wide is averaged with its surroundings. (e) Held frames
   (Meteosat-9 at 15 min) use the held slot's BT with the current clear-sky reference. Measured on 12 Sep: cloudy
   pixels p50 1.8-2.1 km, p90 ~6, p99 10-11, max 15.9; 1.4% of cloudy pixels above 8 km at 17Z; 0.66% of pixels with
   opacity >= 0.1 got height 0 (no satellite saw cloud there in its own opacity). Browser read-back of the decoded
   alpha: mean 2.6 km on cloud, max 15.2 km, 4.3% of cloud above 8 km (H.264 smears the tall tops a little wider).
3. **Bytes.** clouds.mp4 34,289,766 -> 40,721,177 (+18.8%); clouds_2k.mp4 9,575,049 -> 11,096,143 (+15.9%). Same
   crf 26 / aq 3:1.2 / preset slow; encode 552 s + 589 s on the laptop.
4. **rAF work p95** (headless real Chrome, Metal, 1440x900 @2x, `__dbg.rafStats()`), before -> after:
   playing at the open view 1.0 -> 1.2 ms; Caribbean zoomed 1.0 -> 0.7; Chile 1.3 -> 0.4; terminator 0.9 -> 0.4;
   open paused 0.3 -> 0.3. Well under the 4 ms budget; the extra shadow probes cost nothing measurable.
5. **Screenshots** `docs/globe-2026-09-19-height/{before,after}_{carib_anvil_1700z,chile_stratocu_1700z,
   terminator_carib_2300z,open_view}.jpg` + `before.json`/`after.json` (stats). What changed to the eye: at 17Z the
   Caribbean sun is near overhead, so the heavy dark ring every cloud used to carry is gone and only the tall
   convection casts anything (correct, and quieter than before); at 23Z the Central American towers throw long
   shadows east onto ground and onto the deck beside them and read as tall; the Chile stratocumulus sits flat and
   warm-grey next to cooler, brighter high cloud over the Andes; high cloud near the terminator stays lit a little
   longer than the ground. Not stylised: the shadow probes only darken where a measured top is high enough.
6. **Tiles (step 4, not built).** The tile clips are raw-plane YUV read by WebCodecs, so height can go in the tile's
   V plane in place of flow-y IF flow-y is dispensable: measured flow is p50 0.5 px, p90 1.7 px at 4096 wide (2-4x
   that at tile scale), and cloud motion has a y component about as large as x, so dropping flow-y would visibly
   degrade the warp. Better: a 4th plane, i.e. encode tiles as two clips (op+flow, height alone at half res, crf
   higher) or as one clip with extra rows like the base clip (tile 512x512 -> 512x768 + strip). `cloud_tiles.py`
   would need a `height` step reading the native-band BT against the native clear-sky reference (it has `op5`; it
   would need a `bt5`/`clear5` memmap, ~2 GB for the day), and `tiles.js` a second atlas sampler or an alpha write
   in its copy. About a day; only worth it if the 20 km base field visibly lags the 2.4 km tiles when zoomed.
7. **Ten days.** The stage is a normal CLI command with blend's options; on the Hetzner box run
   `height --start .. --end .. --sats <same list as blend>` after `blend`, then `encode` picks it up. It reads
   `raw/<sat>/*_bt.png`, `clear/<sat>/bt_clear_*.png` and `op/<sat>/`, so none of those may be pruned before it runs.
8. **Next.** Look at it on the real screen; if the 3x shadow reads as too much at the tile zoom, drop
   `SHADOW_DRAWN_X` to 2. Consider a per-frame height p50/p99 in the manifest for the footer. Tiles as in 6 if wanted.
   `/rename cloud-height` could not be run from inside the session (built-in command, not a skill).
