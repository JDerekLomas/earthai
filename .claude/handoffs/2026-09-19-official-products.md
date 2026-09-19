# Official sources and cloud products: the physics layer

## Posture (read first)
Derek asked "what else do you need for quality?" and then did the two things only he could do: he
registered with JAXA (P-Tree) and EUMETSAT and pasted the credentials. He expects the globe at
https://earthai-scales.vercel.app/globe/ to move from "looks like weather" to "is the weather": the
two zones that are still second-hand (Himawari from bz2 segments with a home-made Planck reader;
MTG and Meteosat-9 from contrast-stretched 8-bit WMS pictures quantile-matched against a neighbour)
become first-class, and cloud opacity stops being a two-band guess against a clear-sky estimate and
becomes the agencies' own retrieval: optical depth and cloud-top height. He is a designer; the test
is that seams vanish as numbers (mean |Δopacity| in overlaps, today 0.08–0.14) and that high ice
cloud and low stratus look different from each other on the globe.

## Credentials (in `.secrets.json`, gitignored; never print them, never commit them)
- `EUMETSAT_CONSUMER_KEY` / `EUMETSAT_CONSUMER_SECRET`: mint a bearer with
  `curl -d grant_type=client_credentials -u KEY:SECRET https://api.eumetsat.int/token` (1 h validity;
  verified working 2026-09-19). Data Store: https://api.eumetsat.int/data/browse/collections and
  https://api.eumetsat.int/data/download/1.0.0/collections/<id>/products/<pid>. The account holds the
  "Meteosat > 1 hr latency" licence, so archive L1 is allowed; do not request the < 1 hr licence.
  `pip install eumdac` is the official client and is fine to use.
- `PTREE_FTP_HOST/USER/PASS`: plain FTP to ftp.ptree.jaxa.jp (verified listing 2026-09-19).
  Paths seen: `/jma/hsd/YYYYMM/DD/HH/` (raw HSD, 1782 files/hour), `/jma/netcdf/YYYYMM/DD/`
  (full disc on a lat/lon grid: `NC_H09_..._R21_FLDK.02801_02401.nc` = 0.05° 5 km, 143 MB;
  `.07001_06001.nc` = 0.02° 2 km, 697 MB), `/pub/himawari/L2/CLP/010/YYYYMM/DD/HH/` (cloud
  property L2, 10-min: optical thickness, top height, effective radius, phase — read the FAQ at
  https://www.eorc.jaxa.jp/ptree/faq.html and the product doc for the variable names and grid).
  JAXA terms: cite JAXA/JMA; no redistribution of the numerical data. Our page ships derived pictures.
- The box (Scaleway `earthai-clouds-10d`, 51.15.76.176, session "hetzner ten-day milestones") is the
  right place to run bulk fetches (80 MB/s vs ~1 MB/s here). It MAY hold these two credentials in a
  root-only file outside any git checkout (`/root/.earthai-secrets.json`, chmod 600); nothing else.
  Coordinate with that session by SendMessage before starting jobs there — it is running the
  GOES-West tile build and the cache rsync; ask it for CPU/disk headroom and a tmux name.

## Stage A: replace the second-hand zones (12 Sep first, then the ten days)
1. **Himawari from JAXA's gridded netCDF (2 km, 0.02°)** instead of HSD: bands 03 (0.64 µm) and 13
   (10.4 µm) are already calibrated (reflectance / brightness temperature) on a lat/lon grid, so the
   reader is a resample, not a Planck decoder. Confirm the variable names and scale factors from
   the file. Keep the existing `fetch_clouds.py` opacity path as the fallback; measure the
   difference against the HSD path on three slots (mean |Δ|, and a plate).
2. **Meteosat-9 (IODC) and MTG-I1 from the Data Store**: `EO:EUM:DAT:MSG:HRSEVIRI-IODC` (SEVIRI
   L1.5 native, 3 km IR / 1 km HRV; satpy reads it: `seviri_l1b_native`) and the FCI L1c collection
   (search the collection list for "FCI" or "MTG"; satpy `fci_l1c_nc`; 2 km IR 10.5 µm, 1 km VIS
   0.6 µm; a full-disc slot is many chunk files — fetch only the chunks/bands needed). Calibrated
   reflectance and BT, no more quantile matching. Keep the WMS path as fallback; report bytes/slot.
3. **Seam numbers** with all five zones on the same physical quantities, same table as the tiles
   report. Then the ten days on the box, native cache kept per satellite as for GOES.

## Stage B: cloud products instead of the clear-sky guess
1. **GOES (NOAA, AWS, free, no key)**: `ABI-L2-CODF` (cloud optical depth, 2 km, daytime),
   `ABI-L2-ACHAF` (cloud-top height), `ABI-L2-ACMF` (clear-sky mask), `ABI-L2-ACTPF` (phase).
   Full-disc every 10 min in `noaa-goes19` / `noaa-goes18`. These are small (tens of MB).
2. **Himawari (JAXA L2 CLP)**: optical thickness + top height + phase, 10-min, from the FTP path above.
3. **Meteosat (EUMETSAT)**: search the Data Store for the cloud products (OCA "Optimal Cloud
   Analysis" for MSG/IODC gives optical thickness + top pressure; for MTG look for the FCI L2 cloud
   products, CLM/OCA). If a product is missing or licence-gated, say so and keep Stage-A opacity
   for that zone.
4. **A common cloud representation for the page**: opacity = 1 − exp(−τ·g) with g ≈ 0.75 (the
   two-stream forward-scattering approximation; put the constant in the manifest), night falls
   back to the IR opacity where COD is unavailable (COD is daytime-only from GOES), blended through
   twilight as now. Second channel = cloud-top height (km, scaled to 0–20 in 8 bits) — the clip
   already has flow in chroma; put height in a SECOND grey clip / tile stream aligned frame-for-frame
   (`clouds_height.mp4`, `tiles/L5h/…`), or reuse the height path that earthai-46 shipped (5972fcd) —
   read that code first and extend it rather than fork it.
5. **Shader**: height drives shading and colour — high ice cloud brighter and slightly blue, long
   shadow offset proportional to height; low stratus warm grey, short shadow; sun-facing slope from
   the height field rather than from opacity. Keep the existing relief as fallback when height = 0.
6. **Report the seam table and the same plates** (whole disc, Chile, Caribbean, mid-Atlantic,
   dateline) before/after, and the fraction of cloud pixels whose opacity changed by > 0.2.

## Rules
Work on a branch `official-products` in a worktree of /Users/dereklomas/sourcelibrary/earthai (data/
only in the main checkout; read/write it by absolute path). Small commits. Before merging: fetch,
rebase, SendMessage `earthai-46` one line (it owns the page right now: sky/height/more-days work);
wait up to 10 min. Deploy with `scripts/deploy_site.sh` (HEAD + assets, guards the clip height), not
from the working tree; `pgrep -f vercel` first. No new accounts (both are in hand), no GPU box, stay
out of site/earth, site/night, site/index.html. Credit lines on the page: "Himawari data: JAXA/JMA
(P-Tree)", "Meteosat data: EUMETSAT", "GOES data: NOAA".

## Report back
Append here: bytes per slot per source, what the official products change (numbers + plates), the
seam table, what is still second-hand and why. SendMessage "earth-7e" one line at Stage A done and
at Stage B done: DONE/BLOCKED + URL.
