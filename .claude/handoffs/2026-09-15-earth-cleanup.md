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
