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

## Report (2026-09-19, session globe-sky — built on top of the sky, commit c6e800f)
**The shot.** Frame one: a small dark Earth from 14 radii, its night side to us, a thin ring of lit air and a spark of
forward-scatter on the right limb where the Sun is just behind it; the real stars and, as they arrive, the Milky Way. The
camera dollies in on an ease-in-out to 4.3 while the exposure comes up from 35% to 100% (the stars hold, they dim only as
exposure^0.3). From p 0.25 the planet begins a westward turn of 16 deg and the camera cranes up 10 deg, both on a sin^2 hump
whose rate is zero at both ends; at p 0.59 the Sun's disc clears the RIGHT limb — the shell's Mie flare, the corona, and a
brief overshoot of the veil (`bloom` in skyTick) — and settles about 5 deg off the limb as the disc grows to rest. At p 1
playback starts on that frame; the day's own motion (2 h/s: the Sun crosses the sky at 30 deg/s) carries the Sun further
right and the lit hemisphere slides in from that side within the next five seconds, so the arrival's sunrise is the day's.
Plates: `docs/globe-2026-09-19/intro_p0.jpg`, `_p0_3`, `_p0_65`, `_p0_9`, `_p1`, the sheet `intro_sheet_12.jpg`, the phone
sheet `intro_sheet_phone.jpg`, and `intro_after_1s_of_playback.jpg` (the handoff).

**Where I departed, and why.**
1. *The start latitude is the antisolar latitude, not a free tilt, and the rest tilt is that plus 10 deg, not 0.25.* From 14
   radii the Earth hides only a 4 deg cone, so the Sun is behind it at p=0 only if the camera sits at minus the Sun's
   declination (7 deg S in September). And turning the planet under a fixed camera moves the Sun across the sky at the same
   rate, so the brief's "lit hemisphere sliding in" by rotation would sweep the Sun across a 30 deg frame in two seconds (a
   38 deg version did exactly that; sheet v4, not kept). The turn is small (16 deg) and the crane small (10 deg); the day's
   own 30 deg/s does the sliding-in. Net: the arrival rests near the equator in September (further north in winter).
2. *The turn is westward* (the Sun rises on the right): measured, the day's motion carries the Sun to the right in this
   view, so a left-limb sunrise (my first version) had the Sun set again behind the Earth a second after playback began.
3. *The dolly runs to p 0.88, not 0.8*: with 0.8 the last second stood still.
4. *No cruise during the arrival*: the hump's rate is zero at p=1 and the 1.5 deg/s cruise starts with playback; against the
   day's 30 deg/s it is invisible, and it let the solver find a rise at 0.59 instead of 0.23.

**The numbers** (headless Chrome, Metal, 1440x900 @2; the clip from R2, which never buffers 15 s inside the cap in headless,
so `openReady` here is always the 8 s cap): cold open — first paint 176 ms, arrival 171 → 8,923 ms (the crawl at p 0.96 waits
~2.4 s for the cap; on the laptop where the opening is ready at ~6 s the arrival ends at ~6.5 s with no wait); warm (same
context, HTTP cache) — the same, because the video is the gate. rAF work p50 0.2 / p95 0.5–0.7 ms. Gaps over 40 ms after
first paint: one, 50 ms, at the video's first present (the 2x 4096x2272 render-target allocation + calibration readback in
`present()`, pre-existing; not touched). Warm run: one 133 ms gap at 170 ms, before first paint (script start). Phone
(390x844 @3): the same arrival, floor 3 s, no gaps, p95 0.5 ms. Interrupt: a click at p 0.31 ended it in 0.6 s at rest,
paused until ready, then played. Reload in the same session: no arrival (`__intro.state.wanted=false`, dist 4.3).

**Mechanics.** `introState` / `introTick` / `endIntro` / `interruptIntro` / `introReady` in one block before the loop; hooks are
one line each in `firstPaint` (waits for the manifest and, up to 1.5 s, the stars, so frame one is the arrival's frame one),
the ready handler (`introReady()`), `spinRamp` (1 after the arrival), `tick`, `pointerdown`, `wheel`, `setView`. The starting
spin is solved (`solveSpin0`: 720 candidates x 100 p) from the day's first frame's sun; `perf.intro` carries start/end/
sunRiseP/interrupted; `__intro.setP(p)` freezes it for plates. The canvas is now opaque (`setClearColor(0x04090f)`) so the page
background never shows through before the Milky Way loads.

**What I'd change.** (a) On a portrait phone the Sun lands beyond the right edge (the Earth at 4.3 already overflows the
width there) — a portrait rest distance of ~5.5 would show the sunrise; that is the phone's framing, not the arrival's.
(b) The 50 ms gap at the first present could go: allocate the two render targets from the manifest's dimensions at t=0.
(c) The night side is the VIIRS texture's tan glow (see the sky report), which makes the dark start browner than it should
be; a high-pass on `lights_4096.jpg` would make the arrival's first frame bluer and quieter. (d) The `UTC —` clock reads a
dash during the arrival; it could show the day's first timestamp.

### Addendum (earthai-46 review: the night side was too bright)
The tan came from `lights_4096.jpg` itself: VIIRS carries a floor that is not city light (sea 4/255, the Amazon 9, the Sahara
28) and the shader painted it at 0.85. Now: a soft knee on the lights (`smoothstep(0.10,0.22)`, cities untouched), the night
ground down to a whisper of blue (0.001 linear ≈ 5/255 on screen), night cloud 12/255 where thick scaled to ~40 under a full
moon, moonlit ground 10/255 at full, the terminator band and the airglow line as they were, the day side untouched.
Measured at the night_rest view (lon −100, lat 20, dist 4.3, frame 60 = 6 Sep 10:00Z, `#nointro`, 100 px patches, 0–255):
disc centre over the US (cities) 75 → 32; Pacific off Mexico 57 → 10.5; south Pacific 47 → 8; northern Canada 73 → 17.
Plate: `docs/globe-2026-09-19/night_rest_before_after.jpg` (top before, bottom after). The arrival's exposure ramp keeps the
night near black at 100% by construction (it is a multiplier). Sheet and plates redone. A warm run this time did get the
opening ready at 1.7 s: the arrival then ran its full 6.5 s (215 → 6,732 ms) with one 67 ms gap at 1.35 s (the first present).
