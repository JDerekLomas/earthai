"""Pull GOES ABI full-disk files from NOAA's permanent archive on AWS and cut a map out of them.

PRIOR ART: scripts/fetch_goes.py reads NASA GIBS, which is a rolling window (~28 days) of rendered
tiles and had already dropped 12 August by 15 September. This reads the Level-2 netCDF files that
NOAA keeps forever in the `noaa-goes19` Open Data bucket (anonymous HTTPS, no account), so any
instant since 2025 can be fetched, and the radiances come as numbers, not as somebody's palette.

    python scripts/fetch_goes_aws.py --start 2026-08-12T14:00 --end 2026-08-12T20:30 \
        --bbox -80,25,30,80 --out data/eclipse

Two products are understood. `ABI-L2-MCMIPF` is all sixteen bands at 2 km in one ~370 MB file per
ten minutes, which is what a true-colour picture needs (bands 1, 2, 3) plus the infrared control
(band 13). `ABI-L2-CMIPF` is one band per file at native resolution (band 2 at 0.5 km is 430 MB;
band 13 at 2 km is 24 MB) for when one band is enough:

    python scripts/fetch_goes_aws.py --product ABI-L2-CMIPF --band 13 --start ... --end ... --bbox ...

The ABI fixed grid is a `geos` projection seen from the satellite's own longitude. Each file
carries its own projection attributes and scan-angle axes, so the reprojection is exact per file:
the output grid (equirectangular by default, or any PROJ string via --proj) is transformed INTO
the satellite's view once, and every band is sampled bilinearly at those fractional row/column
positions. Off-disk pixels are black.

Writes per instant:
    <out>/truecolor/<UTC>.jpg        R=band 2, B=band 1, G synthesised (0.45 R + 0.10 band 3 + 0.45 B),
                                     gamma 1/2.2 -- the standard GOES "true colour" recipe, no Rayleigh fix
    <out>/truecolor_norm/<UTC>.jpg   the same, but each reflectance divided by cos(solar zenith) first, so
                                     the sun's height over the day no longer changes the brightness and
                                     anything that still darkens is NOT the time of day (capped at 5x;
                                     fades to black below 6 degrees of sun)
    <out>/ir/<UTC>.jpg               band 13 brightness temperature, 300 K black to 190 K white
    <out>/frames.jsonl               one row per instant with the mean normalised brightness in a few
                                     named boxes (see BOXES), so a dimming can be read as a number

The netCDF files are cached under --cache (default data/goes_aws/<product>/) and kept unless
--delete-cache is given; a second run with another --bbox or --proj reads them from disk.
"""
from __future__ import annotations

import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

UA = {"User-Agent": "earthai-goes-aws/0.1 (research; github.com/JDerekLomas/earthai)"}

# Boxes (lon0, lat0, lon1, lat1) whose mean normalised brightness is logged per frame. The first
# three lay under the 12 August 2026 umbra or deep penumbra; the last is a control far to the south.
BOXES = {
    "greenland": (-50, 64, -30, 76),
    "iceland": (-25, 63, -13, 67),
    "labrador_sea": (-60, 55, -45, 62),
    "iberia": (-10, 37, 3, 44),
    "control_sargasso": (-70, 26, -55, 32),
}


# ---------------------------------------------------------------- listing and fetching

def list_keys(bucket: str, product: str, t0: datetime, t1: datetime, band: int | None) -> list[tuple[datetime, str, int]]:
    """Every object whose scan START falls in [t0, t1], as (start, key, bytes). One S3 prefix per
    UTC hour; the key's `_sYYYYDDDHHMMSSt` field is the scan start."""
    keys = []
    hour = t0.replace(minute=0, second=0, microsecond=0)
    while hour <= t1:
        prefix = f"{product}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
        url = f"https://{bucket}.s3.amazonaws.com/?list-type=2&prefix={prefix}"
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        for key, size in re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", r.text, re.S):
            m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_e", key)
            if not m:
                continue
            if band is not None and not re.search(rf"-M\dC{band:02d}_", key):
                continue
            y, doy, hh, mm, ss = map(int, m.groups())
            start = datetime(y, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss)
            if t0 <= start <= t1:
                keys.append((start, key, int(size)))
        hour += timedelta(hours=1)
    return sorted(keys)


