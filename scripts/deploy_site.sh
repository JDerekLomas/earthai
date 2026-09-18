#!/bin/sh
# Deploy site/ to Vercel production from a clean copy: HEAD's tracked files plus the gitignored data assets
# (cloud clips, tiles, the Vercel project link). Other sessions edit this checkout concurrently; a deploy
# straight from the working tree would ship their half-finished edits. Run from the repo root on main.
set -e
cd "$(dirname "$0")/.."
[ "$(git branch --show-current)" = main ] || { echo "not on main"; exit 1; }
pgrep -f "vercel --prod" >/dev/null && { echo "a vercel deploy is already running"; exit 1; }
D=$(mktemp -d /tmp/earthai-deploy.XXXXXX)
git archive HEAD site | tar -x -C "$D"
# every gitignored asset under site/ (clips, tiles, the Vercel project link), never env or node_modules
git ls-files --others --ignored --exclude-standard site | grep -vE '/node_modules/|\.env|\.DS_Store' > "$D/assets.txt"
rsync -a --files-from="$D/assets.txt" . "$D/"
echo "deploying $(git rev-parse --short HEAD) from $D ($(du -sh "$D" | cut -f1))"
cd "$D/site" && npx vercel --prod --yes 2>&1 | grep -E "Production|Aliased|Error|error" | head -5
rm -rf "$D"
