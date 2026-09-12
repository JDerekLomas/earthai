#!/usr/bin/env bash
# Wait for the fetch tmux session to finish, then filter, pack and train. Run inside tmux.
# Usage: scripts/gpu/after_fetch.sh [kimg]
set -euo pipefail
cd /root/earthai
KIMG=${1:-3000}
while tmux has-session -t fetch 2>/dev/null; do sleep 60; done
echo "fetch done $(date)"; find data/tiles -name '*.jpg' | wc -l
.venv/bin/python scripts/build_dataset.py > runs/build_dataset.log 2>&1
tail -8 runs/build_dataset.log
.venv/bin/python scripts/grid.py --n 64 --out docs > /dev/null
/root/sg3env/bin/python /root/third_party/stylegan3/dataset_tool.py \
  --source=data/dataset --dest=data/clouds256.zip --resolution=256x256 > runs/pack.log 2>&1
ls -la data/clouds256.zip
exec scripts/gpu/train_sg2.sh /root/earthai/data/clouds256.zip /root/earthai/runs/sg2-clouds256 "$KIMG"
