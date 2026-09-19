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

## Report (2026-09-19, session official-products; PR #14 merged, main 99fbd09) — interim, the ten days follow

1. **Shipped.** https://earthai-scales.vercel.app/globe/ — 12 Sep 2026 is now built from the agencies' own numbers:
   Himawari-9 from JAXA's gridded L1 (P-Tree), MTG-I1 from EUMETSAT's FCI L1c, Meteosat-9 from SEVIRI L1.5 native, GOES
   as before (NOAA CMIP), and the agencies' cloud retrievals folded in: NOAA ABI COD/ACHA/ACTP, JAXA CLP, EUMETSAT OCA
   (MTG) and CTH+CLM (Meteosat-9). The other nine days stay as they were until the box finishes them (see 8); the page
   says so (`official_days` in the manifest drives the copy: sources table, seams note, height note, footer credits).
   Code: `scripts/official_sources.py` (readers), `fetch_clouds.py` (`fetch --source official`, `products`,
   `apply_products`, `encode --merge-into`, `DiskGeometry` for lat/lon grids and satpy areas), `official_compare.py`.
2. **Bytes per slot per source, measured** (laptop unless said; box = Scaleway Paris):
   | source | what | MB/slot | s/slot | notes |
   |---|---|---|---|---|
   | JAXA P-Tree L1 (FTP byte range) | albedo_03 + tbb_13 + SOZ + SAZ of the 0.05° file | 15–23 (mean 19.5) | 90 laptop / 45 box (3 workers) | file is 106–143 MB; 9 REST/RETR requests; HSD was 151 MB by day |
   | JAXA CLP L2 | COT, CTH, type, QA | 1.3–32 (mean 18) | 13 | daytime only: 108 of 144 slots |
   | EUMETSAT FCI L1c (whole chunks) | 40 chunk files, vis_06 + ir_105 read from each | 850–930 | 148 laptop / 32 box idle / 43–65 box loaded | `--range-reads` would be ~210 MB in ~440 requests at ~1 s each: lighter, slower |
   | EUMETSAT SEVIRI native (IODC) | one .nat, satpy | 271 | 135 laptop / 13 box | every 15 min |
   | EUMETSAT OCA (MTG) | whole netCDF (chunks interleaved, ranges useless) | 162–167 | 22 box | COT (2 layers), CTH, phase |
   | EUMETSAT CTH + CLM (IODC) | two GRIBs, satpy | 4.3 | 10 | no COT product exists for the Indian Ocean service |
   | NOAA ABI L2 (byte range) | COD 4 km + HT 10 km + Phase 2 km + DQF | 12–16 (mean 14.4) | 40 laptop / 14 box | night: no COD |
   12 Sep totals: Himawari 2.8 GB, MTG L1 ~125 GB (box), Meteosat-9 26 GB (box), products ~30 GB. Ten days ≈ 1.9 TB, all on the box.
3. **What the official L1 changes (12 Sep, 4096 grid, satellite-zenith weight > 0.5).** Himawari: P-Tree vs the HSD
   reader — BT bias −0.001 K, mean |Δ| 0.36 K, corr 0.9994; reflectance bias +0.0023 (+1.2%, the Earth–Sun distance
   correction HSD lacked), mean |Δ| 0.0066, corr 0.998: the home-made Planck reader was right, the gain is 8× fewer bytes
   and a validation. Note `albedo_NN`'s long_name says reflectance×cos(SOZ) but it IS the reflectance factor (ratio to HSD
   0.988 flat over solar zenith). MTG: FCI native vs the WMS greys — BT mean |Δ| 1.49 K (p90 3.3 K), reflectance mean |Δ|
   0.017 (p90 0.042); FCI's x axis runs the other way from pyproj's geos x (the disc came out mirrored; now negated; my
   reader matches satpy exactly / 6e-5 K). Meteosat-9: SEVIRI vs WMS — BT mean |Δ| 2.0 K (p90 4.4 K), bias −0.45 K.
   Plates: `docs/globe-2026-09-19-official/{himawari_raw_0300z,mtg_raw_1700z,iodc_raw_1700z}.jpg`.
