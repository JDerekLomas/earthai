#!/usr/bin/env bash
# Generic wait -> filter -> pack -> train chain. Run inside tmux on the GPU box.
# Usage: scripts/gpu/chain.sh <wait-for-session|none> <mode clouds|land> <tiles-dir> <raw-manifest> <dataset-dir> <zip> <run-name> <kimg> <resume.pkl|none>
# Packing is skipped if <zip> already exists (so a second run can share a dataset).
set -euo pipefail
cd /root/earthai
WAIT=$1; MODE=$2; TILES=$3; RAWM=$4; DSET=$5; ZIP=$6; RUN=$7; KIMG=$8; RESUME=$9
# train_sg2.sh cds into the stylegan3 tree, so the dataset path must be absolute.
case "$ZIP" in /*) ;; *) ZIP="/root/earthai/$ZIP" ;; esac
if [ "$WAIT" != none ]; then while tmux has-session -t "$WAIT" 2>/dev/null; do sleep 60; done; fi
echo "chain $RUN: wait over $(date)"
if [ ! -f "$ZIP" ]; then
  # another chain may be packing the same zip right now; wait for its lock
  while [ -f "$ZIP.lock" ]; do sleep 30; done
  if [ ! -f "$ZIP" ]; then
    touch "$ZIP.lock"
    .venv/bin/python scripts/build_dataset.py --mode "$MODE" --tiles "$TILES" --raw-manifest "$RAWM" --out "$DSET" --manifest-out "$DSET.manifest.csv" > "runs/build_$RUN.log" 2>&1
    tail -8 "runs/build_$RUN.log"
    /root/sg3env/bin/python /root/third_party/stylegan3/dataset_tool.py --source="$DSET" --dest="$ZIP" --resolution=256x256 > "runs/pack_$RUN.log" 2>&1
    rm -f "$ZIP.lock"
  fi
fi
ls -la "$ZIP"
exec scripts/gpu/train_sg2.sh "$ZIP" "/root/earthai/runs/$RUN" "$KIMG" "$RESUME"
