# Globe: a smooth open — no freeze in the first fifteen seconds, and an opening that is composed

## Posture (read first)
Derek opened https://earthai-scales.vercel.app/globe/ this morning and said "maybe it needs a loader or
something, so it doesn't look so unsmooth when it first opens." He is on a retina MacBook. The globe appears at
once and starts turning, then it stutters. A designer notices a stutter in the first five seconds more than
anything else on the page, because that is when he is deciding whether the thing is good. He suggested a
loader; the better answer is to remove the freeze and to compose the first seconds so that nothing heavy lands
while the globe is moving. A loader is acceptable only as a quiet state, never a spinner or a bar.

## Measured on the live page (headless Chrome 1440x900 dpr 2, 2026-09-19 ~10:30; rAF gaps over 40 ms in 9 s)
- first paint 70 ms (2048 basemap + poster); first moving frame 1,178 ms; the 8192 basemap JPEG decoded at 4.7 s
- a 50 ms gap at 1.65 s (the video's first frames)
- a **567 ms freeze at 6.9 s**: the 8192x8192 basemap uploaded to the GPU in one `texImage2D` (plus mipmaps) on
  its first use. That is the stutter he saw. It was there before the tiles and the height work (measured last
  night as ~950 ms on the local build).
- `window.__globe` has `firstPaint`, `videoFirstFrame`, `baseLoaded`, `base`; `__dbg.rafStats()` gives the recent
  rAF gap/work percentiles and max. Reproduce with puppeteer-core headless (`/Users/dereklomas/node_modules/
  puppeteer-core`, Chrome at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, flags
  `--use-angle=metal --enable-gpu --ignore-gpu-blocklist`), logging every rAF gap over 40 ms with its time since
  navigation via `evaluateOnNewDocument`.

## Do
1. **Stream the 8192 basemap onto the GPU in bands.** Create the 8192 texture empty (or from the 2048 one
   upscaled) and fill it with `texSubImage2D` in 16 bands of 8192x512 over successive frames, one or two bands per
   frame, from `createImageBitmap(img, 0, y, 8192, 512)` slices (decode off the main thread). Generate mipmaps
   once at the end (or ship a 4096 mip chain and skip the top level's mips if that is still visibly a stall:
   measure). Swap it in only when complete, with no flash. Three.js 0.152 UMD: get the GL texture via
   `renderer.properties.get(texture).__webglTexture` after a first tiny upload, or manage a raw WebGL texture and
   wrap it in a `THREE.Texture` whose `__webglTexture` you set; say which you did. Budget: no rAF gap over 25 ms
   from the upload.
2. **Compose the opening.** (a) The stage fades up from the page background over ~500 ms at first paint, globe
   still, cloud from the poster. (b) The globe begins to turn only when the video has presented its first frame,
   easing from 0 to the cruising spin over ~1.5 s, so the first frames of video are never on a moving globe.
   (c) The 8192 basemap streams in AFTER the video is playing steadily (it already waits for `playing`; keep that,
   add a 500 ms grace) and never during a drag. (d) The tile indexes and any sky assets (the sky session is
   adding stars/Moon/atmosphere in the same page) load after that, in idle time (`requestIdleCallback` with a
   timeout), never all in the same frame. (e) The "lighting the Earth…" line stays as the only loader: it fades
   out when the first video frame presents. No spinner.
3. **Measure and prove it**: run the gap logger on the deployed page three times; report every gap over 40 ms in
   the first 15 s (target: none over 40 ms after first paint; the video start hitch may remain if it is Chrome's
   own decoder start, say so). Also confirm first paint and first moving frame did not get later than today.

## Rules
Repo JDerekLomas/earthai, main, in /Users/dereklomas/sourcelibrary/earthai (cd there; not a worktree). Two other
sessions edit `site/globe/index.html` right now (globe-sky, per-day structure): pull before every edit, keep
your change inside the loading-order section and the tick's spin line, commit small, push immediately, and
deploy with `sh scripts/deploy_site.sh` (read its header). Do not touch `tiles.js`, `cloudAt`, the shaders, or the
pipeline. Curl-check and look at a screenshot of the opening frame after deploy.

## Report back
Append here: what changed, the gap log before/after, first paint / first moving frame before/after. SendMessage
'earthai-46' one line DONE/BLOCKED + URL.
