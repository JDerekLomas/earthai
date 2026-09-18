# The cloud-layer globe: weather as the only video, everything else computed

## Posture (read first)
Derek opens a link on a MacBook (retina, trackpad), sometimes on a phone. He sees the Earth at once,
it is already moving, and he drags it, pinches in on a storm, lets go and watches. He stays a minute
or two. He is a designer: it has to be beautiful first, and it has to feel like an object, not a
video player. He leaves if it takes more than a couple of seconds to show anything, if it stutters or
spins the fans, or if zooming in turns it to mush. He said of the current page
(https://earthai-scales.vercel.app/earth/): "a bit low res and still having some artifacts", and of
this rebuild: "performance is obviously really important. Be smart about it."

## Why the current page looks the way it does (already diagnosed, do not redo)
- Low res: mosaics are 4096x2048 but the 10-day clip ships at 2048x1024, ~4 Mbps H.264 (20 km/px).
  The codec spends its bits on land, ocean and the terminator, which never needed to be video.
- Artifacts: lighting is BAKED IN per source. Each satellite's GeoColor has its own dusk haze, night
  floor and limb milkiness, so zones meet at a hard edge (mid-Atlantic, the dateline, ~60 S), and two
  of five zones are IR blobs painted on Blue Marble beside three zones of real texture. RIFE then
  warps across those edges.

## The idea
Separate the layers. The ONLY thing that changes every ten minutes is cloud, so the only video is a
single-channel cloud field. Land, ocean, day, night, terminator, city lights, cloud shading and the
atmosphere are computed in one fragment shader from the sun's position. No source has lighting in it,
so there is nothing to mismatch at a seam, the land is sharp at any zoom, and a grey cloud video
compresses several times better than a colour picture of the whole planet.

## Goal
A new page `site/globe/` (leave `site/earth/` exactly as it is, for comparison) showing one day of
real cloud at 10-minute cadence over a shader-lit Earth, measurably sharper, cleaner and lighter than
/earth/. Then widen it to all five satellites and ten days.

## Stage 1: prove it with GOES-East, one day (2026-09-12)
1. `scripts/fetch_clouds.py`. Build on `scripts/fetch_goes_aws.py` (anonymous HTTPS to the
   `noaa-goes19` bucket, exact geos reprojection already written). A cloud layer needs only TWO bands:
   one visible (band 2, 0.64 um) and one infrared (band 13, 10.3 um). `ABI-L2-MCMIPF` is ~370 MB per
   slot for all sixteen; do not download 53 GB a day for two variables. Try netCDF byte-range reads
   first (`https://...nc#mode=bytes` with netCDF4, or h5netcdf over fsspec) and pull only `CMI_C02`,
   `CMI_C13` and the projection variables; measure bytes per slot and say what it came to. If range
   reads do not work within an hour, download, extract, delete as you go (transient disk under 60 GB;
   679 GB free).
2. Cloud opacity, 0..1, by CLEAR-SKY COMPOSITING (the standard trick: what a pixel looks like with no
   cloud is its darkest visible / warmest infrared value at that time of day over many days; for a
   one-day proof use the day's own per-pixel extremes within a +/- 1 h window plus Blue Marble albedo
   as a prior, and say how well that works over the Atacama, Greenland and sun glint).
   - day: reflectance normalised by cos(solar zenith), minus clear-sky, scaled to opacity
   - night and everywhere: clear-sky brightness temperature minus BT, scaled to opacity
   - blend the two by solar zenith through twilight so the FIELD is continuous across the terminator.
     The night side is IR-only and softer. That is fine and deliberate: the shader shows night cloud
     dim, so the lower detail lands where the eye cannot see it.
   - weight to zero by satellite view angle from ~58 to ~66 degrees (70 was too generous: limb haze).
3. Output grid: equirectangular but CROPPED to the latitudes that have data (about +/- 66 deg), so no
   pixels are spent on poles nobody can show. 4096 wide, height a multiple of 16. Store each frame as
   a 16-bit or 8-bit grey PNG in `data/clouds/`, plus a coverage mask per satellite (static).
4. Encode: H.264, yuv420p, grey content, `+faststart`, keyframe every ~2 s of playback, no B-frame
   pyramids that hurt seeking. H.264 because every laptop and phone decodes it in hardware; stay at or
   under 4096x2304 (level 5.1), which is the real hardware ceiling. Also encode a 2048-wide variant for
   phones and slow links. Find the crf where cloud streets off Chile and thin cirrus survive; report
   the size of each.

## The page (`site/globe/index.html`, three.js 0.152.2 UMD from cdnjs; no other CDN works on this host)
Performance is the feature. Budget, to be MEASURED with puppeteer-core + headless Chrome (the
claude-in-chrome tab wedges on video-heavy pages) and reported as numbers:
- something beautiful on screen in under 1.5 s on a warm connection: the lit globe renders from a
  2048 basemap and ONE poster cloud frame (a ~150 KB WebP/JPEG) before any video byte arrives; the 8192
  basemap and the video stream in afterwards and swap in without a flash
- 60 fps while dragging on an M-series laptop; pixel ratio capped at 2; no per-frame allocation
- total bytes to first moving frame, and total for the day, both stated; pick the 2048 video when the
  screen is small or `navigator.connection` says so; check `renderer.capabilities.maxTextureSize`
  before asking for an 8192 texture
- the cloud texture uploads only when the video presents a NEW frame (`requestVideoFrameCallback`;
  confirm what three's VideoTexture actually does in 0.152 rather than assuming), and the video pauses
  when the tab or the canvas is off screen
One draw call for the planet, one ShaderMaterial:
- uniforms: day basemap (Blue Marble, no clouds, no baked shading if GIBS has one), night lights
  (VIIRS Black Marble; `data/earth/_lights.jpg` exists), cloud video, sun direction from UTC
- day/night by dot(normal, sun) with a soft twilight band and a warm terminator tint; ocean specular
  glint; lights only on the night side and only where cloud is thin
- cloud: white lit by the sun, blue-grey and faint at night, and a cheap shadow (sample the cloud
  texture again, offset a little toward the sun, darken the ground) which is what makes it read as
  weather above a surface rather than paint on it
- thin atmosphere rim as now. Earth-fixed and sun-fixed modes as on /earth/ (sun-fixed is now just a
  uniform, not a texture trick). A coverage toggle showing where GOES-East can and cannot see;
  outside it the globe is honestly clear and the page says so in a plain sentence.
- temporal smoothness: decide by LOOKING and by bytes between (a) real frames with a shader cross-fade
  between the two nearest frames and (b) RIFE x4 on the grey clips (`scripts/interpolate.py`; check how
  the last session ran it before starting any GPU box; do not start the Scaleway instance for this
  without saying so in the report). Ghosting on fast cloud is the failure of (a); bytes and invented
  motion are the cost of (b).
Style tokens from `site/month/index.html`. Copy in plain sentences: what is real (the clouds), what is
computed (the light), what is missing (the poles, the other four satellites for now).

## Stage 2 (go straight on if stage 1 is clearly better; no need to ask)
- GOES-West (`noaa-goes18`) and Himawari-9 (`noaa-himawari9`, HSD segments, bz2) from AWS, same two
  bands. MTG and Meteosat-9 from EUMETView's keyless WMS single-channel layers (0.6 um and 10.5/10.8
  um; `scripts/fetch_eclipse_meteosat.py` already reads and calibrates them). If anything needs an
  account or key, STOP on that satellite, ship without it, and say so; do not create accounts.
- Blend by view-angle weight. Because every zone is now the same physical quantity, check the seams
  with numbers: mean opacity difference in each overlap.
- Ten days (6-15 Sep, to match /earth/), `scripts/earth_qc.py`-style rules adapted to grey frames.
- Link /globe/ from the homepage only when it is the better page. Another session has uncommitted
  work in `site/earth/index.html`, `site/index.html`, `site/night/`, `scripts/fetch_night.py`: do not
  touch, stage or commit those files; `git add` your own paths by name.

## Definition of done
Deployed from `main` (`git branch --show-current` first; `pgrep -f vercel`; `cd site && npx vercel
--prod --yes`), curl-checked (200/206, and the `<title>`), LOOKED AT in headless Chrome with
screenshots of: the whole disc, a zoom on the Chile cloud streets, the terminator, the mid-Atlantic
where the old seam was, each beside the same view of /earth/. Committed and pushed (this repo is
JDerekLomas/earthai, its own repo, work on main; `data/` is gitignored so work in this directory, not
a worktree). Tracking issue: see the link appended below.

## Report back
Append to this file, numbered, short: what shipped and the URL; the measured numbers (bytes per slot
fetched, clip sizes, time to first paint, fps, bytes to first moving frame); what the clear-sky method
gets wrong; which satellites are in; what you would do next. Then SendMessage the session named
"earth-7e": one line, DONE or BLOCKED + URL. If that session is gone, the file is the report.

Tracking issue: https://github.com/JDerekLomas/earthai/issues/2
