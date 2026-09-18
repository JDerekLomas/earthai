# Globe: the clouds must look like cloud, not paper cutouts

## Posture (read first)
Derek, opening https://earthai-scales.vercel.app/globe/ on his M5 Pro (120 Hz, dpr 2) AFTER the
motion fix shipped: "still jaggedy when i open it.. and when i zoom, super low res". LOOK at
`docs/globe-2026-09-19/look_open_m5pro.jpg` and `look_zoomcap_m5pro.jpg` before anything: they are
screenshots from a real Chrome on his machine at the default view and at the zoom cap. The render
loop is clean (120 fps, p95 8.6 ms) and the flow warp works; what he is seeing is the LOOK of the
cloud field. He is a designer. The reference in his head is a real satellite picture: tonal, soft
edged, with volume. What he gets is a near-binary white lace with hard 10 km stair-step edges and
black holes between the cloud streets, plus faint dark rectangles in the near-clear ocean.

## Diagnosis (done; do not redo)
1. **Tone.** Opacity = 1 - exp(-x/k) with k = 0.26 reflectance / 16 K pushes most cloud to 1.0, and the
   shader maps opacity straight to a flat white (`cloudDay`), so a cloud has two values: on and off.
   Thin cloud, cloud edges and stratocumulus texture all vanish into the same white.
2. **Edges.** Bilinear magnification of a near-binary field shows the texel grid as staircases; at
   the cap one texel is ~2 device px (measured from the geometry: radius_px = H/2 / (d tan 15 deg),
   px per texel = radius_px * 2pi/4096; at d = 1.946, H = 1322 that is 1.9), and the brief asked for
   1.25. The report's "2 CSS px" was off by two.
3. **Blocks.** crf 26 with luma squeezed into 60..190 leaves macroblock rectangles in the clear ocean
   (opacity 0.0-0.1 is where the codec spends nothing and the eye looks).

## Fixes (in this order; deploy after 1+2+4, then 3 if time)
1. **Shader (`site/globe/index.html`) — most of the win, no re-encode needed:**
   - tonal cloud: brightness follows opacity (thin = dimmer and slightly bluer, not just more
     transparent); a gentle contrast curve on `cl` for display (e.g. smoothstep(0.02, 0.9, cl) then
     pow 0.8), tuned by eye against the old GeoColor at the same view (/earth/, frame 97/961 pair in
     `docs/globe-2026-09-18/chile_globe_over_earth.jpg`).
   - volume: a cheap lit look from the opacity gradient — sample cl at +/- one texel in the sun's
     ground direction (`st`), treat the difference as a slope, brighten the sun-facing side, darken
     the far side, scale by `day`. Two extra taps; keep the existing ground shadow. This is what makes
     cloud read as a surface with height instead of paint.
   - bicubic (Catmull-Rom, 4 bilinear taps) sampling of the cloud texture when magnified, so the
     texel grid never shows; keep the warp path.
   - zoom cap at 1.25 device px per texel, computed from the formula above (canvas height in device
     px, fov, clip width); ease into it as now. Yes, that allows only ~1.4x zoom from the default on a
     retina screen. Say so in the copy in one sentence; the tiles brief is the real answer.
2. **Pipeline (`scripts/fetch_clouds.py`) — softer curve, re-encode the day (~3 min):**
   k = 0.45 reflectance / 26 K (halve the steepness), and keep opacity from clipping: cap the curve
   so 0.6 reflectance -> ~0.85, not 1.0. Check marine stratocumulus off Chile is still there (the
   earlier linear ramp lost it; the fix is tonality, not threshold).
3. **Encode:** `-aq-mode 3 -aq-strength 1.2` (or crf 22) so the dark end gets bits; compare bytes and
   the ocean blocks at 1:1; a few MB more is fine for the 4k clip. Keep the chroma flow packing and
   the calibration strip exactly as they are.
4. **Look, in a real Chrome (chrome-devtools MCP, `new_page` on the deployed URL, screenshot via
   `take_screenshot` with `filePath` under this repo), at the same two views as the reference shots:**
   default, and `__setView(-75,-25, __dbg.minDist(), 97)`. Put before/after in
   `docs/globe-2026-09-19/`. Keep rAF p95 under 10 ms at 120 Hz (`__dbg.rafStats` or the sampling loop
   the motion report used).

## Coordination
Session `earthai-46` (Derek's interactive window) is integrating WebCodecs tiles into the SAME
`site/globe/index.html`. Work on a branch `globe-look` in a worktree, commit small, and before you
merge: `git fetch`, rebase onto main, and SendMessage `earthai-46` one line: "globe-look is rebased
and about to merge, touching cloudAt() and the encode; shout now if mid-edit". Wait up to 10 min for
a reply; then merge, deploy (`pgrep -f vercel` first; `cd site && npx vercel --prod --yes`), curl-check.
Rules as in `2026-09-18-cloud-layer-globe.md`: no accounts, no GPU box, stay out of site/earth,
site/night, site/index.html.

## Report back
Append here: what changed in the curve and shader with the numbers, before/after plate paths, bytes,
p95. SendMessage "earth-7e" one line DONE/BLOCKED + URL.
