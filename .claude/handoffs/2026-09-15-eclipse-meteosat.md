# The 12 August 2026 eclipse again, from Meteosat-12 (MTG-I1) at 0 degrees

## Posture (read first)
Derek has seen the GOES-East version (https://earthai-scales.vercel.app/eclipse/) and asked for the
same from Meteosat-12, which sat almost directly under the path: Greenland, Iceland, Spain, all in
its daylight hemisphere at a much better angle than GOES-East's limb. He wants to watch the shadow
cross for thirty seconds, beautifully, on the SAME page as the GOES-East version, and read one
plain paragraph on what is different. He is a designer, not a meteorologist.

## What is already known (measured 15 Sep, do not re-derive)
- EUMETSAT's open, keyless WMS serves 12 August at 10-minute cadence:
  `https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap&layers=mtg_fd:rgb_geocolour&crs=EPSG:4326&bbox=25,-60,80,40&width=1000&height=550&format=image/png&time=2026-08-12T17:30:00Z`
  returns a full image (checked 17:30 and 18:30). `scripts/fetch_earth.py::wms_image` is the working
  helper (bbox order s,w,n,e in EPSG:4326 for WMS 1.3.0).
- The `rgb_geocolour` product blends a night rendering in where it thinks it is dusk, and it reads
  the eclipse darkness as dusk: at 17:30 the Iceland/Greenland corner is tinted orange-red and the
  night city lights start to appear. That is an artifact of the product, not the sky. So: fetch the
  plain daytime true colour too -- read GetCapabilities
  (`https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetCapabilities`) for
  the MTG FCI true-colour RGB layer (something like `mtg_fd:rgb_truecolour`), which shows the shadow
  as plain darkness. Use that for the main clip; show one geocolour frame as a curiosity if you like.
- The GOES-East page's method (divide by cos solar zenith, then read the darkening as the anomaly;
  brightness curves for named boxes; a band-13 pair as the "clouds did not change" control) lives in
  `scripts/fetch_goes_aws.py` and `scripts/eclipse_media.py`; reuse the curve and the page sections.
  Meteosat's IR 10.8 um layer for the control: look for the MTG FCI IR layer in capabilities
  (`mtg_fd:ir105` or similar; Meteosat-9's is `msg_iodc:ir108`).
- Umbra timing: Greenland ~16:30-17:30 UTC, Iceland ~17:45, Spain ~18:30 (sunset end in the
  Mediterranean). Fetch 14:00-20:30 UTC every 10 min. Bbox lon -60..40, lat 25..80 at ~0.05 deg
  (2000x1100) is plenty; 1 MB a frame.

## Definition of done
1. `scripts/fetch_eclipse_meteosat.py` (or a mode of an existing script): frames to `data/eclipse_mtg/`.
2. On `site/eclipse/index.html`, a new section near the top, "From Meteosat, under the path": the
   true-colour clip (6 fps and a RIFE-smoothed version via `scripts/interpolate.py`-style call, see
   `scripts/interpolate.py` and the rife binary at `~/tools/rife/`), a still of the umbra over
   Iceland at its darkest, the same brightness curves for Greenland / Iceland / Spain boxes, and the
   one-paragraph comparison with GOES-East (angle, resolution, what each could and could not see).
3. Committed, pushed, deployed from `main` (`cd site && npx vercel --prod --yes`; first check
   `ps -eo args | grep -c "[v]ercel --prod"` is 0), curl-checked, and LOOKED AT with headless Chrome
   (the previous session found the Chrome extension wedges on mp4 pages; puppeteer-core in
   site/node_modules worked -- see auto-memory `earthai-browser-check-video-pages`).

## Rules
- Work in /Users/dereklomas/sourcelibrary/earthai directly (data/ is gitignored, no worktree).
  Another session is cleaning up site/earth/ at the same time; stay out of it. You own site/eclipse/.
- EUMETSAT imagery credit line on the page: "EUMETSAT" (their open data terms; state it).
- Report back: append a 5-line summary to this file, commit, SendMessage the session named
  "physics-clouds" if it exists (one line, DONE + URL).
