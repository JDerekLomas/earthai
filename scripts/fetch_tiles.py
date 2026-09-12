"""Pull MODIS / VIIRS true-colour tiles from NASA GIBS for the cloud regions.

GIBS serves daily global true-colour imagery as a web-mercator tile pyramid.
We sample K random tiles per region per date at a fixed zoom, save them as JPEG,
and append one manifest row per tile. Resumable: existing files are skipped.

Zoom guide (web mercator, 256 px tiles, at the equator):
  z7  ~1.2 km/px   tile covers ~313 km   cyclones, fronts, large decks
  z8  ~0.6 km/px   tile covers ~156 km   cells, streets, most cloud fields

Usage:
  python scripts/fetch_tiles.py --zoom 8 --per-region 6 --every 5 --workers 8
  python scripts/fetch_tiles.py --regions california namibia --start 2020-01-01 --end 2020-12-31
"""
from __future__ import annotations

import csv
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import click
import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from regions import REGIONS, sample_dates  # noqa: E402

GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{day}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"

LAYERS = {
    "terra": ("MODIS_Terra_CorrectedReflectance_TrueColor", date(2000, 2, 24)),
    "aqua": ("MODIS_Aqua_CorrectedReflectance_TrueColor", date(2002, 7, 3)),
    "snpp": ("VIIRS_SNPP_CorrectedReflectance_TrueColor", date(2015, 11, 24)),
    "noaa20": ("VIIRS_NOAA20_CorrectedReflectance_TrueColor", date(2018, 1, 1)),
}

HEADERS = {"User-Agent": "earthai-cloud-gan/0.1 (research; github.com/JDerekLomas/earthai)"}
MANIFEST_FIELDS = ["path", "region", "regime", "sensor", "date", "z", "x", "y", "lon", "lat"]


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_to_lonlat(x: int, y: int, z: int) -> tuple[float, float]:
    """Centre of tile."""
    n = 2**z
    lon = (x + 0.5) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return lon, lat


def fetch_one(session: requests.Session, job: dict, out_dir: Path, retries: int = 3) -> dict | None:
    layer, _ = LAYERS[job["sensor"]]
    url = GIBS.format(layer=layer, day=job["date"], z=job["z"], y=job["y"], x=job["x"])
    dest = out_dir / job["regime"] / f'{job["sensor"]}_{job["date"]}_{job["z"]}_{job["x"]}_{job["y"]}.jpg'
    if dest.exists():
        return None
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                lon, lat = tile_to_lonlat(job["x"], job["y"], job["z"])
                return {**job, "path": str(dest.relative_to(out_dir.parent)), "lon": f"{lon:.2f}", "lat": f"{lat:.2f}"}
            if r.status_code in (400, 404):
                return None  # no imagery for that day/layer
            time.sleep(2**attempt)
        except requests.RequestException:
            time.sleep(2**attempt)
    return None


@click.command()
@click.option("--zoom", default=8, type=int, help="web mercator zoom (7 or 8)")
@click.option("--per-region", default=6, type=int, help="random tiles per region per date per sensor")
@click.option("--every", default=5, type=int, help="sample every Nth day")
@click.option("--start", default="2015-01-01")
@click.option("--end", default="2025-12-31")
@click.option("--sensors", default="terra,aqua,snpp,noaa20")
@click.option("--regions", "region_names", multiple=True, help="subset of region names")
@click.option("--workers", default=8, type=int, help="concurrent requests (be polite to GIBS)")
@click.option("--out", default="data/tiles", type=click.Path(path_type=Path))
@click.option("--seed", default=0, type=int)
@click.option("--limit", default=0, type=int, help="stop after N downloads (for testing)")
def main(zoom, per_region, every, start, end, sensors, region_names, workers, out, seed, limit):
    rng = random.Random(seed)
    regions = [r for r in REGIONS if not region_names or r.name in region_names]
    sensor_list = [s.strip() for s in sensors.split(",")]
    days = sample_dates(date.fromisoformat(start), date.fromisoformat(end), every, seed)

    jobs = []
    for d in days:
        for s in sensor_list:
            if d < LAYERS[s][1]:
                continue
            for reg in regions:
                x0, y1 = lonlat_to_tile(reg.lon_min, reg.lat_min, zoom)
                x1, y0 = lonlat_to_tile(reg.lon_max, reg.lat_max, zoom)
                for _ in range(per_region):
                    jobs.append({
                        "region": reg.name, "regime": reg.regime, "sensor": s, "date": d.isoformat(),
                        "z": zoom, "x": rng.randint(min(x0, x1), max(x0, x1)), "y": rng.randint(min(y0, y1), max(y0, y1)),
                    })
    rng.shuffle(jobs)
    if limit:
        jobs = jobs[:limit]
    click.echo(f"{len(jobs)} tile jobs over {len(days)} days, {len(regions)} regions, {len(sensor_list)} sensors -> {out}")

    out.mkdir(parents=True, exist_ok=True)
    manifest = out.parent / "manifest_raw.csv"
    new_file = not manifest.exists()
    written = 0
    with open(manifest, "a", newline="") as mf, requests.Session() as session:
        w = csv.DictWriter(mf, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(fetch_one, session, j, out) for j in jobs]
            for f in tqdm(as_completed(futures), total=len(futures), unit="tile", smoothing=0.05):
                row = f.result()
                if row:
                    w.writerow(row)
                    written += 1
                    if written % 200 == 0:
                        mf.flush()
    click.echo(f"downloaded {written} new tiles; manifest at {manifest}")


if __name__ == "__main__":
    main()
