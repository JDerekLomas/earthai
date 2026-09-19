#!/bin/sh
# Finish the ten-day official build: wait for the box's pipeline, bring the day clips back, put them on R2, replace the
# ten days in the live manifest, commit, deploy. Idempotent; re-run after any failure. Run from the repo (a worktree is
# fine: data/ must be reachable as data/ and the site is deployed with deploy_site.sh's recipe from origin/main).
#
# PRIOR ART: scripts/deploy_site.sh (deploy), scripts/r2_sync.py (upload), fetch_clouds.py encode --merge-into (manifest).
# This only sequences them for the one job described in .claude/handoffs/2026-09-19-official-products.md, step 8.
set -e
BOX=root@51.15.76.176
REPO=$(cd "$(dirname "$0")/../.." && pwd)                  # the checkout this runs in (a worktree is fine)
MAIN=/Users/dereklomas/sourcelibrary/earthai                 # data/ and the venv live only in the main checkout
PY=$MAIN/.venv/bin/python
DATA=$MAIN/data/clouds
DAYS=$DATA/official/days10
cd "$REPO"
echo "waiting for PIPELINE DONE on the box"
until ssh -o ConnectTimeout=20 $BOX 'grep -q "PIPELINE DONE" /data/official/tenday.log' 2>/dev/null; do sleep 600; done
mkdir -p "$DAYS"
rsync -a --partial $BOX:/data/official/clouds/days/ "$DAYS/"
ls "$DAYS" | grep -c "clouds_v2_.*[0-9].mp4$" | xargs -I{} echo "{} day clips"
$PY scripts/r2_sync.py "$DAYS:globe" --skip .log,.txt,.npy,.npz,.jsonl,.DS_Store,.png,.json --done "$DATA/r2_done.jsonl"
# the box's manifest replaces every day it carries in the live manifest (the merge keeps `tiles` and the first day's poster)
DAYS="$DAYS" $PY - <<'EOF'
import json, sys, os
sys.path.insert(0, "scripts")
os.environ.setdefault("CLOUDS_ROOT", os.environ["DAYS"] + "/..")
import fetch_clouds as fc
live = json.load(open("site/globe/clouds.json"))
new = json.load(open(os.environ["DAYS"] + "/clouds_v2.json"))
m = fc.merge_manifests(live, new)
json.dump(m, open("site/globe/clouds.json", "w"))
print("days", len(m["days"]), "official", m["official_days"], "tiles", m.get("tiles"), "sizes", m["sizes"])
EOF
for d in $(ls "$DAYS" | grep -o "clouds_v2_2026-[0-9-]*\.mp4" | sed 's/clouds_v2_//; s/.mp4//'); do
  curl -sfI "https://clouds.sourcelibrary.org/globe/clouds_v2_$d.mp4" >/dev/null || { echo "R2 missing clouds_v2_$d.mp4"; exit 1; }
done
git add site/globe/clouds.json
git commit -q -m "globe: the ten days from the agencies' own data (official-products, box build)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>" || true
git push -q
echo "manifest committed and pushed on $(git branch --show-current); now: PR to main, merge, deploy (deploy_site.sh recipe), tell tile-sets"
