#!/usr/bin/env bash
# Train StyleGAN2-ADA (via the NVlabs stylegan3 repo, --cfg=stylegan2) on a packed dataset.
# Verified on Scaleway L40S-1-48G, Ubuntu 24.04 GPU OS, torch 2.6+cu124, CUDA toolkit 12.6:
# 13.7 s/kimg at 256 px, batch 32, ~8.6 GB VRAM.
#
# Usage: scripts/gpu/train_sg2.sh <dataset.zip> <outdir> [kimg] [resume.pkl | none]
set -euo pipefail
DATA=${1:?dataset zip}
OUT=${2:?outdir}
KIMG=${3:-3000}
RESUME=${4:-/root/pretrained/lsundog-res256-paper256-kimg100000-noaug.pkl}
SG3=${SG3:-/root/third_party/stylegan3}
PY=${PY:-/root/sg3env/bin/python}
export CUDA_HOME=${CUDA_HOME:-/usr/local/cuda-12.6}
export PATH=$(dirname "$PY"):$CUDA_HOME/bin:$PATH
export TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST:-8.9}   # L40S; set for your card
mkdir -p "$OUT"
cd "$SG3"
# --cbase=16384 matches the paper256 pretrained networks (half-width channels); required for --resume.
RESUME_ARG=(--resume="$RESUME"); [ "$RESUME" = none ] && RESUME_ARG=()   # "none" = train from scratch
exec "$PY" train.py --outdir="$OUT" --cfg=stylegan2 --cbase=16384 \
  --data="$DATA" --gpus=1 --batch=32 --gamma=1 --mirror=1 --aug=ada \
  "${RESUME_ARG[@]}" --kimg="$KIMG" --tick=4 --snap=25 --metrics=fid50k_full
