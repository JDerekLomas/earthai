"""Build the tile-triage bundle: sprite sheets + tiles.json for the curation page.

Two kinds of tile go in:
  A. what we already train on -- a stratified sample of the built datasets, clouds by
     cloud-fraction bucket and land by region, straight from their manifests;
  B. alternatives -- fresh fetches from the same regions at other sources and zooms
     (MODIS z7/z9, Sentinel-2 z11/z12), so a source or footprint can be judged next to
     the incumbent instead of in the abstract.

Every tile lands in a 10x10 sprite sheet at native 256 px (one JPEG per hundred tiles,
so the page ships a dozen files, not a thousand) and one row in tiles.json carrying
the facets the page filters on. Run on the GPU box, where the datasets are:

    .venv/bin/python scripts/curate_build.py --out /root/curate_bundle
"""
from __future__ import annotations

import csv
import io
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402  same cloud/black/std numbers the filter uses
from regions import REGIONS  # noqa: E402
from scale_ladder import SOURCES, UA, mpp  # noqa: E402

CELL, PER_SHEET, COLS = 256, 100, 10
BUCKETS = [(0.12, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.85), (0.85, 0.95), (0.95, 1.01)]

# set B: (region name, source, zoom) -> tiles to fetch
ALTERNATIVES = [
    ("namibia", "modis", 7), ("namibia", "modis", 9),
    ("tradewind_atlantic", "modis", 7), ("tradewind_atlantic", "modis", 9),
    ("north_atlantic", "modis", 7), ("north_atlantic", "modis", 9),
    ("sahara", "s2", 11), ("sahara", "s2", 12),
    ("himalaya", "s2", 11), ("himalaya", "s2", 12),
    ("us_midwest", "s2", 11), ("us_midwest", "s2", 12),
    ("ganges_delta", "s2", 11), ("ganges_delta", "s2", 12),
    ("amazon", "s2", 11), ("amazon", "s2", 12),
]


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def fetch_one(src, z, region, rng):
    """One random tile inside the region box; a few tries, since GIBS has swath gaps and EOX has no ocean."""
    for _ in range(4):
        lon = rng.uniform(region.lon_min, region.lon_max)
        lat = rng.uniform(region.lat_min, region.lat_max)
        x, y = lonlat_to_tile(lon, lat, z)
        day = f"{rng.randint(2019, 2023)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        try:
            r = requests.get(SOURCES[src]["url"](z, x, y, day), headers=UA, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code == 200 and len(r.content) > 800:
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            import numpy as np
            s = stats(np.asarray(im))
            if s["black"] > 0.5:  # a mostly-empty swath edge is not a sample of anything
                continue
            return im, dict(lon=round(lon, 3), lat=round(lat, 3), date=day, **{k: round(v, 3) for k, v in s.items()})
    return None, None


def sample_manifest(manifest, dataroot, groups, n, rng):
    rows = list(csv.DictReader(open(manifest)))
    out = []
    for label, keep in groups:
        sel = [r for r in rows if keep(r)]
        for r in rng.sample(sel, min(n, len(sel))):
            out.append((label, r, Path(dataroot) / r["path"]))
    return out


@click.command()
@click.option("--out", default="curate_bundle", type=click.Path(path_type=Path))
@click.option("--per-bucket", default=100, type=int, help="clouds tiles per cloud-fraction bucket")
@click.option("--per-region", default=40, type=int, help="land tiles per region")
@click.option("--per-alt", default=25, type=int, help="fresh tiles per (region, source, zoom)")
@click.option("--data", default="data", type=click.Path(path_type=Path))
def main(out, per_bucket, per_region, per_alt, data):
    rng = random.Random(0)
    tiles = []  # (image, meta)

    # A. the incumbents
    for label, r, p in sample_manifest(data / "manifest.csv", data, [
        (f"{lo:.2f}-{hi:.2f}", (lambda lo=lo, hi=hi: lambda r: lo <= float(r["cloud_frac"]) < hi)()) for lo, hi in BUCKETS
    ], per_bucket, rng):
        if p.exists():
            tiles.append((Image.open(p).convert("RGB"), dict(
                set="clouds256", source=r["sensor"].replace("aqua", "modis").replace("terra", "modis").replace("snpp", "viirs").replace("noaa20", "viirs"),
                zoom=int(r["z"]), region=r["region"], regime=r["regime"], date=r["date"], bucket=label,
                cf=float(r["cloud_frac"]), sd=float(r["lum_std"]), lm=float(r["lum_mean"]), lat=float(r["lat"]))))
    regimes = sorted({r["regime"] for r in csv.DictReader(open(data / "dataset_land.manifest.csv"))})
    for label, r, p in sample_manifest(data / "dataset_land.manifest.csv", data, [
        (g, (lambda g=g: lambda r: r["regime"] == g)()) for g in regimes
    ], per_region, rng):
        if p.exists():
            tiles.append((Image.open(p).convert("RGB"), dict(
                set="land256", source="modis", zoom=int(r["z"]), region=r["region"], regime=r["regime"], date=r["date"],
                bucket="land", cf=float(r["cloud_frac"]), sd=float(r["lum_std"]), lm=float(r["lum_mean"]), lat=float(r["lat"]))))
    click.echo(f"incumbents: {len(tiles)}")

    # B. the alternatives, fetched fresh
    by_name = {r.name: r for r in REGIONS}
    jobs = [(name, src, z, i) for name, src, z in ALTERNATIVES if name in by_name for i in range(per_alt)]
    missing = [name for name, _, _ in ALTERNATIVES if name not in by_name]
    if missing:
        click.echo(f"regions not in regions.py, skipped: {sorted(set(missing))}")

    def run(job):
        name, src, z, i = job
        im, meta = fetch_one(src, z, by_name[name], random.Random(hash(job) & 0xFFFF))
        if im is None:
            return None
        reg = by_name[name]
        return im, dict(set="alternative", source=src, zoom=z, region=name, regime=reg.regime, bucket="fresh", **meta)

    with ThreadPoolExecutor(8) as ex:
        got = [t for t in ex.map(run, jobs) if t]
    click.echo(f"alternatives: {len(got)} of {len(jobs)} fetched")
    tiles += got

    # sheets
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheets").mkdir(exist_ok=True)
    rng.shuffle(tiles)
    rows = []
    for si in range(0, len(tiles), PER_SHEET):
        chunk = tiles[si:si + PER_SHEET]
        sheet = Image.new("RGB", (COLS * CELL, math.ceil(len(chunk) / COLS) * CELL), (8, 12, 18))
        for i, (im, meta) in enumerate(chunk):
            if im.size != (CELL, CELL):
                im = im.resize((CELL, CELL), Image.LANCZOS)
            sheet.paste(im, ((i % COLS) * CELL, (i // COLS) * CELL))
            m = float(meta.get("lat", 0))
            rows.append(dict(id=f"t{si + i:04d}", sheet=si // PER_SHEET, ix=i % COLS, iy=i // COLS,
                             km=round(mpp(meta["zoom"], m) * CELL / 1000), **meta))
        sheet.save(out / "sheets" / f"sheet_{si // PER_SHEET:02d}.jpg", quality=80)
    (out / "tiles.json").write_text(json.dumps(rows, separators=(",", ":")))
    click.echo(f"{len(rows)} tiles in {math.ceil(len(rows) / PER_SHEET)} sheets -> {out}")


if __name__ == "__main__":
    main()
