"""Fetch HRRR analysis cloud fields for one place, cropped to the frame, one file per hour.

PRIOR ART: scripts/fetch_goes.py (GIBS tiles) and fetch_terrain.py (AWS terrain tiles) fetch
IMAGES on the frame grid. HRRR is a physics model on a Lambert conformal grid served as GRIB2,
so neither fits: this one reads the .idx sidecar, pulls single fields by HTTP byte range
(the whole file is ~135 MB; one field is ~1 MB), decodes them, and keeps only the window of
model cells that covers the frame.

    python scripts/fetch_hrrr.py --place california --start 2026-08-05 --days 40

Source: NOAA HRRR on AWS Open Data, bucket noaa-hrrr-bdp-pds, hourly analyses (f00) back to
2014-07-30, 3 km, free. Writes data/hrrr/<place>/YYYY-MM-DDTHHZ.npz with:
  SBT114   simulated GOES-11 channel 4 (10.7 um window) brightness temperature, K, float16.
           This is the field that matches GOES-R band 13 (10.3 um). TRAP: SBT113 is NOT band 13,
           it is GOES-11 CHANNEL 3, the 6.7 um WATER VAPOUR channel (reads 200-250 K everywhere;
           the window channel reads ~287 K over clear sea). Verified 2026-09-14 by decoding both.
  SBT113   the water-vapour channel, kept because it is a cheap second physics view.
  LCDC MCDC HCDC  low / middle / high cloud fraction, %, uint8 (255 = missing)
  TCDC     entire-atmosphere total cloud fraction, %, uint8. HRRR's "boundary layer cloud
           layer" TCDC is byte-identical to LCDC over this window (checked), so it is not kept.
  i0 j0    the crop's origin in HRRR grid indices (column, row), so it can be re-projected.
The crop window is computed from the frame's web-mercator bounds through the HRRR projection
(scripts/hrrr_grid.py); nothing is resampled here.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).parent))
from hrrr_grid import crop_window  # noqa: E402

BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
# (name in the .idx, level string in the .idx) -> key in the npz
FIELDS = {
    ("SBT114", "top of atmosphere"): "SBT114",
    ("SBT113", "top of atmosphere"): "SBT113",
    ("LCDC", "low cloud layer"): "LCDC",
    ("MCDC", "middle cloud layer"): "MCDC",
    ("HCDC", "high cloud layer"): "HCDC",
    ("TCDC", "entire atmosphere"): "TCDC",
}
UA = {"User-Agent": "earthai-hrrr-probe/0.1"}


def idx_ranges(idx_text: str) -> dict[str, tuple[int, int | None]]:
    """Byte range of every wanted field. The .idx lists message start offsets in order; a
    message ends where the next begins."""
    rows = [ln.split(":") for ln in idx_text.strip().splitlines()]
    starts = [int(r[1]) for r in rows]
    out = {}
    for k, r in enumerate(rows):
        key = (r[3], r[4])
        if key in FIELDS:
            end = starts[k + 1] - 1 if k + 1 < len(rows) else None
            out[FIELDS[key]] = (starts[k], end)
    return out


def decode(blob: bytes) -> np.ndarray:
    import eccodes
    h = eccodes.codes_new_from_message(blob)
    try:
        ny, nx = eccodes.codes_get(h, "Ny"), eccodes.codes_get(h, "Nx")
        return eccodes.codes_get_values(h).reshape(ny, nx)
    finally:
        eccodes.codes_release(h)


def fetch_hour(t: datetime, out: Path, win: tuple[int, int, int, int]) -> str:
    dst = out / (t.strftime("%Y-%m-%dT%HZ") + ".npz")
    if dst.exists():
        return "have"
    base = f"{BUCKET}/hrrr.{t:%Y%m%d}/conus/hrrr.t{t:%H}z.wrfsfcf00.grib2"
    r = requests.get(base + ".idx", headers=UA, timeout=60)
    if r.status_code == 404:
        return "missing"
    r.raise_for_status()
    ranges = idx_ranges(r.text)
    if len(ranges) < len(FIELDS):
        return f"partial-idx({len(ranges)})"
    i0, i1, j0, j1 = win
    arrays = {}
    for key, (a, b) in ranges.items():
        hdr = dict(UA, Range=f"bytes={a}-{'' if b is None else b}")
        g = requests.get(base, headers=hdr, timeout=120)
        g.raise_for_status()
        full = decode(g.content)
        crop = full[j0:j1, i0:i1].astype(np.float32)
        crop[crop > 9000] = np.nan                     # GRIB missing value comes out as 9999
        if key.startswith("SBT"):
            arrays[key] = crop.astype(np.float16)
        else:
            arrays[key] = np.where(np.isfinite(crop), np.clip(np.rint(crop), 0, 100), 255).astype(np.uint8)
    tmp = dst.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, i0=i0, j0=j0, **arrays)
    tmp.rename(dst)
    return "ok"


@click.command()
@click.option("--place", default="california")
@click.option("--start", default="2026-08-05", help="first UTC day")
@click.option("--days", default=40, type=int)
@click.option("--out", default=None, type=click.Path(path_type=Path), help="default data/hrrr/<place>")
@click.option("--workers", default=6, type=int)
def main(place, start, days, out, workers):
    out = out or Path("data/hrrr") / place
    out.mkdir(parents=True, exist_ok=True)
    win = crop_window(place)
    print(f"crop window cols {win[0]}:{win[1]} rows {win[2]}:{win[3]} -> {win[3]-win[2]}x{win[1]-win[0]} cells")
    t0 = datetime.strptime(start, "%Y-%m-%d")
    hours = [t0 + timedelta(hours=h) for h in range(days * 24)]
    tally: dict[str, int] = {}
    with ThreadPoolExecutor(workers) as ex:
        for t, res in zip(hours, ex.map(lambda t: _safe(t, out, win), hours)):
            tally[res] = tally.get(res, 0) + 1
            if res not in ("ok", "have"):
                print(f"  {t:%Y-%m-%dT%HZ} {res}")
    print(json.dumps(tally))
    (out / "fetch.json").write_text(json.dumps(dict(place=place, start=start, days=days, window=win, tally=tally), indent=1))


def _safe(t, out, win):
    try:
        return fetch_hour(t, out, win)
    except Exception as e:  # noqa: BLE001
        return f"error:{type(e).__name__}:{str(e)[:60]}"


if __name__ == "__main__":
    main()
