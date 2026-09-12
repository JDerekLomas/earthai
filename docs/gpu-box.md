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

Gotchas hit on first setup:
- `pkg_resources` missing under py3.10 + new setuptools: pin `setuptools<70`
- `Ninja is required`: the venv's `bin/` must be on PATH inside tmux
- `size of tensor a (512) must match b (256)` on `--resume`: pass `--cbase=16384` for paper256 nets

Measured: 13.7 s/kimg at 256 px, batch 32, ADA on, ~8.6 GB VRAM, GPU at 100%.

Sessions: `tmux ls` -> `fetch` (GIBS pull, log `runs/fetch.log`), `train` (see `scripts/gpu/`).
