# The whole-Earth globe: remove the artifacts, then interpolate

## Posture (read first)
Derek watched https://earthai-scales.vercel.app/earth/ and said: "great, but has some artifacts to
be removed and interpolated." He means what he SEES in the turning globe: frames that flash, zones
that drop out, seams that jump, and the 10-minute steps. He will watch the result for a minute
and judge by eye. Every fix must be a measured rule, not a hand-picked frame list.

## Definition of done
1. **Find the artifacts by measurement, then look.** `data/earth/earth.jsonl` records per mosaic
   the share of each satellite's zone that carried data; `data/earth/*.jpg` are the 432 mosaics
   (12-14 Sep). Write `scripts/earth_qc.py`: (a) a source dropping below its usual share (a zone
   going dark or falling back to basemap for one frame); (b) single-frame brightness flashes per
   zone, using the same rule as `scripts/goes_qc.py` (jump > 6 grey levels away from BOTH
   neighbours while they agree within 4); (c) any residual white wedge; (d) a seam jump: the
   mean brightness difference across each seam line changing abruptly between frames. Print counts
   per rule and write the flagged frames to a list. Then LOOK at 6 flagged frames (Read the jpgs)
   and 3 unflagged ones and say what the artifacts actually are; if a class is not caught, add a
   rule for it.
2. **Fix at the source where cheap** (e.g. a missing GIBS tile should fall back to the other
   satellite's pixels or the previous frame's, not to black; Meteosat-9's 15-minute slots should
   be time-interpolated rather than nearest-held) in `scripts/fetch_earth.py`, and re-render the
   affected mosaics only. Quarantine what cannot be fixed (move to `data/earth/_qc_rejected/`
   with a reason, like goes_qc does) -- the interpolator then morphs across the gap.
3. **Interpolate.** RIFE 4x through `scripts/interpolate.py`'s `rife_encode` (chunked; the rife
   binary is at `~/tools/rife/rife-ncnn-vulkan-20221029-macos`, ~47 frames/s at 768 px on the Mac's
   GPU; expect slower at 3072x1536 -- measure on 10 frames first, and drop to 2048x1024 if needed).
   Encode the smooth version as `site/earth/earth_smooth.mp4` (keep under ~60 MB: crf 28-30, 48 fps)
   and add a "smooth" toggle on the page next to the existing controls; the manifest carries both.
4. Page: one added paragraph on what was removed and how (counts), and that the in-between frames
   are learned, not observed.
5. Committed, pushed, deployed from `main` (check `ps -eo args | grep -c "[v]ercel --prod"` is 0
   first), curl-checked, and LOOKED AT with headless Chrome (see auto-memory
   `earthai-browser-check-video-pages`; the extension wedges on mp4 pages).

## Rules
- Work in /Users/dereklomas/sourcelibrary/earthai directly (no worktree; data/ is gitignored).
  Another session is adding a Meteosat section to site/eclipse/ at the same time; you own
  site/earth/ and scripts/fetch_earth.py; stay out of site/eclipse/.
- Do not refetch the 3 days from scratch unless a source-side fix requires it (~9 s a frame).
- Report back: append a 5-line summary to this file (what the artifacts were, counts removed,
  sizes), commit, SendMessage the session named "physics-clouds" if it exists (one line, DONE + URL).

## Report (2026-09-15, background session earth-cleanup)
1. SHIPPED https://earthai-scales.vercel.app/earth/ -- `scripts/earth_qc.py` (rules: coverage drop, per-zone flash, white tile, seam jump, per-block flash against a 6-frame median) flagged 30 of the first render's 432 mosaics; looking at them showed four artifact classes: dark rectangles where a GIBS tile block never arrived (the dimmed basemap showing through), flat sky-blue wedges in GeoColor tiles (exact RGB 140,191,250, a partial render's fill), pure-white wedges in Himawari Band 13 tiles (painted as -92 C cloud, recurring daily at 14:00 and 15:00Z), and bright streaks where a Meteosat-9 WMS slot came back with transparent rows -- plus Meteosat-9's 15-minute stepping.
2. Fixed at the source in `scripts/fetch_earth.py`: wedge pixels masked (any fill colour on >2% of a tile's used area; clean tiles measured <=1.2% GeoColor, 0.0 Band 13), every missing pixel filled from that satellite's previous picture for up to 40 min (13 frames filled >=5%, 8 of them a whole picture), partial WMS slots dropped, Meteosat-9 time-interpolated between its 15-min slots (cached), `--warm` for parallel day runs, `--smooth` (RIFE 4x through interpolate.py's chunked rife_encode). All 3 days re-rendered (GIBS throttled to ~3-5 tiles/s from this address: 3 processes x 4 workers, ~70 min); old render kept in `data/earth_v1/`.
3. After the re-render the QC flagged 1 frame: 2026-09-14 15:00Z, where GIBS now returns the GOES-East tiles as a grey dome-shaped smear over the Caribbean (no flat fill, unmaskable) -- quarantined to `data/earth/_qc_rejected/`, recorded in `earth.json` under `removed`; 431 frames remain and the clip steps over it.
4. Clips: `earth.mp4` 431 frames, 3072x1536, crf 24, 30.8 MB; `earth_smooth.mp4` 1,721 frames (RIFE v4.6 x4, 48 fps, crf 28) 37.9 MB, 13 min on the GPU (1.3 real frames/s at 3072). Page: Smooth toggle beside Flat map, one shared real-frame timeline (fractional sun longitude so sun-fixed does not step), a "What was removed, and what is invented" note with the counts.
5. Verified: curl 206 on page, manifest and both mp4s; headless Chrome (puppeteer-core, `$CLAUDE_JOB_DIR/tmp/shot/shot.js`) played the real clip, toggled to smooth with the clock continuous, switched to sun-fixed, and screenshotted the note. No session named "physics-clouds" existed at the end, so no message was sent. Not done: no fetch-time rule for the dome-smear class (one instance; the QC quarantine is the guard), and the daily Himawari 14:00/15:00Z wedges are masked+filled rather than reported upstream.
