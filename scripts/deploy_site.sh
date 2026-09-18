#!/bin/sh
# Deploy site/ to Vercel production from a clean copy: HEAD's tracked files plus the gitignored data assets
# (cloud clips, tiles, the Vercel project link). Other sessions edit this checkout concurrently; a deploy
# straight from the working tree would ship their half-finished edits. Run from the repo root on main.
#
# KEEP_LIVE="globe/clouds.mp4 globe/clouds_2k.mp4 globe/clouds.json" sh scripts/deploy_site.sh
#   ships the copies currently live at $SITE for those site-relative paths instead of the working tree's.
#   Use it when another session has regenerated an asset for a page change that is not in HEAD yet
#   (2026-09-19: the cloud-height session rewrote clouds.mp4 4096x1520 -> 4096x2272 while HEAD's page
#   still read the 1520-tall layout; deploying the working tree would have shipped mush).
# The globe clip is checked against clouds.json before upload: the frame height must be height + code.strip.
set -e
cd "$(dirname "$0")/.."
SITE=${SITE:-https://earthai-scales.vercel.app}
[ "$(git branch --show-current)" = main ] || { echo "not on main"; exit 1; }
pgrep -f "vercel --prod" >/dev/null && { echo "a vercel deploy is already running"; exit 1; }
D=$(mktemp -d /tmp/earthai-deploy.XXXXXX)
git archive HEAD site | tar -x -C "$D"
# every gitignored asset under site/ (clips, tiles, the Vercel project link), never env or node_modules
git ls-files --others --ignored --exclude-standard site | grep -vE '/node_modules/|\.env|\.DS_Store' > "$D/assets.txt"
for p in $KEEP_LIVE; do
  grep -vx "site/$p" "$D/assets.txt" > "$D/assets.tmp" && mv "$D/assets.tmp" "$D/assets.txt"
  mkdir -p "$D/site/$(dirname "$p")"
  curl -sSf -o "$D/site/$p" "$SITE/$p" || { echo "could not fetch live $p"; exit 1; }
  echo "kept live $p ($(wc -c < "$D/site/$p") bytes)"
done
rsync -a --files-from="$D/assets.txt" . "$D/"
if [ -f "$D/site/globe/clouds.json" ] && [ -f "$D/site/globe/clouds.mp4" ] && command -v ffprobe >/dev/null; then
  want=$(python3 -c "import json; c=json.load(open('$D/site/globe/clouds.json')); print(c['height']+c['code'].get('strip',0))")
  have=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$D/site/globe/clouds.mp4")
  [ "$want" = "$have" ] || { echo "globe/clouds.mp4 is $have px tall but clouds.json says $want: another session's clip? use KEEP_LIVE"; rm -rf "$D"; exit 1; }
fi
echo "deploying $(git rev-parse --short HEAD) from $D ($(du -sh "$D" | cut -f1))"
cd "$D/site" && npx vercel --prod --yes 2>&1 | grep -E "Production|Aliased|Error|error" | head -5
rm -rf "$D"
