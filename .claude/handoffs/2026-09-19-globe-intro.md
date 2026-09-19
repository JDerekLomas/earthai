# Globe: the arrival — a cinematic opening that uses the load time instead of hiding it

## Posture (read first)
Derek opens https://earthai-scales.vercel.app/globe/ on a retina MacBook, often in a dark room. Since commit
a032f76 the page holds still for ~6 s while the big basemap streams to the GPU and the first day buffers, then
starts playing smooth. He looked at that and said: "can have a dramatic intro during load?" So: the six seconds
become an approach. He is a designer with a film sense; he will know at once if it is a screensaver or an
arrival. The bar is the opening of a good planetarium show or the first shot of Earth in a film: slow, quiet,
inevitable, with one moment (the sunrise over the limb) that makes you hold your breath. No text over it except
the existing quiet line, no logo, no music. It must be beautiful on the first frame and it must never stutter,
because the whole point of the hold was smoothness. Everything on screen stays real or computed as labelled.

## What exists (do not redo)
- `site/globe/index.html`: the planet ShaderMaterial, the back-face atmosphere shell, `sunVec` per UTC, the
  camera at `dist` (4.3 at rest, capped by `minDist`), `spin`/`tilt`, the poster cloud (`posterOn`), `spinRamp()`
  easing the auto-spin in from the first video frame, and the opening sequence just above the `V.forEach(...error`
  block: `READY_S`, `READY_CAP_MS`, `baseReady`, `vidReady`, the `Promise.race` that flips `wantPlay` and calls
  `sync()`/`drainLater()`, `perf.openReady`. `window.__globe` carries the timings; `__dbg.rafStats()` the frame
  timing. Test with puppeteer-core headless as in `2026-09-19-smooth-open.md` (its gap logger: every rAF gap
  over 40 ms with time since navigation), and with screenshots at chosen moments of the intro.
- The sky session (`2026-09-19-globe-sky.md`) is adding real stars, the Sun disc, the Moon and a scattering
  atmosphere in the same page. **The intro belongs to that session if it is still running** (check `ListAgents`
  for `globe-sky`): the arrival is far better with stars and a real limb than without. If it is running, send it
  this brief and stop. If it has finished, do the intro yourself on top of its work, pulling first.

## The arrival (build this; say in the report where you departed and why)
Duration = however long `openReady` takes (typically 5–7 s on a laptop), with a floor of 4 s so a fast cache
still gets the shot, and the cap of 8 s as now. The choreography is driven by a single 0..1 progress `p` that
is time-based but re-fitted to the actual ready moment: ease the camera along the path with a duration guess of
6 s; when `openReady` fires, retarget the remaining path to finish in max(0.8 s, remaining) so it never snaps.
1. **Start far and dark (p=0).** Camera at dist ~14, the Earth a small disc, the night side toward us with the
   terminator just visible on one limb, city lights on, cloud from the poster (already real). Exposure low: the
   scene starts at ~35% brightness. If the sky session's stars exist, they are there from frame one; if not,
   plain black.
2. **Approach (p 0→0.8).** Dolly in on an ease-in-out curve from 14 to the rest distance 4.3 while the planet
   turns slowly so the terminator sweeps toward the viewer. Exposure rises to 100% by p=0.6. The atmosphere rim
   is the thing that sells this: as the disc grows the blue rim should thicken on the lit side and the
   terminator's warm band should come into view. If the sky session's scattering shell is in, use it; if not,
   the current fresnel rim, slightly boosted during the approach only.
3. **The sunrise (p ~0.55–0.75).** Time the rotation so that the Sun clears the limb during the approach: a
   bright point on the edge that blooms briefly into the sunlit hemisphere sliding into view. Do not fake a
   lens flare bigger than a few pixels of glow; the drama is the lit hemisphere arriving, not a JJ Abrams flare.
   The sun direction is the frame's real UTC, so choose the starting `spin` (camera longitude) from the sun's
   position for the day's first frame such that the sunrise lands at p≈0.65: compute it, do not hardcode.
4. **Settle (p 0.8→1).** The camera arrives at 4.3, tilt eases to the resting 0.25, rotation eases into
   `spinRamp`'s cruising rate, exposure at 100%, the quiet line fades, and the first real frames of cloud begin
   moving on the exact frame playback starts (the swap from poster to video is already seamless). The viewer
   should not be able to say where the intro ended and the page began.
5. **Interruptible and skippable.** Any pointer/touch on the stage during the intro ends it: the camera eases to
   rest in 0.6 s and playback begins when ready. A `#nointro` hash (and `prefers-reduced-motion`) skips it and
   uses today's still hold. Never play the intro on a same-session reload (sessionStorage flag) or on a day
   switch; only on a cold open.
6. **Performance.** rAF work p95 under 4 ms throughout; no gap over 40 ms after first paint (the basemap
   streaming already runs in bands). On phones the same intro, shorter (floor 3 s), exposure ramp the same.

## Rules
Repo JDerekLomas/earthai, main, /Users/dereklomas/sourcelibrary/earthai (not a worktree). Pull before every
edit of `site/globe/index.html`; two sessions may be in it. Keep the intro in its own named functions
(`introState`, `introTick`, `endIntro`) called from the tick, with one guard, so it can be removed by deleting
one block. Deploy with `sh scripts/deploy_site.sh`. Look at it: screenshots at p = 0, 0.3, 0.65 (the sunrise),
0.9, 1.0 into `docs/globe-2026-09-19/intro_*.jpg`, and a 12-frame contact sheet of the whole thing. Commit the
sheet. If it is not beautiful in the sheet, iterate before reporting.

## Report back
Append here: what the shot is, the numbers (intro duration on a warm and cold load, rAF work, gaps), the
sheet's path, what you'd change. SendMessage 'earthai-46' one line DONE/BLOCKED + URL.
