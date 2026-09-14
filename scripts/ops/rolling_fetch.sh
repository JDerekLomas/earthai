#!/bin/zsh
# Daily rolling GOES fetch: keep the archive growing while NASA GIBS still has the frames.
# GIBS holds roughly the last 35 days at 10-minute cadence; every day not fetched is lost.
# Idempotent (existing frames are skipped), so missing a run costs nothing as long as the next
# one happens within a month. Installed as a launchd agent, com.dereklomas.earthai-goes,
# 04:00 local daily; logs to scratch/goes_rolling.log. Run by hand: scripts/ops/rolling_fetch.sh 5
#
# california_x3 / _ir  the deck, GOES-West, z6 3x3 (the month page and the HRRR probe)
# conus / conus_ir     the whole lower 48, GOES-East, z5 7x4 (HRRR covers all of it)
cd "$(dirname "$0")/../.." || exit 1
DAYS=${1:-3}
P=.venv/bin/python
echo "== $(date -u +%FT%TZ) rolling fetch, last $DAYS days"
$P scripts/fetch_goes.py --place california --days $DAYS --allday --span 3 --workers 6
$P scripts/fetch_goes.py --place california --days $DAYS --allday --span 3 --layer ir --workers 6
$P scripts/fetch_goes.py --place conus --days $DAYS --allday --workers 6
$P scripts/fetch_goes.py --place conus --days $DAYS --allday --layer ir --workers 6
echo "== done $(date -u +%FT%TZ): california $(ls data/goes/california_x3/*.jpg | wc -l | tr -d ' ') geo, $(ls data/goes/california_x3_ir/*.jpg | wc -l | tr -d ' ') ir; conus $(ls data/goes/conus/*.jpg 2>/dev/null | wc -l | tr -d ' ') geo, $(ls data/goes/conus_ir/*.jpg 2>/dev/null | wc -l | tr -d ' ') ir"
