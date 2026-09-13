"""Re-fetch gallery tiles at native sensor resolution: the 2x2 z9 children of each z8 tile.

z8 is 567 m/px; MODIS and VIIRS are 250-375 m/px. So every z8 tile is a 2x downsample,
and the same 145 km window is available as a 512 px image by stitching its four z9
children. This resolves each gallery row back to its GIBS tile address (the sampler
kept lat/date/sensor, the raw manifest keeps x/y) and fetches the quad.

    python scripts/upres_tiles.py --tiles tiles.json --ids ids.txt --out upres/

Writes <id>.jpg at 512 px. Landshapes rows are skipped (already 512). Run on the box.
"""
from __future__ import annotations

import csv
import io
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_scenes import lonlat_to_tile, tile_to_lonlat  # noqa: E402
from fetch_tiles import GIBS, LAYERS, HEADERS  # noqa: E402
from scale_ladder import EOX  # noqa: E402

SENSOR = {"modis": "terra", "viirs": "snpp"}  # gallery collapsed sensors; the manifest has the real one


def fetch(layer, day, z, x, y):
    url = EOX.format(z=z, y=y, x=x) if layer == "s2" else GIBS.format(layer=layer, day=day, z=z, y=y, x=x)
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code != 200 or len(r.content) < 500:
        return None
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def quad(layer, day, z, x, y):
    """The four z+1 children of (z, x, y), stitched to 512 px."""
    out = Image.new("RGB", (512, 512))
    for dy in (0, 1):
        for dx in (0, 1):
            im = fetch(layer, day, z + 1, 2 * x + dx, 2 * y + dy)
            if im is None:
                return None
            out.paste(im, (dx * 256, dy * 256))
    return out


def resolve(t, manifest_rows):
    """Gallery row -> (layer, day, z, x, y). Alternatives carry lon/lat; incumbents match the raw manifest."""
    if t["set"] == "alternative":
        x, y = lonlat_to_tile(t["lon"], t["lat"], t["z"])
        layer = "s2" if t["src"] == "s2" else LAYERS["terra"][0] if t["src"] == "modis" else LAYERS["snpp"][0]
        return layer, t["date"], t["z"], x, y
    cands = [r for r in manifest_rows if r["date"] == t["date"] and r["region"] == t["reg"] and int(r["z"]) == t["z"]]
    best, bd = None, 1e9
    for r in cands:
        _, lat = tile_to_lonlat(int(r["x"]) + 0.5, int(r["y"]) + 0.5, int(r["z"]))
        d = abs(lat - t["lat"])
        if d < bd:
            best, bd = r, d
    if best is None or bd > 0.01:
        return None
    return LAYERS[best["sensor"]][0], best["date"], int(best["z"]), int(best["x"]), int(best["y"])


@click.command()
@click.option("--tiles", required=True, type=click.Path(path_type=Path))
@click.option("--ids", default=None, type=click.Path(path_type=Path), help="one id per line; default all non-landshapes rows")
@click.option("--manifests", default="data/manifest.csv,data/dataset_land.manifest.csv")
@click.option("--out", required=True, type=click.Path(path_type=Path))
def main(tiles, ids, manifests, out):
    rows = json.load(open(tiles))
    for t in rows:  # accept both the bundle's field names and the page's slim ones
        t.setdefault("reg", t.get("region")); t.setdefault("src", t.get("source")); t.setdefault("z", t.get("zoom"))
    want = set(l.strip() for l in open(ids)) if ids else None
    rows = [t for t in rows if t.get("set") != "landshapes" and (want is None or t["id"] in want)]
    mrows = []
    for m in manifests.split(","):
        if Path(m).exists():
            mrows += list(csv.DictReader(open(m)))
    out.mkdir(parents=True, exist_ok=True)

    def run(t):
        if (out / f"{t['id']}.jpg").exists():
            return t["id"], "have"
        addr = resolve(t, mrows)
        if not addr:
            return t["id"], "unresolved"
        im = quad(*addr)
        if im is None:
            return t["id"], "missing children"
        im.save(out / f"{t['id']}.jpg", quality=90)
        return t["id"], "ok"

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run, rows))
    by = {}
    for _, s in res:
        by[s] = by.get(s, 0) + 1
    click.echo(f"{len(rows)} rows: {by}")
    bad = [i for i, s in res if s not in ("ok", "have")]
    if bad:
        click.echo("not upres'd: " + " ".join(bad[:20]) + (" ..." if len(bad) > 20 else ""))


if __name__ == "__main__":
    main()
