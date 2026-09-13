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
import io
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from regions import regions_for, sample_dates  # noqa: E402

GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{day}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"

LAYERS = {
    "terra": ("MODIS_Terra_CorrectedReflectance_TrueColor", date(2000, 2, 24)),
    "aqua": ("MODIS_Aqua_CorrectedReflectance_TrueColor", date(2002, 7, 3)),
    "snpp": ("VIIRS_SNPP_CorrectedReflectance_TrueColor", date(2015, 11, 24)),
    "noaa20": ("VIIRS_NOAA20_CorrectedReflectance_TrueColor", date(2018, 1, 1)),
}

HEADERS = {"User-Agent": "earthai-cloud-gan/0.1 (research; github.com/JDerekLomas/earthai)"}
MANIFEST_FIELDS = ["path", "region", "regime", "sensor", "date", "z", "x", "y", "lon", "lat", "ss"]


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


def get_tile(session, layer, day, z, x, y, retries=3):
    """Raw bytes for one tile, or None. Distinguishes "no imagery" from "try again"."""
    url = GIBS.format(layer=layer, day=day, z=z, y=y, x=x)
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
            if r.status_code in (400, 404):
                return None                      # no imagery for that day/layer
            time.sleep(2**attempt)
        except requests.RequestException:
            time.sleep(2**attempt)
    return None


def supersampled(session, layer, day, z, x, y, ss: int, retries=3):
    """The same ground as tile (z, x, y), fetched as an ss x ss block at zoom z+log2(ss)
    and area-averaged back down to 256 px.

    This is not about getting a bigger picture -- the output is the same 256 px. It is
    about WHICH 256 px. Measured against the deeply-sampled truth, the tile server's own
    coarse render carries 3-48% MORE energy above half Nyquist than it should: it is
    aliasing and edge-sharpening, not resolution. Averaging the children down converges on
    the truth (PSNR 30.5 -> 35.2 -> 41.1 over the Namib at z11), compresses ~10% smaller,
    and stops a GAN learning the tile server's artifacts as though they were cloud texture.
    """
    k = int(math.log2(ss))
    zz, x0, y0 = z + k, x * ss, y * ss
    canvas = Image.new("RGB", (256 * ss, 256 * ss))
    for dy in range(ss):
        for dx in range(ss):
            b = get_tile(session, layer, day, zz, x0 + dx, y0 + dy, retries)
            if b is None:
                return None                      # a partial mosaic is not the same ground
            canvas.paste(Image.open(io.BytesIO(b)).convert("RGB"), (256 * dx, 256 * dy))
    return canvas.resize((256, 256), Image.BOX)  # BOX is the area average; LANCZOS re-rings


def fetch_one(session: requests.Session, job: dict, out_dir: Path, retries: int = 3,
              ss: int = 1, quality: int = 92) -> dict | None:
    layer, _ = LAYERS[job["sensor"]]
    tag = "" if ss == 1 else f"_ss{ss}"
    dest = out_dir / job["regime"] / f'{job["sensor"]}_{job["date"]}_{job["z"]}_{job["x"]}_{job["y"]}{tag}.jpg'
    if dest.exists():
        return None
    if ss == 1:
        b = get_tile(session, layer, job["date"], job["z"], job["x"], job["y"], retries)
        if b is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b)
    else:
        im = supersampled(session, layer, job["date"], job["z"], job["x"], job["y"], ss, retries)
        if im is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, quality=quality)
    lon, lat = tile_to_lonlat(job["x"], job["y"], job["z"])
    return {**job, "path": str(dest.relative_to(out_dir.parent)),
            "lon": f"{lon:.2f}", "lat": f"{lat:.2f}", "ss": ss}


@click.command()
@click.option("--zoom", default=8, type=int, help="web mercator zoom (7 or 8)")
@click.option("--per-region", default=6, type=int, help="random tiles per region per date per sensor")
@click.option("--every", default=5, type=int, help="sample every Nth day")
@click.option("--start", default="2015-01-01")
@click.option("--end", default="2025-12-31")
@click.option("--sensors", default="terra,aqua,snpp,noaa20")
@click.option("--surface", default="ocean", type=click.Choice(["ocean", "land", "all"]), help="ocean = cloud set, land = landscape set")
@click.option("--regions", "region_names", multiple=True, help="subset of region names")
@click.option("--workers", default=8, type=int, help="concurrent requests (be polite to GIBS)")
@click.option("--out", default="data/tiles", type=click.Path(path_type=Path))
@click.option("--manifest", default=None, type=click.Path(path_type=Path), help="default: <out parent>/manifest_raw.csv")
@click.option("--seed", default=0, type=int)
@click.option("--limit", default=0, type=int, help="stop after N downloads (for testing)")
@click.option("--supersample", "ss", default=1, type=click.Choice(["1", "2", "4"]),
              help="fetch NxN children a zoom deeper and area-average to 256 px; costs N^2 requests "
                   "and removes the server's aliasing (see scripts/supersample_check.py)")
def main(zoom, per_region, every, start, end, sensors, surface, region_names, workers, out, manifest, seed, limit, ss):
    ss = int(ss)
    rng = random.Random(seed)
    regions = [r for r in regions_for(surface) if not region_names or r.name in region_names]
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
    note = "" if ss == 1 else f"  (supersample x{ss}: {ss*ss} requests per tile, {len(jobs)*ss*ss} total)"
    click.echo(f"{len(jobs)} tile jobs over {len(days)} days, {len(regions)} regions, {len(sensor_list)} sensors -> {out}{note}")

    out.mkdir(parents=True, exist_ok=True)
    manifest = manifest or out.parent / "manifest_raw.csv"
    new_file = not manifest.exists()
    written = 0
    with open(manifest, "a", newline="") as mf, requests.Session() as session:
        w = csv.DictWriter(mf, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(fetch_one, session, j, out, 3, ss) for j in jobs]
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
