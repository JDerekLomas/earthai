# Finish the ten days from the agencies' own data (continuation of 2026-09-19-official-products.md)

## Posture (read first)
Derek looks at https://earthai-scales.vercel.app/globe/ and expects every day, not just 12 Sep, to be built from
JAXA's, EUMETSAT's and NOAA's own products (the page currently says "on 12 Sep … the other 9 days were built
before the agencies' data was in hand and are being rebuilt"). This job is mechanical: the fetch and pipeline are
running unattended on the box; you bring the result back, publish it, and say what changed. Nothing here needs
Derek unless the box dies.

## What is running (started 2026-09-19 ~16:00 UTC)
- Box: Scaleway `earthai-clouds-10d`, `root@51.15.76.176`, tree `/data/official/clouds`, clone `/data/official/earthai`
  (branch `official-products`), venv `/data/official/venv`, runner `/data/official/fc.sh`.
  tmux `official10` runs `tenday_box.sh fetch` (FCI ~15 h, SEVIRI, OCA, IODC products), `official:him10` the Himawari L1
  fetch, and `official10p` waits for `FETCH DONE` in `/data/official/tenday.log` then runs `tenday_box2.sh pipeline`
  (GOES products, clearsky, opacity, blend, height, encode --per-day into `/data/official/clouds/days`) and prints
  `PIPELINE DONE`. Logs: `/data/official/logs/*.log`, `/data/official/tenday.log`.
- The box belongs to session "hetzner ten-day milestones" (its own tile builds until ~03:00 UTC 20 Sep). Budget agreed:
  my jobs ≤ 6 GB RSS total (`ps -o rss=,args= -C python | grep official`), `/data/official` ≤ 200 GB. Never `pkill -f`
  there (it kills the tmux server); never overwrite a script a running chain is executing (copy under a new name).
- Credentials on the box: `/root/.earthai-secrets.json` (root, 600). **Delete it when the fetches are done**
  (`ssh root@51.15.76.176 rm /root/.earthai-secrets.json`) and say so in the report.

## Definition of done
1. `sh scripts/ops/official_tenday_finish.sh` from a worktree on branch `official-products` (it waits for the box, rsyncs
   `days/` to `data/clouds/official/days10/`, uploads the clips to R2, merges the ten days into `site/globe/clouds.json`,
   commits and pushes). If the box's encode wrote fewer than ten days, say which and why (check `tenday.log`).
2. `scripts/official_compare.py --legacy data/clouds/tenday --official <rsynced tree or the box's> --date <each day>` is
   optional; at least print the manifest's seam table (`m.seam`) next to the live one before your deploy, per pair.
3. Rebase on main, `gh pr create --base main`, message `earthai-46` one line and wait up to 10 min, `gh pr merge --merge`,
   deploy with deploy_site.sh's recipe from origin/main (`~/.claude/jobs/9ef08e9b/tmp/deploy_from_main.sh` — copy it into
   your own job dir; a bg worktree session cannot run the script itself because it wants the main checkout on `main`),
   curl-check `official_days` has ten entries, message `tile-sets publish execution` to pull main.
4. Look at it: run `~/.claude/jobs/9ef08e9b/tmp/shot.js` (copy it) against the live page for 8 Sep and 14 Sep views
   (frame offsets: day index × 144) and put the plates in `docs/globe-2026-09-19-official/page/`.
5. Append a numbered report to `2026-09-19-official-products.md` (bytes total, days live, seam table over the ten days
   from the manifest, anything held/missing per satellite from `held`), delete the box credentials, SendMessage
   `earth-7e`: "DONE Stage B ten days + URL" or BLOCKED.

## What NOT to redo
The readers, the products stage, the two-stream fit, the page copy and the 12 Sep day are done and merged (PR #14).
Do not re-fit `cod_a`/`cod_asym`. Do not touch `cloud_tiles.py` or the tile sets. Do not fetch on the laptop.
