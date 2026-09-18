# Globe: real motion between the ten-minute frames

## Posture
Derek opened https://earthai-scales.vercel.app/globe/ on his M5 Pro (120 Hz, dpr 2), zoomed in, and
said "sort of jerky?". Measured in his Chrome: the render loop is clean (120 fps, worst frame 9.4 ms,
no dropped rAF, presents every 83 ms). The jerk is the CONTENT: 12 real frames/s, ten minutes apart,
and the Blend is a dissolve, not motion, so a cloud that moved 3 texture px fades between two
positions. Zoomed in that is 10+ screen px pulsing at 12 Hz. The fix is motion, not more frames.

## Approach: warp with optical flow in the shader (preferred over RIFE)
Ship a low-res flow field per frame and let the planet shader warp frame A forward and frame B
backward by uMix*flow before blending (the standard two-way warp; ghosting only where flow is wrong).
Arbitrary smoothness at any playback rate, works for ten days without 4x the frames, and decode cost
does not change.
1. Flow: OpenCV DIS (`cv2.DISOpticalFlow_create(PRESET_MEDIUM)`) between consecutive opacity PNGs in
   `data/clouds/op/` at 1024 wide, forward flow A->B. Report the 99th-percentile magnitude in texture
   px at 4096 (expect ~2–8 px). Handle the seam at lon 180 (wrap) and the held frames (zero flow).
2. Carry it in the SAME video, no second file to keep in sync: the clip is grey, so the U and V chroma
   planes are empty. Write raw yuv420p frames yourself (Y = opacity as now, U = flow x, V = flow y,
   centred on 128, scaled so the 99th percentile hits +/- 100 levels) and encode with
   `-f rawvideo -pix_fmt yuv420p` in, `-pix_fmt yuv420p -color_range tv -colorspace bt709
   -color_primaries bt709 -color_trc bt709` out, so the browser's YUV->RGB is known. In the copy pass
   invert BT.709 limited-range RGB->YUV and write Y to .r, U to .g, V to .b of an RGBA8 target
   (replaces the R8 target). MEASURE the round trip first: encode a synthetic frame with known U/V
   ramps, read back in the browser, report max error in levels; accept if <= 3 of 256. If the round
   trip is unreliable (Chrome may tag differently on Mac), fall back to a lossless WebP atlas of flow
   tiles per day (512x188x2 per frame, ~5–10 MB/day) indexed by frame number.
3. Shader: `cloudAt(uv)` becomes: fa = flowA(uv) ; a = A(uv - fa*uMix) ; b = B(uv + fb*(1-uMix)) where
   fb is B's own flow (the next frame's forward flow is unknown at B, so use A's flow as an estimate:
   b = B(uv + fa*(1-uMix))); mix by uMix. Do the same for the shadow sample. Keep Blend as the toggle
   (off = step, on = warp); add nothing to the UI.
4. Also: the auto-spin is per rAF tick (`spin+=0.00045`), so on a 120 Hz screen it turns twice as
   fast as designed; make it per second of wall clock. Same check for the momentum decay (0.94/tick).
5. Zoom cap. Derek, zooming in: "maybe dont let me zoom to where it looks so blurry... I thought it
   would be higher res". The clip is 4096 px around the equator, 10 km/px; the 8192 basemap under it
   is twice as sharp, which makes the cloud look worse than it is. Cap `dist` so one cloud texel is
   never larger than ~1.25 device px at the disc centre: at the current geometry the visible
   half-disc is 2048 texels across, so the minimum dist follows from the canvas height in device px,
   the 30 deg fov and the clip width (compute it, do not hardcode; recompute on resize and when the
   2k clip is chosen). Ease the wheel/pinch into the cap rather than clamping hard. Say the
   resolution in the page copy in one plain sentence ("ten kilometres per pixel; the satellites see
   finer, this page does not yet"). The real answer to "higher res" is regional tiles streamed when
   zoomed (the satellites resolve 0.5–2 km); note in the report what that would take, do not build it.
6. Measure again in a real Chrome (chrome-devtools MCP `new_page` + `evaluate_script` on
   `window.__dbg`): rAF p95 and max must stay under 10 ms at 120 Hz with the warp in; clip bytes
   before/after; and screenshots zoomed on the Chile cloud streets at uMix 0.5 with Blend on and off.

## Rules
Same repo rules as the parent brief (`2026-09-18-cloud-layer-globe.md`): work on main in
/Users/dereklomas/sourcelibrary/earthai, do not touch site/earth, site/night or site/index.html, no GPU
box (DIS runs on the CPU in minutes for 144 frames), deploy with `cd site && npx vercel --prod --yes`
after `pgrep -f vercel`, curl-check, look at it. Append a short numbered report here and SendMessage
"earth-7e" one line DONE/BLOCKED + URL.