def fetch(bucket: str, key: str, size: int, cache: Path) -> Path:
    """Stream one object to the cache; a file of the right size is not fetched again."""
    dest = cache / Path(key).name
    if dest.exists() and dest.stat().st_size == size:
        return dest
    tmp = dest.with_suffix(".part")
    with requests.get(f"https://{bucket}.s3.amazonaws.com/{key}", headers=UA, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if tmp.stat().st_size != size:
        tmp.unlink()
        raise OSError(f"short download for {key}: {tmp.stat().st_size if tmp.exists() else 0} of {size}")
    tmp.rename(dest)
    return dest


# ---------------------------------------------------------------- the sun

def cos_solar_zenith(t: datetime, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """cos(solar zenith angle) on a lon/lat grid, Spencer's (1971) series -- a third of a degree,
    which is plenty for taking the daily cycle out of a picture."""
    doy = t.timetuple().tm_yday
    hours = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hours - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))          # minutes
    ha = np.radians(15 * (hours + lon / 15 + eot / 60 - 12))
    la = np.radians(lat)
    return np.sin(la) * math.sin(decl) + np.cos(la) * math.cos(decl) * np.cos(ha)


# ---------------------------------------------------------------- the grid

class Grid:
    """The output map: a regular grid in `proj` covering the lon/lat bbox at `res_km`, plus, per
    satellite file geometry, the fractional (row, col) in the ABI fixed grid of every output pixel."""

    def __init__(self, bbox: tuple[float, float, float, float], res_km: float, proj: str, limb_smooth_km: float = 0.0):
        from pyproj import CRS, Transformer
        self.crs = CRS.from_user_input(proj)
        self.res_km = res_km
        self.limb_smooth_km = limb_smooth_km
        lon0, lat0, lon1, lat1 = bbox
        fwd = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        # bound the projected box by the bbox's edges, not just its corners
        e_lon = np.concatenate([np.linspace(lon0, lon1, 200), np.linspace(lon0, lon1, 200), np.full(200, lon0), np.full(200, lon1)])
        e_lat = np.concatenate([np.full(200, lat0), np.full(200, lat1), np.linspace(lat0, lat1, 200), np.linspace(lat0, lat1, 200)])
        ex, ey = fwd.transform(e_lon, e_lat)
        ex, ey = ex[np.isfinite(ex)], ey[np.isfinite(ey)]
        step = res_km * 1000 if self.crs.is_projected else res_km / 111.32
        self.w = int(round((ex.max() - ex.min()) / step))
        self.h = int(round((ey.max() - ey.min()) / step))
        xs = ex.min() + (np.arange(self.w) + 0.5) * step
        ys = ey.max() - (np.arange(self.h) + 0.5) * step
        X, Y = np.meshgrid(xs, ys)
        inv = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        self.lon, self.lat = inv.transform(X, Y)
        self.lon = np.where(np.isfinite(self.lon), self.lon, 0.0)
        self.lat = np.where(np.isfinite(self.lat), self.lat, -90.0)
        self._sat = {}

    def box_mask(self, box) -> np.ndarray:
        lon0, lat0, lon1, lat1 = box
        return (self.lon >= lon0) & (self.lon <= lon1) & (self.lat >= lat0) & (self.lat <= lat1)

    def sat_coords(self, ds) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(rows, cols, valid) of every output pixel in this file's fixed grid; cached per geometry."""
        from pyproj import Transformer
        p = ds["goes_imager_projection"]
        h = float(p.perspective_point_height); lon0 = float(p.longitude_of_projection_origin)
        sweep = p.sweep_angle_axis if isinstance(p.sweep_angle_axis, str) else p.sweep_angle_axis.decode()
        xv, yv = ds["x"], ds["y"]
        sx, ox = float(xv.scale_factor), float(xv.add_offset)
        sy, oy = float(yv.scale_factor), float(yv.add_offset)
        key = (h, lon0, sweep, sx, ox, sy, oy, xv.shape[0], yv.shape[0])
        if key in self._sat:
            return self._sat[key]
        geos = (f"+proj=geos +h={h} +lon_0={lon0} +sweep={sweep} +a={float(p.semi_major_axis)} "
                f"+b={float(p.semi_minor_axis)} +units=m +no_defs")
        tr = Transformer.from_crs("EPSG:4326", geos, always_xy=True)
        gx, gy = tr.transform(self.lon, self.lat)
        valid = np.isfinite(gx) & np.isfinite(gy)
        # projected metres -> scan angle (radians) -> fractional index along the file's own axes
        cols = ((np.where(valid, gx, 0) / h) - ox) / sx
        rows = ((np.where(valid, gy, 0) / h) - oy) / sy
        valid &= (cols >= 0) & (cols <= xv.shape[0] - 1) & (rows >= 0) & (rows <= yv.shape[0] - 1)
        # The Earth's limb is a staircase of 2 km grid cells seen edge-on; in a map view that
        # staircase stretches into 100 km serrations. Fade the last degree and a half of central
        # angle before the limb (81.3 deg from the sub-satellite point) to black instead.
        cg = np.cos(np.radians(self.lat)) * np.cos(np.radians(self.lon - lon0))
        gamma = np.degrees(np.arccos(np.clip(cg, -1, 1)))
        limb = np.clip((81.0 - gamma) / 1.5, 0, 1).astype(np.float32)
        out = (rows, cols, valid, limb)
        self._sat[key] = out
        return out

    def sample(self, ds, var: str) -> np.ndarray:
        """One band, bilinear, onto the output grid as float32 physical units; NaN off-disk / fill."""
        from scipy.ndimage import map_coordinates
        rows, cols, valid, limb = self.sat_coords(ds)
        v = ds[var]
        v.set_auto_scale(False); v.set_auto_mask(False)
        raw = v[:].astype(np.float32)
        fill = float(v._FillValue) if hasattr(v, "_FillValue") else None
        if fill is not None:
            raw[raw == fill] = np.nan
        raw = raw * float(getattr(v, "scale_factor", 1.0)) + float(getattr(v, "add_offset", 0.0))
        out = map_coordinates(raw, [rows.ravel(), cols.ravel()], order=1, mode="constant", cval=np.nan).reshape(rows.shape)
        out[~valid] = np.nan
        if getattr(v, "units", "") != "K":                  # reflectances fade to black; temperatures stay numbers
            out = out * limb
            out[limb <= 0] = np.nan
            if self.limb_smooth_km > 0:
                # A map view stretches the limb's staircase into serrations; erode the valid area by
                # that much and blur the edge so the picture ends in a soft, smooth dark.
                from scipy.ndimage import binary_erosion, gaussian_filter
                px = max(1, int(self.limb_smooth_km / self.res_km))
                finite = np.isfinite(out)
                alpha = gaussian_filter(binary_erosion(finite, iterations=px, border_value=1).astype(np.float32), px / 2)
                out = np.nan_to_num(out, nan=0.0) * alpha
                out[alpha < 0.005] = np.nan
        return out


