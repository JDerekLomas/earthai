"""The HRRR grid, and how the frame maps onto it.

PRIOR ART: scripts/grid.py holds the web-mercator tile arithmetic for the frame (z, x, y, span)
and fetch_terrain.py puts elevation on that grid. HRRR is not on a tile grid: it is a 3 km
Lambert conformal conic grid (1799 x 1059 cells, GRIB template 30), so this module owns the
projection and the frame -> HRRR index mapping. Verified 2026-09-14 against the latitude/
longitude arrays cfgrib decodes from a real file: all four checked corners land on their own
grid index to 1e-12.

Every frame pixel gets fractional HRRR indices (I, J). Sampling a field on the frame is then
one map_coordinates call, and `inside` says which pixels HRRR covers at all -- the model's
western edge cuts through the California frame (it reaches -134.1 only at the grid's corner).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pyproj

LCC = pyproj.CRS.from_proj4("+proj=lcc +lat_1=38.5 +lat_2=38.5 +lat_0=38.5 +lon_0=262.5 +R=6371229 +units=m +no_defs")
FIRST = (237.280472, 21.138123)   # lon, lat of grid point (0, 0)
DX = 3000.0
NX, NY = 1799, 1059
_TR = pyproj.Transformer.from_crs("EPSG:4326", LCC, always_xy=True)
_X0, _Y0 = _TR.transform(*FIRST)


def frame_lonlat(place: str, px: int | None = None):
    """lon/lat at every pixel centre of the place's frame (web mercator z/x0/y0/span)."""
    meta = json.loads((Path("data/terrain") / f"{place}_terrain.json").read_text())
    z, x0, y0, span = meta["z"], meta["x0"], meta["y0"], meta["span"]
    px = px or meta["px"]
    n = 2 ** z
    cols, rows = np.meshgrid(np.arange(px), np.arange(px))
    xt = x0 + (cols + 0.5) / px * span
    yt = y0 + (rows + 0.5) / px * span
    lon = xt / n * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * yt / n))))
    return lon, lat, meta


def frame_to_hrrr(place: str, px: int | None = None):
    """Fractional HRRR (I=column, J=row) for every frame pixel, plus the coverage mask."""
    lon, lat, meta = frame_lonlat(place, px)
    x, y = _TR.transform(lon, lat)
    I = (x - _X0) / DX
    J = (y - _Y0) / DX
    inside = (I >= 0) & (I <= NX - 1) & (J >= 0) & (J <= NY - 1)
    return I.astype(np.float32), J.astype(np.float32), inside, meta


def crop_window(place: str, pad: int = 2) -> tuple[int, int, int, int]:
    """(i0, i1, j0, j1): the HRRR index window that covers the frame, clipped to the grid."""
    I, J, inside, _ = frame_to_hrrr(place)
    i0 = max(0, int(math.floor(I[inside].min())) - pad)
    i1 = min(NX, int(math.ceil(I[inside].max())) + pad + 1)
    j0 = max(0, int(math.floor(J[inside].min())) - pad)
    j1 = min(NY, int(math.ceil(J[inside].max())) + pad + 1)
    return i0, i1, j0, j1


if __name__ == "__main__":
    I, J, inside, meta = frame_to_hrrr("california")
    print("coverage", round(float(inside.mean()), 3), "window", crop_window("california"))
