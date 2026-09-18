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