# ---------------------------------------------------------------- rendering

def to_u8(a: np.ndarray) -> np.ndarray:
    return (np.clip(np.nan_to_num(a, nan=0.0), 0, 1) * 255).round().astype(np.uint8)


def truecolor(r: np.ndarray, g_veg: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The usual GOES recipe: gamma each band, then a green that is mostly red+blue with a tenth of
    the vegetation band, because ABI has no true green channel."""
    gamma = lambda x: np.power(np.clip(np.nan_to_num(x, nan=0.0), 0, 1), 1 / 2.2)
    R, V, B = gamma(r), gamma(g_veg), gamma(b)
    G = 0.45 * R + 0.10 * V + 0.45 * B
    return np.dstack([to_u8(R), to_u8(G), to_u8(B)])


def normalise(refl: np.ndarray, mu: np.ndarray, cap: float = 5.0) -> np.ndarray:
    """Reflectance divided by cos(solar zenith), capped, and faded to black where the sun is under
    ~6 degrees so the terminator does not blow up into noise."""
    f = 1.0 / np.clip(mu, 1.0 / cap, 1.0)
    fade = np.clip(mu / 0.10, 0, 1)
    return refl * f * fade


def ir_grey(bt: np.ndarray, warm: float = 300.0, cold: float = 190.0) -> np.ndarray:
    return to_u8((warm - bt) / (warm - cold))


# ---------------------------------------------------------------- main

@click.command()
@click.option("--bucket", default="noaa-goes19", show_default=True, help="noaa-goes19 = GOES-East (since Apr 2025); noaa-goes18 = GOES-West")
@click.option("--product", default="ABI-L2-MCMIPF", show_default=True, type=click.Choice(["ABI-L2-MCMIPF", "ABI-L2-CMIPF"]))
@click.option("--band", default=None, type=int, help="CMIPF only: which band (1-16)")
@click.option("--start", required=True, help="UTC, e.g. 2026-08-12T14:00")
@click.option("--end", required=True, help="UTC, inclusive")
@click.option("--bbox", default="-80,25,30,80", show_default=True, help="lon0,lat0,lon1,lat1")
@click.option("--res-km", default=4.0, show_default=True, help="output pixel size")
@click.option("--proj", default="EPSG:4326", show_default=True, help="output CRS: EPSG code or PROJ string (e.g. '+proj=laea +lat_0=60 +lon_0=-35')")
@click.option("--out", default="data/eclipse", type=click.Path(path_type=Path), show_default=True)
@click.option("--cache", default=None, type=click.Path(path_type=Path), help="netCDF cache dir (default data/goes_aws/<product>)")
@click.option("--delete-cache", is_flag=True, help="remove each netCDF once its frames are written")
@click.option("--workers", default=4, show_default=True, help="parallel downloads")
@click.option("--no-fetch", is_flag=True, help="only render what is already in the cache")
@click.option("--limb-smooth-km", default=0.0, show_default=True, help="map views only: erode and blur the disk edge by this much ground distance to hide the stretched staircase")
def main(bucket, product, band, start, end, bbox, res_km, proj, out, cache, delete_cache, workers, no_fetch, limb_smooth_km):
    import netCDF4

    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    box = tuple(float(v) for v in bbox.split(","))
    cache = cache or Path("data/goes_aws") / product
    cache.mkdir(parents=True, exist_ok=True)
    if product == "ABI-L2-CMIPF" and band is None:
        raise click.UsageError("--band is required for ABI-L2-CMIPF")

    grid = Grid(box, res_km, proj, limb_smooth_km)
    click.echo(f"grid {grid.w}x{grid.h} px at {res_km} km, {proj}")

    if no_fetch:
        keys = []
        for f in sorted(cache.glob("*.nc")):
            m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_e", f.name)
            if not m or (band is not None and not re.search(rf"-M\dC{band:02d}_", f.name)):
                continue
            y, doy, hh, mm, ss = map(int, m.groups())
            ts = datetime(y, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1, hours=hh, minutes=mm, seconds=ss)
            if t0 <= ts <= t1:
                keys.append((ts, f.name, f.stat().st_size))
        keys.sort()
    else:
        keys = list_keys(bucket, product, t0, t1, band)
    click.echo(f"{len(keys)} files, {sum(s for _, _, s in keys) / 1e9:.1f} GB")
    if not keys:
        return

    sub = {"truecolor": out / "truecolor", "truecolor_norm": out / "truecolor_norm", "ir": out / "ir"}
    if product == "ABI-L2-CMIPF":
        sub = {"band": out / f"band{band:02d}"}
    for d in sub.values():
        d.mkdir(parents=True, exist_ok=True)
    log = open(out / "frames.jsonl", "a")
    masks = {k: grid.box_mask(b) for k, b in BOXES.items()}

    def get(item):
        ts, key, size = item
        if no_fetch:
            return ts, cache / key
        for attempt in range(3):
            try:
                return ts, fetch(bucket, key, size, cache)
            except Exception as e:                       # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {Path(key).name}: {e}")
        raise RuntimeError(f"could not fetch {key}")

    with ThreadPoolExecutor(workers) as pool:
        for ts, path in pool.map(get, keys):
            name = ts.strftime("%Y-%m-%dT%H%M%SZ")
            ds = netCDF4.Dataset(path)
            row = {"t": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "file": path.name}
            if product == "ABI-L2-MCMIPF":
                mu = cos_solar_zenith(ts, grid.lon, grid.lat)
                b1, b2, b3, b13 = (grid.sample(ds, f"CMI_C{n:02d}") for n in (1, 2, 3, 13))
                Image.fromarray(truecolor(b2, b3, b1)).save(sub["truecolor"] / f"{name}.jpg", quality=90)
                n1, n2, n3 = (normalise(b, mu) for b in (b1, b2, b3))
                Image.fromarray(truecolor(n2, n3, n1)).save(sub["truecolor_norm"] / f"{name}.jpg", quality=90)
                Image.fromarray(ir_grey(b13)).save(sub["ir"] / f"{name}.jpg", quality=90)
                for k, m in masks.items():
                    sel = m & np.isfinite(n2)
                    row[k] = {"norm_red": round(float(np.nanmean(n2[sel])), 4) if sel.any() else None,
                              "raw_red": round(float(np.nanmean(b2[sel])), 4) if sel.any() else None,
                              "cos_sza": round(float(mu[m].mean()), 4),
                              "bt13": round(float(np.nanmean(b13[sel])), 2) if sel.any() else None}
            else:
                a = grid.sample(ds, "CMI")
                if band <= 6:
                    mu = cos_solar_zenith(ts, grid.lon, grid.lat)
                    img = to_u8(np.power(np.clip(np.nan_to_num(a, nan=0.0), 0, 1), 1 / 2.2))
                    for k, m in masks.items():
                        sel = m & np.isfinite(a)
                        row[k] = {"raw": round(float(np.nanmean(a[sel])), 4) if sel.any() else None,
                                  "norm": round(float(np.nanmean(normalise(a, mu)[sel])), 4) if sel.any() else None,
                                  "cos_sza": round(float(mu[m].mean()), 4)}
                else:
                    img = ir_grey(a)
                    for k, m in masks.items():
                        sel = m & np.isfinite(a)
                        row[k] = {"bt": round(float(np.nanmean(a[sel])), 2) if sel.any() else None}
                Image.fromarray(img).save(sub["band"] / f"{name}.jpg", quality=90)
            ds.close()
            log.write(json.dumps(row) + "\n"); log.flush()
            if delete_cache and not no_fetch:
                path.unlink()
            click.echo(f"   {name}  " + "  ".join(f"{k}={v.get('norm_red', v.get('norm', v.get('bt')))}" for k, v in row.items() if isinstance(v, dict)))
    log.close()


if __name__ == "__main__":
    main()
