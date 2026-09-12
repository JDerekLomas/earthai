# Watch the first earthai training runs (handoff, 2026-09-12)

## Goal
Verify that the three queued training chains on the Scaleway GPU box start correctly when the
tile fetches finish, then report the first sample grids and FID trend. Report by posting ONE
comment on https://github.com/JDerekLomas/earthai/issues/1 (gh is authenticated on this Mac).

## Box
- `ssh root@51.159.165.126` (key already authorised). Repo at `/root/earthai`, notes in `docs/gpu-box.md`.
- tmux sessions: `fetch` (cloud tiles, log `runs/fetch.log`), `fetch_land` (`runs/fetch_land.log`),
  `train` (chain: waits for `fetch`, then builds `data/dataset`, packs `data/clouds256.zip`, trains
  `runs/sg2-clouds256`, log `runs/train_chain.log`), `train_scratch` (waits for `fetch`, shares the zip,
  trains `runs/sg2-clouds256-scratch` from scratch, log `runs/chain_scratch.log`), `train_land`
  (waits for `fetch_land`, builds `data/dataset_land`, packs `data/land256.zip`, trains `runs/sg2-land256`,
  log `runs/chain_land.log`).
- Training logs: `runs/<run>/<id>/log.txt` (lines starting `tick`), FID in `metric-fid50k_full.jsonl`,
  grids `fakes*.png` (256 px tiles, ~1.6 MB each; downscale before moving anything).

## Timeline (box clock is UTC; fetch started ~03:20 UTC 12 Sep)
- Cloud fetch ends ~08:00 UTC; land fetch ends ~09:00 UTC.
- First snapshot (100 kimg) for the cloud runs ~1 h after training starts.

## Definition of done
1. Sleep until ~08:15 UTC (use Monitor/until-loop or ScheduleWakeup; do NOT busy-poll).
2. Check `tmux ls` and the chain logs. If a chain died (session gone, Traceback in its log), read the
   error, fix it if it is a one-line fix (path, missing dep), restart the chain with the same command
   from `runs/*.log` history or `scripts/gpu/chain.sh --help` in the repo, and note it in the report.
3. Once `runs/sg2-clouds256/*/fakes000100.png` exists (or the first `fakes0*.png` after init), build a
   small comparison JPEG (top-left 5x2 tiles of reals.png, fakes_init.png, latest fakes, ~640 px wide,
   quality 70) exactly like `docs/bench_compare_24kimg.jpg`, scp it to `docs/` in
   `/Users/dereklomas/earthai`, commit and push (attribution trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`).
4. Post one issue comment: sessions alive/dead, dataset sizes (kept counts from `runs/build_*.log`),
   sec/kimg per run, FID values so far, link to the committed comparison image, and one sentence of
   qualitative judgement. Then stop. Do not stop the GPU instance.

## Do NOT
- Do not re-run fetches, delete data, or change training hyperparameters.
- Do not download videos or full-size PNGs to the Mac (Derek is on plane wifi).
- Do not stop the instance.
