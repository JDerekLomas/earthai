"""Elevation for a GOES place, on exactly the same pixel grid as its frames.

PRIOR ART: scripts/fetch_tiles.py and fetch_goes.py fetch web-mercator imagery from GIBS;
this reuses their tiling (a z/x/y tile here is the same ground as a z/x/y tile there) and the
supersampling finding from scripts/supersample_check.py. Neither can do this job: the source
is a different service, and the payload is encoded HEIGHT, not a picture, so averaging and
rendering are different operations.

Source: AWS Open Data terrain tiles (s3://elevation-tiles-prod), free and unauthenticated,
web mercator z0-15, "terrarium" RGB encoding:  metres = R*256 + G + B/256 - 32768.
Includes bathymetry, which matters here: the California stratocumulus deck sits over the
continental shelf break, and the offshore z6 tile is 98% below sea level.

    python scripts/fetch_terrain.py --place california
    python scripts/fetch_terrain.py --place gulf --ss 4 --exaggerate 4

Fetches the children `ss` times finer than the frame's zoom and area-averages them down,
which is the same move that converged on the truth for Sentinel-2 imagery. Writes, all
aligned pixel-for-pixel with data/goes/<place>_x<span>/*.jpg:
  <place>_elev.npy        float32 metres, the real data
  <place>_hillshade.png   shaded relief for looking at, land and seafloor
  <place>_land.png        1 where elevation > 0
"""
from __future__ import annotations

import io
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_goes import PLACES  # noqa: E402

URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
UA = {"User-Agent": "earthai-terrain/0.1 (research; github.com/JDerekLomas/earthai)"}
EARTH_CIRC = 40075016.686


def decode(b: bytes) -> np.ndarray:
    a = np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).astype(np.float64)
    return (a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768).astype(np.float32)


def get(z, x, y, retries=3):
    for _ in range(retries):
        try:
            r = requests.get(URL.format(z=z, x=x, y=y), headers=UA, timeout=40)
            if r.status_code == 200:
                return decode(r.content)
            if r.status_code in (403, 404):
                return None
        except requests.RequestException:
            pass
    return None


def tile_lat(y: float, z: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def hillshade(elev: np.ndarray, m_per_px: np.ndarray, exaggerate: float, az=315.0, alt=45.0) -> np.ndarray:
    """Standard Horn-ish hillshade. m_per_px varies by row: mercator pixels shrink toward the pole."""
    dzdy, dzdx = np.gradient(elev * exaggerate)
    dzdx = dzdx / m_per_px[:, None]
    dzdy = dzdy / m_per_px[:, None]
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, dzdy)
    azr, altr = math.radians(360 - az + 90), math.radians(alt)
    shade = np.sin(altr) * np.cos(slope) + np.cos(altr) * np.sin(slope) * np.cos(azr - aspect)
    return np.clip(shade, 0, 1)


@click.command()
@click.option("--place", required=True, type=click.Choice(list(PLACES)))
@click.option("--span", default=3, type=int, help="must match the frames: 3 = the 768 px x3 frames")
@click.option("--ss", default=4, type=click.Choice(["1", "2", "4", "8"]), help="supersample factor")
@click.option("--exaggerate", default=3.0, type=float,
              help="vertical exaggeration for the hillshade only; at ~2 km/px real relief reads flat")
@click.option("--out", default="data/terrain", type=click.Path(path_type=Path))
def main(place, span, ss, exaggerate, out):
    ss = int(ss)
    _, z, cx, cy, _ = PLACES[place]
    x0, y0 = cx - span // 2, cy - span // 2          # same block fetch_goes.fetch() builds
    k = int(math.log2(ss))
    zz = z + k
    jobs = [(zz, (x0 + dx) * ss + sx, (y0 + dy) * ss + sy, dx, dy, sx, sy)
            for dy in range(span) for dx in range(span) for sy in range(ss) for sx in range(ss)]
    click.echo(f"{place}: {span}x{span} z{z} tiles at x{ss} supersample = {len(jobs)} requests at z{zz}")

    with ThreadPoolExecutor(16) as ex:
        tiles = list(ex.map(lambda j: get(j[0], j[1], j[2]), jobs))
    bad = sum(t is None for t in tiles)
    if bad:
        raise SystemExit(f"{bad} of {len(jobs)} terrain tiles missing; not writing a partial grid")

    side = 256 * ss
    fine = np.zeros((side * span, side * span), dtype=np.float32)
    for (_, _, _, dx, dy, sx, sy), t in zip(jobs, tiles):
        oy, ox = (dy * ss + sy) * 256, (dx * ss + sx) * 256
        fine[oy:oy + 256, ox:ox + 256] = t
    # area-average to the frame grid: each output pixel is the mean of an ss x ss block
    n = 256 * span
    elev = fine.reshape(n, ss, n, ss).mean(axis=(1, 3)) if ss > 1 else fine

    # metres per output pixel, per row, at the row's own latitude
    rows = np.arange(n) + 0.5
    lat = np.array([tile_lat(y0 + r / 256, z) for r in rows])
    m_per_px = EARTH_CIRC * np.cos(np.radians(lat)) / (2 ** z * 256)

    out.mkdir(parents=True, exist_ok=True)
    np.save(out / f"{place}_elev.npy", elev.astype(np.float32))
    hs = hillshade(elev, m_per_px, exaggerate)
    # tint: seafloor cool, land warm, so the coastline is legible at a glance
    land = elev > 0
    rgb = np.zeros((n, n, 3), dtype=np.float32)
    depth = np.clip(-elev / 5000, 0, 1)
    height = np.clip(elev / 4000, 0, 1)
    rgb[..., 0] = np.where(land, 0.55 + 0.35 * height, 0.10 + 0.10 * (1 - depth))
    rgb[..., 1] = np.where(land, 0.50 + 0.25 * height, 0.22 + 0.18 * (1 - depth))
    rgb[..., 2] = np.where(land, 0.40 + 0.15 * height, 0.35 + 0.30 * (1 - depth))
    rgb = rgb * (0.35 + 0.65 * hs[..., None])
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(out / f"{place}_hillshade.png")
    Image.fromarray((land * 255).astype(np.uint8)).save(out / f"{place}_land.png")
    meta = dict(place=place, z=z, x0=x0, y0=y0, span=span, px=n, supersample=ss,
                lat_top=round(float(lat[0]), 3), lat_bottom=round(float(lat[-1]), 3),
                m_per_px_mid=round(float(m_per_px[n // 2])), elev_min=round(float(elev.min())),
                elev_max=round(float(elev.max())), land_share=round(float(land.mean()), 3),
                encoding="terrarium, AWS Open Data elevation-tiles-prod")
    (out / f"{place}_terrain.json").write_text(json.dumps(meta, indent=1))
    click.echo(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
