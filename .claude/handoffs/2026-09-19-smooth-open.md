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

## Report (2026-09-19, session smooth-open) — DONE, live at https://earthai-scales.vercel.app/globe/ (commit 3860d3e)

**What changed** (`site/globe/index.html`, the loading-order section and the tick's spin line only):
- `streamBase(url)`: the big basemap is fetched, decoded off the main thread with `createImageBitmap(blob, {imageOrientation:'flipY'})`,
  an empty `SRGB8_ALPHA8` texture of the image's size is allocated with `texStorage2D`, and **32 bands of 8192x128 (4 MB)** go up with
  `texSubImage2D`, **one per frame** (0.6–1.5 ms each, ~0.55 s in all), never during a drag; then one `generateMipmap`, one frame's
  grace, and the swap. The raw GL texture is wrapped in a `THREE.Texture` whose `__webglTexture` is set through
  `renderer.properties.get(tex)` — its `version` stays 0 so three binds it and never uploads; sampler params (mipmap linear, repeat/clamp,
  anisotropy 8) are set by hand. Without WebGL2 or `createImageBitmap`, or on any error, the plain loader as before.
- The opening: the canvas fades up over 500 ms at first paint (inline style, set before the 2048 basemap arrives); `spinRamp()` keeps
  the globe still until the video presents its first frame, then eases 0→1 over 1.5 s (tick multiplies the spin rate by it);
  the big basemap starts 500 ms after `playing` (6 s fallback); `later(fn)` queues the tile start (and, via `window.__later`, the sky's
  stars/Moon) one per idle period after the basemap is in. The "lighting the Earth… / streaming N MB of cloud…" line is still the only
  loader and still fades at the first video frame.
- `perf.T0`, `perf.firstMove`, `perf.baseStream` {alloc, bands[], mip} added to `window.__globe` for measuring.

**Gap log, live page, headless Chrome 1440x900 @2x Metal, rAF gaps in the first 15 s** (times relative to script start `T0`):
| | first paint | video 1st frame | first moving frame | 8192 in | gaps > 40 ms |
|---|---|---|---|---|---|
| before ×3 (this morning) | 69–157 ms | 1,078–1,827 | 1,178 (handoff) | 2.3–7.4 s, then a freeze | **550–620 ms** at the basemap's first use (one `texSubImage2D`: main-thread JPEG decode + 128 MB upload); 50 ms at the video's first frames in 2 of 3 |
| after ×3 (deployed) | 68–70 ms | 2,064–2,259 | 2,046–2,240 (= the video's first frame, by design) | 5.3–5.5 s, no freeze | **none after first paint**; one 50 ms in the first-paint frame itself in 2 of 3 (the 2048/poster/coverage/lights uploads, before the fade-up, globe still) |

Over 25 ms after the first second: only a 33 ms at ~7.7 s in two runs (the tile indexes starting in idle time). `__dbg.rafStats()` steady
state unchanged (p95 gap 16.7, work p95 0.6 ms). The video start hitch did not appear in any after run. The first moving frame is later
than before because (b) ties it to the video's first frame; the globe now sits still under the poster cloud until the cloud is real.
The streamed texture is pixel-identical to the old one: row readback at 15 latitudes matches, and clouds-off screenshots at three views
(whole globe, US west at 1.3, Antarctic at 1.3) differ by 0.0 mean. Scripts: `$CLAUDE_JOB_DIR/tmp/{gaps,base,look,texread}.cjs` of job 258cf20c.

**Traps found on the way** (also in auto-memory `earthai-globe-gpu-upload-traps`):
- The 367–400 ms "constant" gap at ~3.5 s in every before run was **my logger's mid-run screenshot** stalling the page, not the site.
  Never screenshot inside a timing window.
- ANGLE Metal: `texSubImage2D` into `SRGB8_ALPHA8` is ~1 ms up to 8 MB a call and **45–98 ms at 16 MB** (RGBA8 stays 2–4 ms at any size);
  ImageBitmap sources trip it at a lower size — hence 128-row bands, not 512.
- `UNPACK_FLIP_Y_WEBGL` is **ignored for ImageBitmap sources**; `imageOrientation:'flipY'` at `createImageBitmap` is honoured.
- The basemap JPEGs are **2:1 (8192x4096)**, not square — a square allocation put the map in the lower half and the north black.
- Headless Chrome's `navigator.connection.effectiveType` flips to `3g` under load, so the page picks the 2k clip and the 4096 basemap
  in some runs; pin it with `--force-effective-connection-type=4G`.
