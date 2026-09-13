# GPU box (Scaleway) setup notes

Instance: `earthai-gpu`, L40S-1-48G, zone fr-par-2, project MakeMode, €1.47/h billed per minute.
Created 2026-09-12. **Stop it when idle**: `scw instance server stop <id> zone=fr-par-2`
(the 300 GB block volume keeps data and checkpoints while stopped, ~€0.08/GB/month).

Layout on the box:
- `/root/earthai` this repo, `.venv` (py3.12) for the data scripts
- `/root/third_party/stylegan3` NVlabs repo; `/root/sg3env` (py3.10, torch 2.6+cu124, setuptools<70, ninja)
- `/root/pretrained/*.pkl` 256 px transfer-learning source nets (ffhq, lsundog)
- CUDA toolkit 12.6 from NVIDIA's apt repo (the GPU OS image ships drivers but no nvcc; the
  cuda-keyring .deb conflicts with the image's existing repo entry, use the existing one)

Measured throughput, same recipe (256 px, batch 32, cbase 16384, ADA), same dataset:

| card | s/kimg | EUR/h | EUR per 1000 kimg | verdict |
|---|---|---|---|---|
| L40S-1-48G | 13.7 | 1.47 | **5.59** | best value |
| H100-1-80G | 8.9 | 2.87 | 7.10 | 1.54x faster, 27% dearer |

The H100's spec ratio predicts 2.1x; it delivers 1.54x, because a 256 px StyleGAN2 does not
saturate it -- 12.4 GB of 80 in use. Break-even needed 1.95x. **Buy the H100 for wall-clock,
never for cost**, and measure before believing a spec sheet.

Gotchas hit on first setup:
- `pkg_resources` missing under py3.10 + new setuptools: pin `setuptools<70`
- `Ninja is required`: the venv's `bin/` must be on PATH inside tmux
- `size of tensor a (512) must match b (256)` on `--resume`: pass `--cbase=16384` for paper256 nets

Gotchas hit rebuilding on the H100 (2026-09-13), both invisible until nvcc runs:
- **`build-essential` is not in the GPU OS image.** nvcc needs a host compiler, so the
  custom ops fail with `gcc: No such file or directory` / `nvcc fatal: Failed to preprocess
  host compiler properties` even though CUDA installed fine.
- **A `setsid nohup` script inherits a minimal PATH.** `export PATH=...:$PATH` then does not
  contain `/usr/bin`, so gcc is installed and invisible, and the error is identical to not
  having installed it. Write the full PATH explicitly in any detached script.

Measured: 13.7 s/kimg at 256 px, batch 32, ADA on, ~8.6 GB VRAM, GPU at 100%.

Sessions: `tmux ls` -> `fetch` (GIBS pull, log `runs/fetch.log`), `train` (see `scripts/gpu/`).