4. **Opacity from optical thickness.** The brief's 1−exp(−0.75τ) saturates a τ-2 cloud at 0.78; no single exponential fits
   the two-band field (best g 0.07, rmse 0.139). The two-stream reflectance A·x/(1+x), x=(1−g)τ, does: fitted on GOES-East
   12 Sep 17Z, A 0.88, g 0.86 (the droplet asymmetry parameter; textbook 0.85), rmse 0.123, within 0.05 of the bin medians
   from τ 3 to τ 70 (`curve.cod_a`, `cod_asym` in the manifest). COD takes over by day through a cos(solar zenith) ramp
   0.25→0.45; night keeps the two-band field; a pixel the agency's mask calls clear has its two-band opacity ×0.25
   (`clear_damp`). Retrieved tops replace the lapse-rate height where they exist; a hole in a retrieval inside a retrieved
   cloud takes its neighbours' top (5 px max filter) rather than falling several km.
5. **What the products change (12 Sep, official tree vs the one-day legacy tree with the same clear-sky method).** Mean
   |Δopacity| 0.030 over every pixel; 35% of the grid is cloud (op ≥ 0.1 in either) and **9.5% of those pixels moved by
   more than 0.2**. Height: where both have a top, mean |Δ| 3.5 km and 62% moved by more than 2 km — the retrieved tops put
   cirrus and anvils at 10–14 km where the lapse method read 4–6 (cloudy-pixel p50 5.0 km at 20Z vs 1.8–2.1 before). To the
   eye (`docs/globe-2026-09-19-official/page/pair_*.jpg`, before above / after below): the false cloud over the Sahara and
   Sudan at noon is gone (Africa 12Z); the Atacama/Andes false cloud is gone (Chile); anvils stand and throw long shadows
   (Caribbean); the Chile stratocumulus reads as a low flat deck. Frame-level crops: `docs/globe-2026-09-19-official/frames_*.png`.
6. **Seam table, 12 Sep** (mean |Δopacity| where both satellites weigh ≥ 0.5, all 144 frames; before = one-day legacy):
   GOES-E/GOES-W 0.064 → **0.050**; GOES-E/MTG 0.062 → **0.045**; MTG/Meteosat-9 0.074 → **0.056**; GOES-W/Himawari 0.067 →
   **0.063**; Himawari/Meteosat-9 0.117 → 0.119 (bias −0.028: Himawari has a COD by day, Meteosat-9 has none, so that seam
   now compares two different quantities; it is the one that did not improve). Against the live ten-day build's 12 Sep
   (ten-day clear-sky) the same pairs read 0.068/0.059/0.076/0.068/0.113 before.
7. **Still second-hand, and why.** (a) Meteosat-9's opacity: EUMETSAT publishes no optical-thickness product for the Indian
   Ocean service, so its daytime opacity stays two-band (the retrieved CTH and mask are used). (b) Himawari at night: JAXA's
   CLP is a daytime algorithm; night is two-band + lapse height. (c) The regional tiles (`cloud_tiles.py`) are the two-band
   pipeline at 2 km; untouched. (d) The nine other days of the live page until 8 lands. (e) No native cache is kept for
   FCI/SEVIRI (the brief's "as for GOES"): 2 km arrays are 500 MB/slot; the box's disk budget is 200 GB. Say if wanted.
8. **The ten days, running on the box** (tmux `official10` + `official:him10`, logs `/data/official/logs/*.log`, tree
   `/data/official/clouds`, budget from the box's owner: ≤ 6 GB RSS, ≤ 200 GB disk). At 16:40 UTC: Himawari 837/1440,
   FCI 219/1440 (43 s/slot → ~15 h), SEVIRI 448/960, OCA 312/1440, GOES products stopped at 37 (RAM) and re-run by the
   pipeline stage. To finish: `bash /data/official/tenday_box2.sh pipeline` (GOES products, clearsky ×3, opacity ×5, blend,
   height, encode `--per-day --name clouds_v2 --assets https://clouds.sourcelibrary.org/globe/` into
   `/data/official/clouds/days`), rsync `days/` to `data/clouds/official/days10/`, `r2_sync.py data/clouds/official/days10:globe
   --skip .log,.txt,.npy,.npz,.jsonl,.DS_Store,.png,.json`, then `encode` is not needed again: run `merge_manifests` (or
   `encode --per-day --merge-into site/globe/clouds.json --days-only <none>` shape) to replace all ten days, commit
   `site/globe/clouds.json`, deploy with the deploy_site.sh recipe (`~/.claude/jobs/9ef08e9b/tmp/deploy_from_main.sh`),
   message tile-sets to pull. Credentials on the box: `/root/.earthai-secrets.json` (root, 600) — delete when the fetches end.
9. **Traps for the next reader** are in auto-memory `earthai-official-sources-traps.md` (JAXA albedo naming, FCI x axis,
   Data Store range latency, OCA's interleaved chunks, P-Tree's ~4-connection cap per account, the worktree hook tripping on
   any "git" substring such as `longitude`).
