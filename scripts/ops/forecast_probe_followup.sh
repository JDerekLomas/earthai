#!/bin/zsh
# Waits for the HRRR f06/f12 fetches (started 14 Sep 2026) to finish, scores both forecast
# hours with the agreement probe, commits the results, deploys the site from main, and checks
# the live JSON. Deterministic follow-up; needs no model. Log: scratch/forecast_followup.log
cd "$(dirname "$0")/../.." || exit 1
P=.venv/bin/python
echo "== $(date -u +%FT%TZ) waiting for fetch_hrrr processes"
while pgrep -f "fetch_hrrr.py" > /dev/null; do sleep 60; done
echo "== $(date -u +%FT%TZ) fetched: f06 $(ls data/hrrr/california_f06/*.npz | wc -l | tr -d ' ')  f12 $(ls data/hrrr/california_f12/*.npz | wc -l | tr -d ' ')"
for fh in 6 12; do
  $P -u scripts/hrrr_agreement.py --place california --fhour $fh || { echo "analysis f$fh FAILED"; exit 1; }
done
for fh in 06 12; do
  $P -c "import json; J=json.load(open('site/hrrr/california_f$fh/agreement.json')); print('f$fh cloud-mask r', J['cloud']['overall']['LCDC']['anom_pearson']['mean'], 'ir r', J['temperature']['overall']['anom_pearson']['mean'])" || exit 1
done
git add site/hrrr/california_f06 site/hrrr/california_f12 && git commit -q -m "earthai: HRRR 6 h and 12 h forecasts scored against GOES (forecast-hour probe)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" && git push -q origin main
[ "$(git branch --show-current)" = "main" ] || { echo "not on main, not deploying"; exit 1; }
(cd site && npx vercel --prod --yes > ../scratch/deploy_forecast.log 2>&1) && echo "deployed" || echo "DEPLOY FAILED"
sleep 20
for fh in 06 12; do curl -s -o /dev/null -w "live f$fh %{http_code}\n" https://earthai-scales.vercel.app/hrrr/california_f$fh/agreement.json; done
echo "== $(date -u +%FT%TZ) DONE" | tee scratch/forecast_probe.DONE
