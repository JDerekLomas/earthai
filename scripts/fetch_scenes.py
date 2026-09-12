"""Fetch contiguous N x N tile mosaics -- scenes -- instead of isolated tiles.

A scene is what a curator can judge: at z8 a 5x5 mosaic is ~725 km across, wide
enough to see a swath edge, a coastline creeping in, a storm system, or that a
region is just haze that day. Keep or reject the scene, then cut_scenes.py breaks
the kept ones into training tiles at whatever window size the run wants -- so one
fetch serves 256 px crops (145 km at z8), 512 -> 256 (290 km) and 1024 -> 256 (580 km).

    python scripts/fetch_scenes.py --out data/scenes --per-region 6
    python scripts/fetch_scenes.py --regions sahara,ganges_delta --source s2 --zoom 11 --per-region 4

Writes data/scenes/<id>.jpg, a 384 px preview in data/scenes/low/, and scenes.jsonl.
"""
from __future__ import annotations

import io
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402
from regions import REGIONS  # noqa: E402
from scale_ladder import SOURCES, UA, mpp  # noqa: E402

TILE = 256


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tile_to_lonlat(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def fetch_tile(src, z, x, y, day):
    try:
        r = requests.get(SOURCES[src]["url"](z, x, y, day), headers=UA, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200 or len(r.content) < 500:
        return None
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def fetch_scene(src, z, x0, y0, n, day):
    """n x n mosaic with top-left tile (x0, y0). None if any tile is missing."""
    canvas = Image.new("RGB", (n * TILE, n * TILE))
    with ThreadPoolExecutor(8) as ex:
        tiles = list(ex.map(lambda xy: fetch_tile(src, z, xy[0], xy[1], day), [(x0 + i, y0 + j) for j in range(n) for i in range(n)]))
    if any(t is None for t in tiles):
        return None
    for k, t in enumerate(tiles):
        canvas.paste(t, ((k % n) * TILE, (k // n) * TILE))
    return canvas


@click.command()
@click.option("--out", default="data/scenes", type=click.Path(path_type=Path))
@click.option("--source", default="modis", type=click.Choice(list(SOURCES)))
@click.option("--zoom", default=8, type=int)
@click.option("--n", default=5, type=int, help="scene is n x n tiles")
@click.option("--per-region", default=6, type=int)
@click.option("--regions", "region_names", default="", help="comma-separated; default: every region in regions.py")
@click.option("--surface", default=None, type=click.Choice(["ocean", "land"]), help="restrict to one surface")
@click.option("--max-black", default=0.15, type=float, help="drop scenes with more nodata than this")
@click.option("--years", default="2019-2023")
@click.option("--seed", default=0, type=int)
def main(out, source, zoom, n, per_region, region_names, surface, max_black, years, seed):
    rng = random.Random(seed)
    y0, y1 = (int(v) for v in years.split("-"))
    wanted = {n for n in region_names.split(",") if n}
    regions = [r for r in REGIONS if (not wanted or r.name in wanted) and (not surface or r.surface == surface)]
    out.mkdir(parents=True, exist_ok=True)
    (out / "low").mkdir(exist_ok=True)
    log = open(out / "scenes.jsonl", "a")
    have = {json.loads(l)["id"] for l in open(out / "scenes.jsonl")} if (out / "scenes.jsonl").exists() else set()
    got = 0
    for reg in regions:
        made, tries = 0, 0
        while made < per_region and tries < per_region * 4:
            tries += 1
            lon = rng.uniform(reg.lon_min, reg.lon_max)
            lat = rng.uniform(reg.lat_min, reg.lat_max)
            x, y = lonlat_to_tile(lon, lat, zoom)
            day = f"{rng.randint(y0, y1)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}" if source != "s2" else "composite"
            sid = f"{source}_z{zoom}_{reg.name}_{day}_{x}_{y}"
            if sid in have:
                continue
            im = fetch_scene(source, zoom, x, y, n, day)
            if im is None:
                continue
            s = stats(np.asarray(im.resize((512, 512), Image.BILINEAR)))
            if s["black"] > max_black:
                continue
            im.save(out / f"{sid}.jpg", quality=88)
            im.resize((384, 384), Image.LANCZOS).save(out / "low" / f"{sid}.jpg", quality=72)
            lon0, lat0 = tile_to_lonlat(x, y, zoom)
            lon1, lat1 = tile_to_lonlat(x + n, y + n, zoom)
            m = mpp(zoom, (lat0 + lat1) / 2)
            row = dict(id=sid, source=source, zoom=zoom, n=n, px=n * TILE, region=reg.name, regime=reg.regime, surface=reg.surface,
                       date=day, x=x, y=y, bbox=[round(lon0, 3), round(lat1, 3), round(lon1, 3), round(lat0, 3)],
                       mpp=round(m), km=round(m * n * TILE / 1000), **{k: round(v, 3) for k, v in s.items()})
            log.write(json.dumps(row) + "\n"); log.flush()
            made += 1; got += 1
            click.echo(f"  {sid}  {row['km']} km  cloud {s['cloud']:.2f} black {s['black']:.2f}")
        click.echo(f"{reg.name}: {made} scenes")
    click.echo(f"{got} new scenes -> {out}")


if __name__ == "__main__":
    main()
