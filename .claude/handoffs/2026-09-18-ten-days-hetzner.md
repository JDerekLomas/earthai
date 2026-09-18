# Ten days of cloud from a Hetzner box

## Posture
Derek approved renting a box ("you want to do on the hetzner?" / "k"). The laptop's link to AWS S3 is
0.3–1.5 MB/s per connection with SSL EOFs and IncompleteReads; one satellite-day took 1.5 h, and ten
days of five satellites (~340 GB) is not doable from here. The result he wants: /globe/ playing the
same ten days as /earth/ (6–15 Sep 2026), and the native-resolution cache that the tile levels
(brief `2026-09-18-globe-tiles.md`) are built from, so nothing has to be fetched twice.

## Who runs what
- This session: provisions the box, runs the fetches there, brings the products back, keeps the
  cost visible, and DELETES the box when done. The page integration belongs to the other sessions.
- Two sibling background sessions are working in this directory: motion (`2026-09-18-globe-motion.md`)
  and tiles (`2026-09-18-globe-tiles.md`). Do not edit `site/globe/`, `scripts/fetch_clouds.py` or
  `scripts/cloud_tiles.py`; if the box needs a fix in a script, make it in a copy on the box, and
  put the diff in your report for them.

## The box
- `hcloud` is installed and authenticated (context "mycontext"; existing servers are sourcelibrary's,
  leave them alone). Create ONE server: `cpx51` (16 vCPU, 32 GB, 360 GB disk) or `ccx33` if cpx51 is
  unavailable, image ubuntu-24.04, location nbg1 or fsn1, name `earthai-clouds-10d`, ssh key = an
  existing key from `hcloud ssh-key list`. Attach a 600 GB volume (raw cache + products; ext4,
  automount). Labels at creation, per the sourcelibrary rule: `lease-until=2026-09-21T23:59:00Z`,
  `owner=earthai-issue-2`. Expect ~EUR 0.10/h server + ~EUR 0.03/h volume; state the running total
  in the report. If the lease is about to expire with work unfinished, extend the label and say so.
- Setup: python 3.12 venv, `pip install -e .` from a clone of github.com/JDerekLomas/earthai plus
  whatever `fetch_clouds.py` / `cloud_tiles.py` import (netCDF4, h5netcdf, xarray, pyproj, opencv,
  requests). Run every long job under `tmux` or `nohup` with a log; never inside an ssh session that
  can drop.
- First measure: one GOES slot from the box (bytes/s). If it is not at least 10x the laptop, stop and
  report before spending more.

## The work, in this order
1. Native fetch with `scripts/cloud_tiles.py fetch` (it keeps the 2 km band arrays per satellite
   window; check its `--help` and the tiles brief for what it writes) for GOES-East and GOES-West,
   6–15 Sep, 8 workers each, in parallel. Then Himawari-9 (bigger: ~150 MB/slot by day). MTG and
   Meteosat-9 come from EUMETView's WMS via `fetch_clouds.py` at the 4096 grid (the WMS can be asked
   for larger images; note in the report what native for them would take, do not do it now).
2. Run the existing 4096 pipeline (`fetch_clouds.py clearsky / opacity / blend / encode`) over the
   ten days FROM THE NATIVE CACHE where a derive step exists, else from its own fetch (the 4096 fetch
   is 6 GB/day, cheap from the box). Ten days of clear-sky (per-time-of-day warmest, true darkest) is
   the main quality gain; keep the per-satellite clear-sky references as files.
3. Products back to the laptop: the ten-day clips and manifest (`clouds.mp4`, `clouds_2k.mp4`,
   `clouds.json`, poster) into `data/clouds/tenday/` (NOT into site/globe/), the opacity PNGs, and the
   clear-sky references. rsync over ssh with `--partial --bwlimit=0`; measure the rate. The native
   cache (~100+ GB) STAYS on the volume until the tiles pipeline has consumed it (see 4).
4. Upload to R2 bucket `earthai-clouds` (exists, empty): native cache under `native/<sat>/<date>/`,
   products under `tenday/`. `wrangler` on the laptop is logged in via OAuth, which cannot be copied
   to the box. Two options, try in order: (a) `rclone` on the box with an R2 S3 API token — Derek must
   create it in the Cloudflare dashboard (R2 > Manage API tokens > Object Read & Write, this bucket);
   ask for it through your report / SendMessage to earth-7e and carry on with 1–3 meanwhile; (b) if
   no token arrives, rsync to the laptop and `wrangler r2 object put` from there for the products only.
5. Detach and delete the volume and server when the cache is in R2 (or Derek says the cache is not
   needed). `hcloud server delete`, `hcloud volume delete`. Confirm with `hcloud server list`.

## Report back
Append here: box id, cost so far, measured bytes/s, slots fetched per satellite (and which slots do
not exist on S3), where the products landed, what is still on the box, and the diff for any script
fix. Commit and push this file only. SendMessage "earth-7e" one line at each of: box up + rate
measured; ten-day 4k clip on the laptop; cache in R2 and box deleted. BLOCKED lines when waiting on
Derek (the R2 token).
