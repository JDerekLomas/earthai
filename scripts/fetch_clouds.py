"""A cloud LAYER for the globe: one number per pixel, 0 (clear) to 1 (opaque), every ten minutes.

PRIOR ART: scripts/fetch_earth.py stitches five satellites' finished PICTURES (GeoColor, painted
infrared), so every source brings its own lighting and the zones meet at hard edges.
scripts/fetch_goes_aws.py reads the calibrated numbers from NOAA's archive but downloads all
sixteen bands (~370 MB a slot) and draws pictures of a bounding box. This reads only the two
bands a cloud mask needs, by HTTP byte range, and turns them into opacity: no lighting in it at
all, so the page can light it, and nothing to mismatch where two satellites meet.

    python scripts/fetch_clouds.py fetch     --sat goes19 --start 2026-09-12T00:00 --end 2026-09-12T23:50
    python scripts/fetch_clouds.py calibrate --sat mtg        # WMS satellites only, after their reference is fetched
    python scripts/fetch_clouds.py clearsky  --sat goes19
    python scripts/fetch_clouds.py opacity   --sat goes19
    python scripts/fetch_clouds.py blend     --start 2026-09-12T00:00 --end 2026-09-12T23:50 --sats goes19,goes18,himawari9,mtg,iodc
    python scripts/fetch_clouds.py height    --start 2026-09-12T00:00 --end 2026-09-12T23:50 --sats goes19,goes18,himawari9,mtg,iodc
    python scripts/fetch_clouds.py encode    --out site/globe --name clouds

Run from the repo root (paths are relative to data/). The grid is equirectangular, 4096 wide,
cropped to +/-66.09 deg latitude (1504 rows, a multiple of 16): a geostationary satellite sees
nothing useful poleward of that, so no pixel is spent there.

fetch     Five satellites, three kinds of source, one output: <UTC>_vis.png (reflectance x 40000)
          and <UTC>_bt.png (kelvin x 100) as 16-bit PNGs on the grid under data/clouds/raw/<sat>/,
          0 = no data.
          abi (goes19, goes18): the HDF5 chunk index of `CMI_C02` (0.64 um reflectance factor) and
          `CMI_C13` (10.3 um brightness temperature) is read by byte range (a few 256 KB blocks),
          then each variable's chunks, which sit contiguously in the file, come down in ONE ranged
          GET and are inflated and unshuffled here. ~40-55 MB a slot instead of ~370.
          ahi (himawari9): the raw HSD segments, bands 3 (0.64 um, 0.5 km, box-averaged 4x4) and 13,
          bz2, calibrated from the header blocks (~150 MB a slot, mostly band 3).
          wms (mtg, iodc): EUMETView's 0.6 um and 10.5/10.8 um layers as served, an 8-bit grey each,
          stored x100; `calibrate` turns grey into reflectance and kelvin by quantile matching
          against a neighbour in the overlap. Meteosat-9 is every 15 min; blend holds it.
          Every native grid is box-averaged 2x2, blurred a little (the output pixel is ~10 km;
          without this, cloud streets alias) and sampled bilinearly.
clearsky  What each pixel looks like with NO cloud, from the frames themselves.
          vis: the lowest sun-normalised reflectance seen in any daytime slot (mu > 0.35).
          bt:  per time of day, the warmest temperature seen within +/-1 h of that time on any
               day, but never colder than (warmest of all - DIURNAL_K), so cloud that sits still
               for hours is not mistaken for the ground.
opacity   vis: 1-exp(-(refl/mu - clear - margin)/k); bt: 1-exp(-(clear - bt - margin)/k); the visible
          term fades out through twilight (mu 0.30 -> 0.10) and inside the sun's glint on water,
          so the field is continuous across the terminator: night and glint are infrared-only.
          Weighted to zero from 58 to 66 degrees of satellite zenith angle.
blend     Weighted mean of every satellite's opacity on the common grid (weights as above).
height    Cloud-top height per blended frame from the band-13 brightness temperature against the same
          clear-sky reference: (T_clear - BT) / 6.5 K/km, 0..16 km, 0 where opacity < 0.1, blended with
          blend's weights over the satellites that see cloud there. data/clouds/height/<slot>.png, km x 16.
encode    Grey H.264 clips for the page (opacity in luma, optical flow to the next frame in the
          otherwise-empty chroma planes, the height field at half resolution in extra rows under the
          opacity when `height` has run, a calibration strip along the bottom), a poster, a manifest.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_goes_aws import UA, cos_solar_zenith, list_keys  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

W, H = 4096, 1504
LAT_MAX = H / W * 180.0                       # 66.09
ROOT = Path(os.environ.get("CLOUDS_ROOT", "data/clouds"))   # a second tree (e.g. data/clouds/official) for a side-by-side run
VIS_SCALE, BT_SCALE = 40000.0, 100.0
ZEN_FULL, ZEN_ZERO = 58.0, 66.0               # satellite zenith angle: full weight .. none
DIURNAL_K = 5.0                               # clear-sky bt over water is never below (warmest of all - this)
DIURNAL_LAND_K = 32.0                         # over land (deserts cool 20-30 K overnight)
R_EARTH, R_GEO = 6378.137, 42164.16

# `official` is the agency's own calibrated product for the same two bands (scripts/official_sources.py):
# `fetch --source official` writes physical values into raw/<sat>/ and a source.json marker, and the
# WMS grey path (`calibrate`, the LUT) is then bypassed for that satellite.
SATS = {
    "goes19": {"bucket": "noaa-goes19", "lon": -75.0, "kind": "abi", "name": "GOES-East"},
    "goes18": {"bucket": "noaa-goes18", "lon": -137.0, "kind": "abi", "name": "GOES-West"},
    "himawari9": {"bucket": "noaa-himawari9", "lon": 140.7, "kind": "ahi", "name": "Himawari-9", "official": "ptree"},
    # EUMETView serves contrast-stretched 8-bit greys, not numbers: `calibrate` maps them onto a
    # neighbour's physical values by quantile matching in the overlap (mtg <- goes19, iodc <- mtg)
    "mtg": {"lon": 0.0, "kind": "wms", "name": "Meteosat MTG-I1", "step": 10, "ref": "goes19", "official": "fci",
            "vis": ("mtg_fd:vis06_hrfi", ""), "ir": ("mtg_fd:ir105_hrfi", "mtg_fd:mtg_fd_ir105_hrfi_grayscale")},
    "iodc": {"lon": 45.5, "kind": "wms", "name": "Meteosat-9", "step": 15, "ref": "mtg", "official": "seviri",
             "vis": ("msg_iodc:vis006", ""), "ir": ("msg_iodc:ir108", "")},
}
WMS = ("https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
       "&layers={layer}&styles={style}&crs=EPSG:4326&bbox={s},{w},{n},{e}&width={W}&height={H}"
       "&format=image/png&transparent=true&time={t}")
GREY_SCALE = 100.0                            # WMS greys are stored x100 in the same 16-bit PNGs; 0 = no data


# ---------------------------------------------------------------- the grid

def grid_lonlat() -> tuple[np.ndarray, np.ndarray]:
    lon = -180 + (np.arange(W) + 0.5) * 360.0 / W
    lat = LAT_MAX - (np.arange(H) + 0.5) * 360.0 / W
    return np.meshgrid(lon.astype(np.float32), lat.astype(np.float32))


def sat_zenith(lon: np.ndarray, lat: np.ndarray, sub_lon: float) -> np.ndarray:
    """Satellite zenith angle in degrees at each ground point (90+ = over the horizon)."""
    cg = np.cos(np.radians(lat)) * np.cos(np.radians(lon - sub_lon))
    d = np.sqrt(R_GEO ** 2 + R_EARTH ** 2 - 2 * R_GEO * R_EARTH * cg)
    cosz = (R_GEO * cg - R_EARTH) / d
    return np.degrees(np.arccos(np.clip(cosz, -1, 1)))


def view_weight(sub_lon: float) -> np.ndarray:
    lon, lat = grid_lonlat()
    z = sat_zenith(lon, lat, sub_lon)
    t = np.clip((ZEN_ZERO - z) / (ZEN_ZERO - ZEN_FULL), 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def sun_vector(t: datetime) -> tuple[float, float, float]:
    """Unit vector to the sun in Earth-fixed coordinates (x through 0N 0E, z through the pole).
    Same Spencer series as cos_solar_zenith, so the page and the pipeline agree."""
    doy = t.timetuple().tm_yday
    hours = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hours - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    sub_lon = math.radians(-15 * (hours + eot / 60 - 12))
    return (math.cos(decl) * math.cos(sub_lon), math.cos(decl) * math.sin(sub_lon), math.sin(decl))


def glint_cos(t: datetime, lon: np.ndarray, lat: np.ndarray, sub_lon: float) -> np.ndarray:
    """cos of the angle between the sun's mirror reflection off a flat sea and the direction to
    the satellite: 1 at the centre of the glint."""
    la, lo = np.radians(lat), np.radians(lon)
    n = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]).astype(np.float32)
    s = np.array(sun_vector(t), dtype=np.float32)[:, None, None]
    sl = math.radians(sub_lon)
    v = np.array([R_GEO * math.cos(sl), R_GEO * math.sin(sl), 0.0], dtype=np.float32)[:, None, None] - R_EARTH * n
    v /= np.linalg.norm(v, axis=0, keepdims=True)
    r = 2 * (n * s).sum(0, keepdims=True) * n - s
    return (r * v).sum(0)


# ---------------------------------------------------------------- ABI by byte range

class DiskGeometry:
    """Fractional (row, col) in a source grid of every output pixel, and `sample()` to pull a source
    array onto the output grid: box-average `pool`x`pool` (so the source pixel is near the output's
    ~10 km before it is blurred and sampled bilinearly), a light blur, bilinear, masked to where the
    satellite sees the ground (zenith under ZEN_ZERO+1).
    Constructors: `abi`/`ahi`/`fci` for geostationary fixed grids (`colrow(ax, ay)` turns scan angles
    in radians, x east, y north, into fractional 0-based native (col, row)); `latlon` for a regular
    lat/lon grid (JAXA's gridded netCDF); `area` for a satpy AreaDefinition (SEVIRI native)."""

    def __init__(self, sub_lon: float, rows: np.ndarray, cols: np.ndarray, ok: np.ndarray, pool: int = 2, blur: float = 1.0):
        lon, lat = grid_lonlat()
        self.ok = ok & (sat_zenith(lon, lat, sub_lon) < ZEN_ZERO + 1)
        self.pool, self.blur = pool, blur
        self.rows = ((rows + 0.5) / pool - 0.5).astype(np.float32) if pool > 1 else rows.astype(np.float32)
        self.cols = ((cols + 0.5) / pool - 0.5).astype(np.float32) if pool > 1 else cols.astype(np.float32)

    @classmethod
    def geos(cls, sub_lon: float, sweep: str, colrow, pool: int = 2, h: float = 35786023.0, a: float = 6378137.0, b: float = 6356752.31414):
        from pyproj import Transformer
        lon, lat = grid_lonlat()
        geos = f"+proj=geos +h={h} +lon_0={sub_lon} +sweep={sweep} +a={a} +b={b} +units=m +no_defs"
        gx, gy = Transformer.from_crs("EPSG:4326", geos, always_xy=True).transform(lon.astype(np.float64), lat.astype(np.float64))
        ok = np.isfinite(gx) & np.isfinite(gy)
        cols, rows = colrow(np.where(ok, gx, 0) / h, np.where(ok, gy, 0) / h)
        return cls(sub_lon, rows, cols, ok, pool)

    @classmethod
    def abi(cls, sub_lon: float, scale: float = 5.6e-05, off: float = 0.151844):
        return cls.geos(sub_lon, "x", lambda ax, ay: ((ax + off) / scale, (off - ay) / scale))

    @classmethod
    def ahi(cls, sub_lon: float, cfac: float = 20466275, coff: float = 2750.5):
        f = cfac / 65536 * 180 / math.pi                       # HSD: pixel = COFF + angle_deg * CFAC / 2^16, 1-based
        return cls.geos(sub_lon, "y", lambda ax, ay: (coff + ax * f - 1, coff - ay * f - 1))

    @classmethod
    def fci(cls, sub_lon: float, xscale: float, xoff: float, yscale: float, yoff: float, pool: int):
        """MTG FCI L1c reference grid: packed x/y are column/row numbers, angle = offset + scale * index. The
        array assembled from the chunk files has row index = FCI row - 1 and row 1 is the SOUTH edge (south-up).
        The file's x runs the OTHER way from pyproj's geos x (its positive azimuth is west; taken literally the
        disc came out mirrored, Atlantic east of Africa), so the azimuth is negated, and satpy's fci_l1c_nc area
        (verified on coastlines) puts the edge at index -0.5, half a pixel from the packing's literal reading."""
        return cls.geos(sub_lon, "y", lambda ax, ay: ((-ax - xoff) / xscale - 0.5, (ay - yoff) / yscale - 0.5), pool,
                        h=35786400.0, a=6378137.0, b=6356752.31424518)

    @classmethod
    def latlon(cls, sub_lon: float, lat0: float, lon0: float, step: float, nlat: int, nlon: int, pool: int = 1, blur: float = 1.0):
        """A regular grid with its first row at lat0 (north edge pixel centre) and first column at lon0, `step`
        degrees; lon0 may exceed 180 (JAXA: 70..210 E), longitudes are unwrapped to match."""
        lon, lat = grid_lonlat()
        lonu = np.where(lon < lon0 - 1e-6, lon + 360, lon)
        rows = (lat0 - lat) / step
        cols = (lonu - lon0) / step
        ok = (rows >= -0.5) & (rows <= nlat - 0.5) & (cols >= -0.5) & (cols <= nlon - 0.5)
        return cls(sub_lon, rows, cols, ok, pool, blur)

    @classmethod
    def area(cls, sub_lon: float, area, pool: int = 1, blur: float = 0.8):
        """A satpy/pyresample AreaDefinition: (col, row) from its CRS and extent, in either orientation
        (SEVIRI native's extent runs south-east up; the formulas below handle a negative pixel size)."""
        from pyproj import Transformer
        lon, lat = grid_lonlat()
        gx, gy = Transformer.from_crs("EPSG:4326", area.crs, always_xy=True).transform(lon.astype(np.float64), lat.astype(np.float64))
        ok = np.isfinite(gx) & np.isfinite(gy)
        llx, lly, urx, ury = area.area_extent
        nrows, ncols = area.shape
        cols = (np.where(ok, gx, 0) - llx) / ((urx - llx) / ncols) - 0.5
        rows = (ury - np.where(ok, gy, 0)) / ((ury - lly) / nrows) - 0.5
        return cls(sub_lon, rows, cols, ok, pool, blur)

    def sample(self, raw: np.ndarray) -> np.ndarray:
        """raw: 2-D float32 with NaN for fill. Returns the output grid, NaN where unseen."""
        from scipy.ndimage import gaussian_filter, map_coordinates
        p = self.pool
        if p > 1:
            m0, m1 = raw.shape[0] // p, raw.shape[1] // p
            a = raw[:m0 * p, :m1 * p].reshape(m0, p, m1, p)
            good = np.isfinite(a)
            cnt = good.sum((1, 3))
            pooled = np.where(good, a, 0).sum((1, 3)) / np.maximum(cnt, 1)
            seen = cnt > 0
        else:
            seen = np.isfinite(raw)
            pooled = raw
        wgt = gaussian_filter(seen.astype(np.float32), self.blur)
        pooled = gaussian_filter(np.where(seen, pooled, 0).astype(np.float32), self.blur) / np.maximum(wgt, 1e-3)
        pooled[wgt < 0.5] = np.nan
        out = map_coordinates(pooled, [self.rows.ravel(), self.cols.ravel()], order=1, mode="constant", cval=np.nan).reshape(H, W)
        out[~self.ok] = np.nan
        return out


class RangeFile:
    """A read-only file over HTTP byte ranges for h5py, one plain `requests` session per instance
    (fsspec's http filesystem shares one event loop across threads and deadlocked at 8 workers).
    Reads are rounded out to BLOCK-sized pieces and kept, so the HDF5 superblock, object headers
    and chunk B-trees cost a handful of requests."""
    BLOCK = 1 << 18

    def __init__(self, url: str):
        self.url, self.pos, self.blocks, self.fetched = url, 0, {}, 0
        self.s = requests.Session()
        r = self.s.head(url, headers=UA, timeout=60)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])

    def _block(self, i: int) -> bytes:
        if i not in self.blocks:
            lo = i * self.BLOCK
            hi = min(lo + self.BLOCK, self.size) - 1
            r = self.s.get(self.url, headers={**UA, "Range": f"bytes={lo}-{hi}"}, timeout=120)
            r.raise_for_status()
            self.blocks[i] = r.content
            self.fetched += len(r.content)
        return self.blocks[i]

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self.size - self.pos
        out, pos, end = [], self.pos, min(self.pos + n, self.size)
        while pos < end:
            b = self._block(pos // self.BLOCK)
            off = pos % self.BLOCK
            take = min(len(b) - off, end - pos)
            out.append(b[off:off + take])
            pos += take
        self.pos = pos
        return b"".join(out)

    def seek(self, off: int, whence: int = 0) -> int:
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def tell(self) -> int:
        return self.pos

    def readable(self): return True
    def seekable(self): return True
    def writable(self): return False
    def flush(self): pass
    def close(self): self.s.close()


def abi_read(url: str, names: tuple[str, ...]) -> tuple[dict[str, np.ndarray], int]:
    """The named int16 variables of one MCMIPF file as float32 physical values, and bytes fetched."""
    import h5py
    out = {}
    f = RangeFile(url)
    h = h5py.File(f, "r")
    plan = {}
    for v in names:
        d = h[v]
        info = [d.id.get_chunk_info(i) for i in range(d.id.get_num_chunks())]
        plan[v] = (d.shape, d.chunks, float(d.attrs["scale_factor"][0]), float(d.attrs["add_offset"][0]),
                   int(d.attrs["_FillValue"][0]), [(c.byte_offset, c.size, c.chunk_offset[0]) for c in info])
    h.close()
    fetched = f.fetched
    f.close()
    for v, (shape, chunks, sf, ao, fill, info) in plan.items():
        lo = min(o for o, _, _ in info)
        hi = max(o + s for o, s, _ in info)
        r = requests.get(url, headers={**UA, "Range": f"bytes={lo}-{hi - 1}"}, timeout=600)
        r.raise_for_status()
        buf = r.content
        if len(buf) != hi - lo:
            raise OSError(f"short range for {v}: {len(buf)} of {hi - lo}")
        fetched += len(buf)
        arr = np.empty(shape, np.int16)
        for o, s, r0 in info:
            b = np.frombuffer(zlib.decompress(buf[o - lo:o - lo + s]), np.uint8)
            b = b.reshape(2, -1).T.copy().view("<i2").reshape(chunks)      # undo the HDF5 shuffle filter
            n = min(chunks[0], shape[0] - r0)
            arr[r0:r0 + n] = b[:n]
        a = arr.astype(np.float32) * sf + ao
        a[arr == fill] = np.nan
        out[v] = a
    return out, fetched


# ---------------------------------------------------------------- AHI (Himawari) HSD segments

def hsd_segment(raw: bytes) -> tuple[int, np.ndarray, dict]:
    """One decompressed HSD segment -> (first line, calibrated float32 block with NaN, header)."""
    import struct
    pos, blocks, hdr_len = 0, {}, None
    while True:
        num, length = raw[pos], struct.unpack_from("<H", raw, pos + 1)[0]
        blocks[num] = raw[pos:pos + length]
        pos += length
        if num == 1:
            hdr_len = struct.unpack_from("<I", blocks[1], 70)[0]
        if pos >= hdr_len:
            break
    _, cols, lines = struct.unpack_from("<HHH", blocks[2], 3)
    sub_lon, cfac, lfac, coff, loff = struct.unpack_from("<dIIff", blocks[3], 3)
    band, wl, _, err, outside, gain, const = struct.unpack_from("<HdHHHdd", blocks[5], 3)
    _, _, first_line = struct.unpack_from("<BBH", blocks[7], 3)
    data = np.frombuffer(raw, "<u2", count=cols * lines, offset=pos).reshape(lines, cols)
    good = (data != err) & (data != outside)
    rad = gain * data.astype(np.float32) + const
    if band <= 6:
        val = rad * struct.unpack_from("<d", blocks[5], 35)[0]              # radiance -> reflectance factor
    else:
        c0, c1, c2 = struct.unpack_from("<3d", blocks[5], 35)
        c, h, k = struct.unpack_from("<3d", blocks[5], 83)
        lam = wl * 1e-6
        r = rad.astype(np.float64) * 1e6
        te = (h * c / (k * lam)) / np.log(1 + 2 * h * c ** 2 / (lam ** 5 * np.maximum(r, 1e-9)))
        val = (c0 + c1 * te + c2 * te ** 2).astype(np.float32)
    val[~good] = np.nan
    return first_line - 1, val, {"sub_lon": sub_lon, "cfac": cfac, "coff": coff, "cols": cols}


def ahi_read(bucket: str, day: datetime, band: int, pool: int) -> np.ndarray | None:
    """All ten segments of one band at one slot, assembled and box-averaged `pool`x so both bands land
    on the 5500 grid (B03 is 0.5 km = 22000 px; B13 is 2 km = 5500)."""
    import bz2
    prefix = f"AHI-L1b-FLDK/{day:%Y/%m/%d/%H%M}/"
    r = requests.get(f"https://{bucket}.s3.amazonaws.com/?list-type=2&prefix={prefix}", headers=UA, timeout=60)
    r.raise_for_status()
    import re
    keys = [k for k in re.findall(r"<Key>([^<]+)</Key>", r.text) if f"_B{band:02d}_" in k]
    if len(keys) != 10:
        return None
    full = np.full((5500, 5500), np.nan, np.float32)

    def one(key):
        rr = requests.get(f"https://{bucket}.s3.amazonaws.com/{key}", headers=UA, timeout=300)
        rr.raise_for_status()
        return hsd_segment(bz2.decompress(rr.content))

    with ThreadPoolExecutor(3) as pool_:
        for r0, val, _ in pool_.map(one, keys):
            if pool > 1:
                m = val.shape[0] // pool
                a = val.reshape(m, pool, val.shape[1] // pool, pool)
                good = np.isfinite(a)
                cnt = good.sum((1, 3))
                val = np.where(cnt > 0, np.where(good, a, 0).sum((1, 3)) / np.maximum(cnt, 1), np.nan).astype(np.float32)
                r0 //= pool
            full[r0:r0 + val.shape[0]] = val
    return full


# ---------------------------------------------------------------- EUMETView WMS greys

def wms_columns(sub_lon: float) -> tuple[int, int]:
    """Grid columns spanned by a disk (+/- 81 deg of longitude), wrapped later if needed."""
    px = W / 360
    c0 = int(math.floor((sub_lon - 81 + 180) * px))
    c1 = int(math.ceil((sub_lon + 81 + 180) * px))
    return c0, c1


def wms_grey(sat: str, band: str, t: datetime) -> np.ndarray | None:
    """One band as served, on the full cloud grid: float32 grey 0..255, NaN where transparent/absent."""
    layer, style = SATS[sat][band]
    c0, c1 = wms_columns(SATS[sat]["lon"])
    w, e = -180 + c0 * 360 / W, -180 + c1 * 360 / W
    url = WMS.format(layer=layer, style=style, s=-LAT_MAX, w=w, n=LAT_MAX, e=e, W=c1 - c0, H=H, t=t.strftime("%Y-%m-%dT%H:%M:00.000Z"))
    import io
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=240)
            if r.status_code == 200 and r.content[:2] == b"\x89P":
                break
        except requests.RequestException:
            pass
        time.sleep(5)
    else:
        return None
    im = Image.open(io.BytesIO(r.content)).convert("RGBA")
    a = np.asarray(im).astype(np.float32)
    g = a[..., :3].mean(-1)
    g[a[..., 3] < 128] = np.nan
    if np.isfinite(g).mean() < 0.2 or (band == "ir" and np.nanstd(g) < 2):   # a blank slot (a night vis is flat and fine)
        return None
    out = np.full((H, W), np.nan, np.float32)
    out[:, c0:c1] = g
    return out


def lut_apply(lut: dict, band: str, grey: np.ndarray) -> np.ndarray:
    return np.interp(grey, lut[band]["grey"], lut[band]["value"]).astype(np.float32)


_PHYSICAL: dict[str, bool] = {}


def physical(sat: str) -> bool:
    """True when raw/<sat>/ holds calibrated reflectance and kelvin (ABI, AHI, or an official source
    fetched by `--source official`, which leaves raw/<sat>/source.json); False for WMS greys + LUT."""
    if sat not in _PHYSICAL:
        _PHYSICAL[sat] = SATS[sat]["kind"] != "wms" or (ROOT / "raw" / sat / "source.json").exists()
    return _PHYSICAL[sat]


def lut_for(sat: str) -> dict | None:
    return None if physical(sat) else json.loads((ROOT / f"lut_{sat}.json").read_text())


def load_band(sat: str, t: datetime, band: str, lut: dict | None = None) -> np.ndarray | None:
    """Physical values (reflectance or K) for any satellite, NaN = no data."""
    p = ROOT / "raw" / sat / f"{slot_name(t)}_{band}.png"
    if not p.exists():
        return None
    if physical(sat):
        return load16(p, VIS_SCALE if band == "vis" else BT_SCALE)
    g = load16(p, GREY_SCALE)
    if lut is None:
        lut = json.loads((ROOT / f"lut_{sat}.json").read_text())
    v = lut_apply(lut, band, np.nan_to_num(g, nan=0.0))
    v[~np.isfinite(g)] = np.nan
    return v


def save16(path: Path, a: np.ndarray, scale: float) -> None:
    q = np.clip(np.nan_to_num(a, nan=0.0) * scale, 1, 65535).round().astype(np.uint16)
    q[~np.isfinite(a)] = 0
    tmp = path.with_suffix(".tmp.png")
    Image.fromarray(q).save(tmp, compress_level=3)
    tmp.rename(path)


def load16(path: Path, scale: float) -> np.ndarray:
    q = np.asarray(Image.open(path)).astype(np.float32)
    a = q / scale
    a[q == 0] = np.nan
    return a


def slot_name(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H%MZ")


def parse_slot(name: str) -> datetime:
    return datetime.strptime(name[:16], "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc)


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- commands

@click.group()
def cli():
    pass


def slots_between(start: str, end: str, step: int = 10) -> list[datetime]:
    t, t1, out = utc(start), utc(end), []
    while t <= t1:
        out.append(t)
        t += timedelta(minutes=step)
    return out


def have(out: Path, slot: datetime) -> bool:
    return (out / f"{slot_name(slot)}_vis.png").exists() and (out / f"{slot_name(slot)}_bt.png").exists()


@cli.command()
@click.option("--sat", required=True, type=click.Choice(sorted(SATS)))
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--workers", default=6, show_default=True)
@click.option("--source", default="legacy", type=click.Choice(["legacy", "official"]), show_default=True,
              help="official = the agency's calibrated product (JAXA P-Tree for himawari9, EUMETSAT Data Store for mtg and iodc; "
                   "needs .secrets.json or $EARTHAI_SECRETS); legacy = HSD segments / EUMETView WMS. GOES is always NOAA's own.")
@click.option("--range-reads", is_flag=True, help="official mtg: read only the two channels of each FCI chunk by HTTP byte range "
              "(~210 MB a slot in ~440 requests) instead of whole chunk files (~815 MB a slot); slower, lighter")
def fetch(sat, start, end, workers, source, range_reads):
    cfg = SATS[sat]
    out = ROOT / "raw" / sat
    out.mkdir(parents=True, exist_ok=True)
    log = open(ROOT / "fetch.jsonl", "a")
    if source == "official" and cfg.get("official"):
        return fetch_official(sat, cfg, out, start, end, workers, log, range_reads)
    if cfg["kind"] == "wms" and physical(sat):
        raise click.ClickException(f"raw/{sat} holds official (physical) frames; a legacy WMS fetch would mix greys into it")
    if cfg["kind"] == "ahi":
        return fetch_ahi(sat, cfg, out, start, end, workers, log)
    if cfg["kind"] == "wms":
        return fetch_wms(sat, cfg, out, start, end, workers, log)
    keys = list_keys(cfg["bucket"], "ABI-L2-MCMIPF", utc(start), utc(end) + timedelta(minutes=9), None)
    todo = []
    for ts, key, size in keys:
        slot = ts.replace(minute=ts.minute // 10 * 10, second=0)
        if not have(out, slot):
            todo.append((slot, key, size))
    click.echo(f"{sat}: {len(keys)} slots listed, {len(todo)} to fetch ({sum(s for *_, s in todo) / 1e9:.1f} GB if taken whole)")
    if not todo:
        return
    geo = DiskGeometry.abi(cfg["lon"])

    def one(item):
        slot, key, size = item
        url = f"https://{cfg['bucket']}.s3.amazonaws.com/{key}"
        for attempt in range(6):                                    # patient: a DNS blip once killed a whole run
            try:
                t0 = time.time()
                bands, nbytes = abi_read(url, ("CMI_C02", "CMI_C13"))
                return slot, size, bands, nbytes, time.time() - t0
            except Exception as e:                                  # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {slot_name(slot)}: {e}")
                time.sleep(10 * (attempt + 1))
        return slot, size, None, 0, 0.0

    total = 0
    with ThreadPoolExecutor(workers) as pool:
        for slot, size, bands, nbytes, dt in pool.map(one, todo):
            if bands is None:
                click.echo(f"   FAILED {slot_name(slot)}")
                continue
            save16(out / f"{slot_name(slot)}_vis.png", geo.sample(bands["CMI_C02"]), VIS_SCALE)
            save16(out / f"{slot_name(slot)}_bt.png", geo.sample(bands["CMI_C13"]), BT_SCALE)
            total += nbytes
            log.write(json.dumps({"sat": sat, "slot": slot_name(slot), "file_bytes": size, "fetched_bytes": nbytes, "seconds": round(dt, 1)}) + "\n")
            log.flush()
            click.echo(f"   {slot_name(slot)}  {nbytes / 1e6:.1f} MB of {size / 1e6:.0f}  {dt:.0f}s")
    click.echo(f"fetched {total / 1e9:.2f} GB")


def fetch_ahi(sat, cfg, out, start, end, workers, log):
    todo = [t for t in slots_between(start, end) if not have(out, t)]
    click.echo(f"{sat}: {len(todo)} slots to fetch")
    geo = DiskGeometry.ahi(cfg["lon"])
    lon, lat = grid_lonlat()
    seen = sat_zenith(lon, lat, cfg["lon"]) < ZEN_ZERO

    def one(t):
        for attempt in range(6):
            try:
                t0 = time.time()
                if cos_solar_zenith(t, lon, lat)[seen].max() < 0.12:     # the whole disk is dark: band 3 (129 MB) is never used
                    vis = np.full((5500, 5500), np.nan, np.float32)
                else:
                    vis = ahi_read(cfg["bucket"], t, 3, 4)
                bt = ahi_read(cfg["bucket"], t, 13, 1)
                return t, vis, bt, time.time() - t0
            except Exception as e:                                  # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {slot_name(t)}: {e}")
                time.sleep(10 * (attempt + 1))
        return t, None, None, 0.0

    with ThreadPoolExecutor(workers) as pool:
        for t, vis, bt, dt in pool.map(one, todo):
            if vis is None or bt is None:
                click.echo(f"   MISSING {slot_name(t)}")
                continue
            save16(out / f"{slot_name(t)}_vis.png", geo.sample(vis), VIS_SCALE)
            save16(out / f"{slot_name(t)}_bt.png", geo.sample(bt), BT_SCALE)
            log.write(json.dumps({"sat": sat, "slot": slot_name(t), "seconds": round(dt, 1)}) + "\n")
            log.flush()
            click.echo(f"   {slot_name(t)}  {dt:.0f}s")


def fetch_wms(sat, cfg, out, start, end, workers, log):
    step = cfg["step"]
    todo = [t for t in slots_between(start, end, step) if not have(out, t)]
    click.echo(f"{sat}: {len(todo)} slots to fetch")

    def one(t):
        t0 = time.time()
        vis, ir = wms_grey(sat, "vis", t), wms_grey(sat, "ir", t)
        if vis is None and ir is not None:                          # MTG's vis layer is transparent at night: dark, not missing
            vis = np.where(np.isfinite(ir), 1.0, np.nan).astype(np.float32)
        return t, vis, ir, time.time() - t0

    with ThreadPoolExecutor(workers) as pool:
        for t, vis, ir, dt in pool.map(one, todo):
            if vis is None or ir is None:
                click.echo(f"   MISSING {slot_name(t)} vis={vis is not None} ir={ir is not None}")
                continue
            save16(out / f"{slot_name(t)}_vis.png", vis, GREY_SCALE)
            save16(out / f"{slot_name(t)}_bt.png", ir, GREY_SCALE)
            log.write(json.dumps({"sat": sat, "slot": slot_name(t), "seconds": round(dt, 1)}) + "\n")
            log.flush()
            click.echo(f"   {slot_name(t)}  {dt:.0f}s")


def fetch_official(sat, cfg, out, start, end, workers, log, range_reads):
    """The agency's own calibrated bands (scripts/official_sources.py) onto the grid, written like ABI's:
    reflectance factor x VIS_SCALE and kelvin x BT_SCALE, plus raw/<sat>/source.json so every later stage
    treats the satellite as physical (no LUT, the Blue Marble cap applies)."""
    import official_sources as osrc
    kind = cfg["official"]
    step = cfg.get("step", 10)
    todo = [t for t in slots_between(start, end, step) if not have(out, t)]
    marker = out / "source.json"
    if not marker.exists():
        if any(out.glob("*_bt.png")):
            raise click.ClickException(f"{out} already holds legacy frames; use a fresh CLOUDS_ROOT or move them away first")
        marker.write_text(json.dumps({"source": kind, "physical": True, "since": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    _PHYSICAL[sat] = True
    click.echo(f"{sat} <- {kind}: {len(todo)} slots to fetch")
    if not todo:
        return
    lon, lat = grid_lonlat()
    geo_cache: dict = {}

    def geometry(key, make):
        if key not in geo_cache:
            geo_cache[key] = make()
        return geo_cache[key]

    def one(t):
        for attempt in range(4):
            try:
                t0 = time.time()
                if kind == "ptree":
                    got = osrc.ptree_l1(t)
                    if got is None:
                        return t, None, 0, 0.0
                    bands, n = got
                    g = geometry("ptree", lambda: DiskGeometry.latlon(cfg["lon"], **osrc.PTREE_GRID))
                    return t, {"vis": g.sample(bands["vis"]), "bt": g.sample(bands["bt"])}, n, time.time() - t0
                if kind == "fci":
                    got = osrc.fci_slot(t, use_range=range_reads, workers=max(2, workers))
                    if got is None:
                        return t, None, 0, 0.0
                    d, n = got
                    pv, pb = d["pack"][osrc.FCI_VIS], d["pack"][osrc.FCI_IR]
                    gv = geometry("fci_vis", lambda: DiskGeometry.fci(cfg["lon"], pv["xscale"], pv["xoff"], pv["yscale"], pv["yoff"], pool=4))
                    gb = geometry("fci_ir", lambda: DiskGeometry.fci(cfg["lon"], pb["xscale"], pb["xoff"], pb["yscale"], pb["yoff"], pool=2))
                    return t, {"vis": gv.sample(d["vis"]), "bt": gb.sample(d["bt"])}, n, time.time() - t0
                if kind == "seviri":
                    got = osrc.seviri_slot(t, workdir=ROOT / "tmp")
                    if got is None:
                        return t, None, 0, 0.0
                    d, n = got
                    g = geometry("seviri", lambda: DiskGeometry.area(cfg["lon"], d["area"], pool=1, blur=0.8))
                    return t, {"vis": g.sample(d["vis"]), "bt": g.sample(d["bt"])}, n, time.time() - t0
                raise click.ClickException(f"unknown official source {kind}")
            except Exception as e:                                  # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {slot_name(t)}: {type(e).__name__}: {e}")
                time.sleep(15 * (attempt + 1))
        return t, None, 0, 0.0

    (ROOT / "tmp").mkdir(exist_ok=True)
    # FCI parallelises inside a slot (its chunk files); the others across slots
    n_slots = 1 if kind == "fci" else workers
    total = 0
    with ThreadPoolExecutor(n_slots) as pool:
        for t, bands, nbytes, dt in pool.map(one, todo):
            if bands is None:
                click.echo(f"   MISSING {slot_name(t)}")
                continue
            save16(out / f"{slot_name(t)}_vis.png", bands["vis"], VIS_SCALE)
            save16(out / f"{slot_name(t)}_bt.png", bands["bt"], BT_SCALE)
            total += nbytes
            log.write(json.dumps({"sat": sat, "slot": slot_name(t), "source": kind, "fetched_bytes": nbytes, "seconds": round(dt, 1)}) + "\n")
            log.flush()
            click.echo(f"   {slot_name(t)}  {nbytes / 1e6:.1f} MB  {dt:.0f}s")
    click.echo(f"fetched {total / 1e9:.2f} GB")


# ---------------------------------------------------------------- cloud products (Stage B): the agencies' retrievals

# The page's opacity from an optical thickness. The brief's 1 - exp(-0.75 tau) saturates a tau-2 cloud at 0.78,
# where the two-band field puts a marine stratocumulus deck (tau ~10) at ~0.5, and no single exponential fits
# (best g 0.07, rmse 0.139). The two-stream reflectance A x/(1+x), x = (1-g) tau, does: fitted on GOES-East
# 12 Sep 17Z against the two-band opacity, A 0.88, g 0.86 (the droplet asymmetry parameter; textbook 0.85),
# rmse 0.123, within 0.05 of the bin medians from tau 3 to tau 70. So the scale of the field is kept and only
# the PATTERN comes from the retrieval, which is the point: every satellite's tau is the same physical quantity.
PRODUCTS = {"cod_a": 0.88, "cod_asym": 0.86,                          # opacity = A x/(1+x), x = (1-asym) tau (see cod_opacity)
            "cod_mu_lo": 0.25, "cod_mu_hi": 0.45,                    # COD is daytime-only: it takes over from the two-band
            "clear_damp": 0.25}                                      # opacity through this cos(solar zenith) ramp; a confident
                                                                     # clear classification scales the two-band opacity by this


def products_geometry(sat: str, kind: str, info, cache: dict) -> DiskGeometry:
    """A DiskGeometry for one product field's source grid, cached by (kind, packing)."""
    key = (kind, json.dumps(info, sort_keys=True, default=str) if kind != "area" else (str(info.area_extent), info.shape))
    if key in cache:
        return cache[key]
    sub_lon = SATS[sat]["lon"]
    if kind == "abi":
        px = abs(info["xscale"])                                    # 5.6e-5 rad = 2 km, 1.12e-4 = 4 km, 2.8e-4 = 10 km
        pool, blur = (2, 1.0) if px < 8e-5 else (1, 1.0) if px < 2e-4 else (1, 0.5)
        g = DiskGeometry.geos(sub_lon, "x", lambda ax, ay: ((ax - info["xoff"]) / info["xscale"], (ay - info["yoff"]) / info["yscale"]), pool)
        g.blur = blur
    elif kind == "fci":
        g = DiskGeometry.fci(sub_lon, info["xscale"], info["xoff"], info["yscale"], info["yoff"], pool=2)
    elif kind == "latlon":
        g = DiskGeometry.latlon(sub_lon, **info)
    elif kind == "area":
        g = DiskGeometry.area(sub_lon, info, pool=1, blur=0.8 if info.shape[0] > 2000 else 0.5)
    else:
        raise ValueError(kind)
    cache[key] = g
    return g


def products_paths(sat: str, t: datetime) -> dict[str, Path]:
    d = ROOT / "products" / sat
    return {k: d / f"{slot_name(t)}_{k}.png" for k in ("cod", "cth", "cls")}


def load_products(sat: str, t: datetime) -> dict | None:
    """The sampled products for one satellite slot, or None. cod: tau (NaN = none); cth: metres (NaN = none);
    clear / ice: fractions 0..1 (NaN = unclassified)."""
    p = products_paths(sat, t)
    if not p["cls"].exists():
        return None
    out = {}
    cls = np.asarray(Image.open(p["cls"])).astype(np.float32)
    valid = cls[..., 2] > 0
    out["clear"] = np.where(valid, cls[..., 0] / 255.0, np.nan).astype(np.float32)
    out["ice"] = np.where(valid, cls[..., 1] / 255.0, np.nan).astype(np.float32)
    out["cod"] = load16(p["cod"], 100.0) if p["cod"].exists() else None
    out["cth"] = (load16(p["cth"], 1.0) - 1.0) if p["cth"].exists() else None
    return out


@cli.command()
@click.option("--sat", required=True, type=click.Choice(sorted(SATS)))
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--workers", default=4, show_default=True)
def products(sat, start, end, workers):
    """The agency's cloud retrievals for this satellite onto the grid, data/clouds/products/<sat>/<slot>_{cod,cth,cls}.png:
    cod = optical thickness x 100 (uint16, 0 = no retrieval; 0.01 is written for a retrieved zero), cth = top height in
    metres + 1 (uint16, 0 = none), cls = RGB: clear fraction x 255, ice-top fraction x 255, 255 where classified.
    GOES: NOAA ABI-L2 CODF/ACHAF/ACTPF (no key). Himawari: JAXA CLP (daytime). MTG: EUMETSAT OCA (day and night).
    Meteosat-9: EUMETSAT CTH + cloud mask (GRIB), no optical thickness exists for that service."""
    import official_sources as osrc
    cfg = SATS[sat]
    out = ROOT / "products" / sat
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "tmp").mkdir(exist_ok=True)
    step = cfg.get("step", 10)
    todo = [t for t in slots_between(start, end, step) if not products_paths(sat, t)["cls"].exists()]
    click.echo(f"{sat} products: {len(todo)} slots")
    log = open(ROOT / "fetch.jsonl", "a")
    geoms: dict = {}
    glock = __import__("threading").Lock()

    def source(t):
        if cfg["kind"] == "abi":
            return osrc.goes_products(cfg["bucket"], t)
        if sat == "himawari9":
            return osrc.clp_products(t)
        if sat == "mtg":
            return osrc.oca_products(t)
        if sat == "iodc":
            return osrc.iodc_products(t, workdir=ROOT / "tmp")
        raise click.ClickException(f"no product source for {sat}")

    def one(t):
        for attempt in range(4):
            try:
                t0 = time.time()
                got = source(t)
                if got is None:
                    return t, None, 0, 0.0
                d, n = got
                fields = {}
                for k in ("cod", "cth", "clear", "ice"):
                    if k in d:
                        kind, info = d["geom"][k]
                        with glock:
                            g = products_geometry(sat, kind, info, geoms)
                        fields[k] = g.sample(d[k])
                return t, fields, n, time.time() - t0
            except Exception as e:                                  # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {slot_name(t)}: {type(e).__name__}: {e}")
                time.sleep(15 * (attempt + 1))
        return t, None, 0, 0.0

    total = 0
    with ThreadPoolExecutor(workers) as pool:
        for t, f, nbytes, dt in pool.map(one, todo):
            p = products_paths(sat, t)
            if f is None:
                click.echo(f"   none {slot_name(t)}")
                continue
            clear = f.get("clear")
            if clear is None:
                click.echo(f"   no classification {slot_name(t)}")
                continue
            ice = f.get("ice", np.full_like(clear, np.nan))
            valid = np.isfinite(clear)
            cls = np.zeros((H, W, 3), np.uint8)
            cls[..., 0] = np.clip(np.nan_to_num(clear) * 255, 0, 255).round()
            cls[..., 1] = np.clip(np.nan_to_num(ice) * 255, 0, 255).round()
            cls[..., 2] = valid * 255
            Image.fromarray(cls).save(p["cls"].with_suffix(".tmp.png"), compress_level=3)
            p["cls"].with_suffix(".tmp.png").rename(p["cls"])
            if "cod" in f:
                cod = f["cod"]
                cod = np.where(np.isnan(cod) & (clear > 0.5), 0.0, cod)          # a clear pixel has tau 0, not "none"
                save16(p["cod"], np.clip(cod, 0, 655.0), 100.0)                   # save16 floors a stored value at 1, so a true 0 reads 0.01
            if "cth" in f:
                save16(p["cth"], np.clip(f["cth"], 0, 65000) + 1.0, 1.0)
            total += nbytes
            log.write(json.dumps({"sat": sat, "slot": slot_name(t), "products": list(f), "fetched_bytes": nbytes, "seconds": round(dt, 1)}) + "\n")
            log.flush()
            click.echo(f"   {slot_name(t)}  {nbytes / 1e6:.1f} MB  {dt:.0f}s  {' '.join(sorted(f))}")
    click.echo(f"fetched {total / 1e9:.2f} GB")


@cli.command()
@click.option("--sat", required=True)
@click.option("--slots", default=8, show_default=True, help="how many slots, spread over what is on disk")
def calibrate(sat, slots):
    """Quantile-match this WMS satellite's greys onto its reference's physical values in their overlap:
    a monotonic grey -> reflectance (day pixels) and grey -> kelvin (all pixels) table."""
    cfg = SATS[sat]
    ref = cfg["ref"]
    lon, lat = grid_lonlat()
    both = (sat_zenith(lon, lat, cfg["lon"]) < 55) & (sat_zenith(lon, lat, SATS[ref]["lon"]) < 55)
    mine = raw_slots(sat)
    theirs = set(raw_slots(ref))
    common = [t for t in mine if t in theirs]
    pick = common[:: max(1, len(common) // slots)][:slots]
    click.echo(f"{sat} <- {ref}: {len(common)} common slots, using {len(pick)}; overlap {both.mean() * 100:.1f}% of grid")
    lut = {}
    for band, cond in (("vis", "day"), ("bt", "all")):
        gs, vs = [], []
        for t in pick:
            g = load16(ROOT / "raw" / sat / f"{slot_name(t)}_{band}.png", GREY_SCALE)
            v = load_band(ref, t, band)
            sel = both & np.isfinite(g) & np.isfinite(v)
            if cond == "day":
                sel &= cos_solar_zenith(t, lon, lat) > 0.4        # same instant, same sun: raw grey against raw reflectance
            if sel.sum() < 5000:
                continue
            gs.append(g[sel]); vs.append(v[sel])
        g, v = np.concatenate(gs), np.concatenate(vs)
        q = np.linspace(0, 1, 257)
        # quantile matching can only build an INCREASING map; an infrared grey is bright = cold, so
        # check the sign first and match against the reversed quantiles when it is negative
        sign = float(np.corrcoef(g, v)[0, 1])
        gq, vq = np.quantile(g, q), np.quantile(v, q if sign >= 0 else 1 - q)
        click.echo(f"   {band}: correlation {sign:+.2f} -> {'increasing' if sign >= 0 else 'DECREASING'} map")
        # collapse repeated grey quantiles so the table is a function
        grey, value = [], []
        for a, b in zip(gq, vq):
            if grey and a <= grey[-1]:
                value[-1] = (value[-1] + b) / 2
            else:
                grey.append(float(a)); value.append(float(b))
        key = "vis" if band == "vis" else "bt"
        lut[key] = {"grey": grey, "value": value, "n": int(len(g)), "slots": [slot_name(t) for t in pick]}
        click.echo(f"   {band}: {len(g)} pixels, grey {grey[0]:.0f}..{grey[-1]:.0f} -> {value[0]:.3f}..{value[-1]:.3f}")
    (ROOT / f"lut_{sat}.json").write_text(json.dumps(lut))
    click.echo(f"wrote {ROOT / f'lut_{sat}.json'}")


def raw_slots(sat: str) -> list[datetime]:
    return sorted(parse_slot(p.name) for p in (ROOT / "raw" / sat).glob("*_bt.png"))


@cli.command()
@click.option("--sat", required=True)
def clearsky(sat):
    raw = ROOT / "raw" / sat
    out = ROOT / "clear" / sat
    out.mkdir(parents=True, exist_ok=True)
    slots = raw_slots(sat)
    lon, lat = grid_lonlat()
    click.echo(f"{sat}: {len(slots)} slots")

    vmin = np.full((H, W), np.inf, np.float32)
    bt_all = np.full((H, W), -np.inf, np.float32)
    by_tod: dict[int, np.ndarray] = {}
    lut = lut_for(sat)
    for i, t in enumerate(slots):
        mu = cos_solar_zenith(t, lon, lat).astype(np.float32)
        vis = load_band(sat, t, "vis", lut)
        rn = np.where((mu > 0.35) & np.isfinite(vis), vis / np.maximum(mu, 0.35), np.inf)
        np.minimum(vmin, rn, out=vmin)
        bt = np.nan_to_num(load_band(sat, t, "bt", lut), nan=-np.inf)
        np.maximum(bt_all, bt, out=bt_all)
        tod = (t.hour * 60 + t.minute) // 10
        by_tod[tod] = np.maximum(by_tod[tod], bt) if tod in by_tod else bt
        if i % 24 == 0:
            click.echo(f"   read {slot_name(t)}")
    vmin[~np.isfinite(vmin)] = np.nan
    # A place under cloud in every daytime frame has no clear glimpse, and its "darkest" is the cloud.
    # Cap the reference with a prior from Blue Marble: the red channel (0.64 um, the same band) linearised,
    # generous by 1.4x + 0.04 for relief shading and aerosol; open water is ~0.05 in band 2 away from glint.
    # Only for satellites that deliver calibrated reflectance: a WMS grey mapped by quantiles is not
    # accurate enough over bright deserts for an absolute cap (the Sahara came out as solid cloud).
    water = water_mask()
    if not physical(sat):
        cap = np.full((H, W), 0.09, np.float32)
        vmin = np.where(np.isfinite(vmin), np.maximum(vmin, 0.0), cap)
    else:
        cap = np.maximum(albedo_prior() * 1.4 + 0.04, 0.09).astype(np.float32)   # a floor, not a water branch: coast pixels are mixed
        vmin = np.where(np.isfinite(vmin), np.minimum(vmin, cap), cap)
    save16(out / "vis_clear.png", vmin, VIS_SCALE)
    save16(out / "bt_warmest.png", np.where(np.isfinite(bt_all), bt_all, np.nan), BT_SCALE)
    # the floor under the per-time-of-day reference: the sea barely cools overnight, deserts cool 20-30 K
    floor = np.where(water, bt_all - DIURNAL_K, bt_all - DIURNAL_LAND_K).astype(np.float32)
    for tod in range(144):
        near = [by_tod[(tod + k) % 144] for k in range(-6, 7) if (tod + k) % 144 in by_tod]
        if not near:
            continue
        m = np.maximum(np.maximum.reduce(near), floor)
        save16(out / f"bt_clear_{tod:03d}.png", np.where(np.isfinite(m), m, np.nan), BT_SCALE)
    click.echo("clear-sky maps written")


def basemap_rgb() -> np.ndarray:
    """Blue Marble on the cloud grid, float32 0..1 sRGB."""
    base = np.asarray(Image.open("data/earth/_base.jpg").convert("RGB").resize((W, W // 2), Image.BILINEAR)).astype(np.float32) / 255
    top = (W // 2 - H) // 2
    return base[top:top + H]


def water_mask() -> np.ndarray:
    """Crude: Blue Marble pixels that are dark and blue. Confines the glint fade and the ocean albedo prior."""
    base = basemap_rgb()
    r, b = base[..., 0], base[..., 2]
    return (b > r * 1.25) & (r < 70 / 255)


def albedo_prior() -> np.ndarray:
    """Blue Marble's red channel, sRGB-decoded to linear reflectance."""
    r = basemap_rgb()[..., 0]
    return np.where(r <= 0.04045, r / 12.92, ((r + 0.055) / 1.055) ** 2.4).astype(np.float32)


# vis_k / bt_k set how fast the saturating curve reaches 1. At 0.26 / 16 K (2026-09-18) most cloud sat
# at 0.9+ and the field read as on/off white lace on the page; at 0.45 / 26 K a thick cloud top
# (reflectance 0.8) lands at ~0.81, a bright one (1.0) at ~0.88 and marine stratocumulus (0.35) at
# ~0.40, so nothing clips and the page can shade by opacity (the shader inverts this curve for
# brightness: `-k ln(1 - opacity)` is the reflectance again).
PARAMS = {"bt_margin": 3.0, "bt_k": 26.0, "vis_margin": 0.03, "vis_margin_mu": 0.012, "vis_margin_rel": 0.12, "vis_k": 0.45,
          "glint_in": 18.0, "glint_out": 36.0}


def opacity_frame(sat: str, t: datetime, lon, lat, vis_clear, water, clear_dir: Path, p: dict, lut=None) -> np.ndarray | None:
    bt = load_band(sat, t, "bt", lut)
    vis = load_band(sat, t, "vis", lut)
    if bt is None or vis is None:
        return None
    tod = (t.hour * 60 + t.minute) // 10
    bt_clear = load16(clear_dir / f"bt_clear_{tod:03d}.png", BT_SCALE)
    mu = cos_solar_zenith(t, lon, lat).astype(np.float32)

    # saturating curves, not ramps: reflectance and the temperature deficit both saturate with optical depth,
    # so a marine stratocumulus deck (reflectance ~0.35, 6 K colder than the sea) reads as ~0.6, thick cloud as ~1
    ir = 1 - np.exp(-np.clip(bt_clear - bt - p["bt_margin"], 0, None) / p["bt_k"])
    rn = vis / np.maximum(mu, 0.08)
    # absolute + low-sun + relative: bright ground varies ~10-15% over the day (BRDF), and a one-day
    # "darkest" reference sits at the bottom of that range
    margin = p["vis_margin"] + p["vis_margin_mu"] / np.maximum(mu, 0.08) + p["vis_margin_rel"] * vis_clear
    vo = 1 - np.exp(-np.clip(rn - vis_clear - margin, 0, None) / p["vis_k"])
    wv = np.clip((mu - 0.10) / 0.20, 0, 1)
    wv = wv * wv * (3 - 2 * wv)
    c_in, c_out = math.cos(math.radians(p["glint_in"])), math.cos(math.radians(p["glint_out"]))
    g = np.clip((c_in - glint_cos(t, lon, lat, SATS[sat]["lon"])) / (c_in - c_out), 0, 1)
    wv = np.where(water, wv * g, wv)
    op = ir + wv * np.clip(np.nan_to_num(vo, nan=0.0) - ir, 0, None)
    op = apply_products(sat, t, op, mu)
    op[~np.isfinite(bt)] = np.nan
    return op


def cod_opacity(tau: np.ndarray) -> np.ndarray:
    """Opacity from an optical thickness: the two-stream reflectance of a non-absorbing cloud, A x / (1 + x)
    with x = (1 - g) tau. g is the droplet asymmetry parameter (fitted 0.86; textbook 0.85) and A the ceiling
    a very thick cloud reaches on the page (0.88, where the two-band field puts one). See `fit-products`."""
    x = (1 - PRODUCTS["cod_asym"]) * tau
    return (PRODUCTS["cod_a"] * x / (1 + x)).astype(np.float32)


def apply_products(sat: str, t: datetime, op: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """Where the agency's retrieval exists for this slot, the optical thickness takes over the daytime opacity
    through the cos(solar zenith) ramp cod_mu_lo..cod_mu_hi (COD is retrieved by day only; night keeps the
    two-band field, blended through twilight as before), and a confident clear classification scales the
    remaining two-band opacity by clear_damp (mostly the infrared night term over cooling deserts, which the
    mask sees as ground). Without products the field is untouched."""
    pr = load_products(sat, t)
    if pr is None:
        return op
    if pr["cod"] is not None:
        tau = pr["cod"]
        has = np.isfinite(tau)
        wc = np.clip((mu - PRODUCTS["cod_mu_lo"]) / (PRODUCTS["cod_mu_hi"] - PRODUCTS["cod_mu_lo"]), 0, 1)
        wc = wc * wc * (3 - 2 * wc) * has
        op = op * (1 - wc) + cod_opacity(np.nan_to_num(tau)) * wc
    clear = pr["clear"]
    damp = 1 - (1 - PRODUCTS["clear_damp"]) * np.clip(np.nan_to_num(clear, nan=0.0), 0, 1)
    return (op * damp).astype(np.float32)


@cli.command()
@click.option("--sat", required=True)
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--workers", default=8, show_default=True)
def opacity(sat, start, end, workers):
    out = ROOT / "op" / sat
    out.mkdir(parents=True, exist_ok=True)
    clear_dir = ROOT / "clear" / sat
    lon, lat = grid_lonlat()
    vis_clear = np.nan_to_num(load16(clear_dir / "vis_clear.png", VIS_SCALE), nan=0.08)
    water = water_mask()
    slots = [t for t in raw_slots(sat) if (not start or t >= utc(start)) and (not end or t <= utc(end))]
    wgt = view_weight(SATS[sat]["lon"])
    Image.fromarray((wgt * 255).round().astype(np.uint8)).save(ROOT / f"weight_{sat}.png")
    lut = lut_for(sat)

    def one(t):
        op = opacity_frame(sat, t, lon, lat, vis_clear, water, clear_dir, PARAMS, lut)
        q = (np.nan_to_num(op, nan=0.0) * 254).round().astype(np.uint8) + 1          # 0 = no data
        q[~np.isfinite(op)] = 0
        Image.fromarray(q).save(out / f"{slot_name(t)}.png", compress_level=3)
        return t

    with ThreadPoolExecutor(workers) as pool:
        for i, t in enumerate(pool.map(one, slots)):
            if i % 24 == 0:
                click.echo(f"   {slot_name(t)}")
    click.echo(f"{len(slots)} opacity frames in {out}")


@cli.command()
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--sats", default="goes19", show_default=True, help="comma-separated")
@click.option("--hold-min", default=30, show_default=True, help="reuse a satellite's previous frame for this long")
def blend(start, end, sats, hold_min):
    names = sats.split(",")
    out = ROOT / "frames"
    out.mkdir(parents=True, exist_ok=True)
    weights = {s: view_weight(SATS[s]["lon"]) for s in names}
    cover = np.clip(sum(weights.values()), 0, 1)
    Image.fromarray((cover * 255).round().astype(np.uint8)).save(ROOT / "coverage.png")
    # where two satellites both weigh at least half, their opacities should agree: measure it
    pairs = [(a, b, (weights[a] > 0.5) & (weights[b] > 0.5)) for i, a in enumerate(names) for b in names[i + 1:]]
    pairs = [(a, b, m) for a, b, m in pairs if m.sum() > 1000]
    t, t1 = utc(start), utc(end)
    log = open(ROOT / "frames.jsonl", "a")
    while t <= t1:
        num = np.zeros((H, W), np.float32)
        den = np.zeros((H, W), np.float32)
        row = {"t": slot_name(t), "held": {}, "seam": {}}
        ops = {}
        for s in names:
            for back in range(0, hold_min + 1, 5):
                f = ROOT / "op" / s / f"{slot_name(t - timedelta(minutes=back))}.png"
                if f.exists():
                    q = np.asarray(Image.open(f)).astype(np.float32)
                    w = weights[s] * (q > 0)
                    o = (q - 1) / 254.0
                    num += w * o
                    den += w
                    ops[s] = np.where(q > 0, o, np.nan)
                    if back:
                        row["held"][s] = back
                    break
            else:
                row["held"][s] = None
        for a, b, m in pairs:
            if a in ops and b in ops:
                d = (ops[a] - ops[b])[m]
                d = d[np.isfinite(d)]
                if d.size:
                    row["seam"][f"{a}-{b}"] = {"bias": round(float(d.mean()), 4), "mad": round(float(np.abs(d).mean()), 4)}
        op = np.where(den > 0, num / np.maximum(den, 1e-6), 0) * np.clip(den, 0, 1)
        row["mean"] = round(float(op.mean()), 4)
        Image.fromarray((op * 255).round().astype(np.uint8)).save(out / f"{slot_name(t)}.png", compress_level=3)
        log.write(json.dumps(row) + "\n")
        t += timedelta(minutes=10)
    click.echo(f"frames in {out}")


# ---------------------------------------------------------------- height: cloud top from brightness temperature

LAPSE_K_PER_KM = 6.5                          # a standard atmosphere; the page says so
HEIGHT_MAX_KM = 16.0                          # the tropical tropopause; uint8 = km x 16 fills 0..255
HEIGHT_MIN_OP = 0.10                          # below this opacity a pixel has no top to measure


def height_frame(sat: str, t: datetime, clear_dir: Path, lut=None) -> np.ndarray | None:
    """Cloud-top height in km for one satellite frame: (T_clear - BT) / lapse rate, clamped 0..16.
    T_clear is the same per-pixel, per-time-of-day clear-sky reference `opacity` uses, so a clear pixel
    reads 0 by construction. Thin cirrus reads too LOW: its brightness temperature is a mix of the cold
    cloud and the warm ground beneath, so a 12 km veil can come out at 4-6 km. Accepted and said on the page."""
    bt = load_band(sat, t, "bt", lut)
    if bt is None:
        return None
    tod = (t.hour * 60 + t.minute) // 10
    bt_clear = load16(clear_dir / f"bt_clear_{tod:03d}.png", BT_SCALE)
    h = np.clip((bt_clear - bt) / LAPSE_K_PER_KM, 0, HEIGHT_MAX_KM)
    pr = load_products(sat, t)
    if pr is not None and pr["cth"] is not None:                # the agency's retrieved top where it has one
        cth = pr["cth"]
        h = np.where(np.isfinite(cth), np.clip(cth / 1000.0, 0, HEIGHT_MAX_KM), h)
    h[~np.isfinite(bt)] = np.nan
    return h.astype(np.float32)


@cli.command()
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--sats", default="goes19", show_default=True, help="comma-separated, the same list as blend")
@click.option("--hold-min", default=30, show_default=True, help="the same hold as blend, so a held opacity frame gets its own height")
@click.option("--workers", default=4, show_default=True)
def height(start, end, sats, hold_min, workers):
    """Cloud-top height per blended frame, data/clouds/height/<slot>.png, uint8 = km x 16.
    Blended across satellites with blend's view weights, restricted to pixels where THAT satellite sees
    cloud (opacity >= 0.1): a satellite that sees no cloud has no top to average in, so it abstains rather
    than voting 0 km. Where the blended opacity is under 0.1 the height is 0 whatever the temperatures say."""
    names = sats.split(",")
    out = ROOT / "height"
    out.mkdir(parents=True, exist_ok=True)
    weights = {s: view_weight(SATS[s]["lon"]) for s in names}
    luts = {s: lut_for(s) for s in names}
    slots = slots_between(start, end)

    def one(t):
        num = np.zeros((H, W), np.float32)
        den = np.zeros((H, W), np.float32)
        used = []
        for s in names:
            for back in range(0, hold_min + 1, 5):
                ts = t - timedelta(minutes=back)
                f = ROOT / "op" / s / f"{slot_name(ts)}.png"
                if f.exists():
                    h = height_frame(s, ts, ROOT / "clear" / s, luts[s])
                    if h is None:
                        break
                    q = np.asarray(Image.open(f)).astype(np.float32)
                    cloudy = (q > 0) & ((q - 1) / 254.0 >= HEIGHT_MIN_OP) & np.isfinite(h)
                    w = weights[s] * cloudy
                    num += w * np.nan_to_num(h, nan=0.0)
                    den += w
                    used.append(s)
                    break
        h = np.where(den > 0, num / np.maximum(den, 1e-6), 0.0)
        fb = ROOT / "frames" / f"{slot_name(t)}.png"
        if fb.exists():
            op = np.asarray(Image.open(fb)).astype(np.float32) / 255.0
            h[op < HEIGHT_MIN_OP] = 0.0
        q = np.clip(np.rint(h * 16.0), 0, 255).astype(np.uint8)
        Image.fromarray(q).save(out / f"{slot_name(t)}.png", compress_level=3)
        cl = h[h > 0]
        return t, used, (float(np.percentile(cl, 50)), float(np.percentile(cl, 90)), float(np.percentile(cl, 99))) if cl.size else (0.0, 0.0, 0.0)

    with ThreadPoolExecutor(workers) as pool:
        for i, (t, used, (p50, p90, p99)) in enumerate(pool.map(one, slots)):
            if i % 24 == 0:
                click.echo(f"   {slot_name(t)}  sats {len(used)}  cloudy-pixel height km p50 {p50:.1f} p90 {p90:.1f} p99 {p99:.1f}")
    click.echo(f"{len(slots)} height frames in {out}")


# ---------------------------------------------------------------- encode: opacity in luma, motion in chroma

# The clip is grey, so its two chroma planes are empty: they carry the optical flow to the NEXT frame,
# and the page warps each frame along it instead of dissolving. Three things about the browser decide
# the numbers below (measured 2026-09-18 in Chrome on macOS, `docs/globe-2026-09-18/roundtrip.md`):
#   1. the page only ever sees RGB, after the browser's own YUV->RGB, which CLAMPS to 0..255: chroma
#      survives only while the luma stays away from black and white, so opacity is coded into luma
#      levels Y_LO..Y_HI and the flow into +/-CHROMA levels around 128;
#   2. the browser ignored the range/matrix tags (every variant decoded as limited-range BT.601), so
#      the last STRIP rows of every frame hold eight known patches and the page solves the actual
#      RGB->YUV matrix from them instead of assuming one;
#   3. the clip is encoded limited-range (16..235) so that the browser's expansion is at least the
#      intended one where it is honoured.
Y_LO, Y_HI, CHROMA, STRIP = 60, 190, 24, 16
PATCHES = ((Y_LO, 128, 128), ((Y_LO + Y_HI) // 2, 128, 128), (Y_HI, 128, 128),
           ((Y_LO + Y_HI) // 2, 128 + CHROMA, 128), ((Y_LO + Y_HI) // 2, 128 - CHROMA, 128),
           ((Y_LO + Y_HI) // 2, 128, 128 + CHROMA), ((Y_LO + Y_HI) // 2, 128, 128 - CHROMA),
           ((Y_LO + Y_HI) // 2, 128 + CHROMA, 128 + CHROMA))
FLOW_W, FLOW_PAD = 1024, 64                   # flow is estimated at a quarter width; the seam at lon 180 is padded by wrapping


def flow_fields(frames: list[Path]) -> tuple[np.ndarray, float]:
    """DIS optical flow from each frame to the next, (n, 376, 1024, 2) float16 in pixels AT 4096 WIDE,
    x to the right and y DOWN the image; the last frame's is zero. Returns it and the 99th-percentile
    magnitude over every pixel of every frame."""
    import cv2
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    h = H * FLOW_W // W

    def small(p):
        a = cv2.resize(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE), (FLOW_W, h), interpolation=cv2.INTER_AREA)
        return np.concatenate([a[:, -FLOW_PAD:], a, a[:, :FLOW_PAD]], axis=1)

    out = np.zeros((len(frames), h, FLOW_W, 2), np.float16)
    prev = small(frames[0])
    for i in range(1, len(frames)):
        cur = small(frames[i])
        f = dis.calc(prev, cur, None)[:, FLOW_PAD:-FLOW_PAD] * (W / FLOW_W)
        out[i - 1] = cv2.GaussianBlur(f, (0, 0), 1.2)
        prev = cur
    mag = np.hypot(out[..., 0].astype(np.float32), out[..., 1].astype(np.float32))
    return out, float(np.percentile(mag, 99))


def height_rows(width: int) -> int:
    """Rows the height field takes under the opacity at this clip width: half resolution, so a quarter of
    the pixels, and a multiple of 16 so every plane stays macroblock-aligned (4096 wide: 752 rows)."""
    return (H * width // W) // 2


def yuv_frame(op: np.ndarray, flow: np.ndarray, p99: float, width: int, hgt: np.ndarray | None = None) -> bytes:
    """One raw yuv420p frame of the clip at `width`: luma = opacity coded Y_LO..Y_HI, chroma = flow
    coded 128 +/- CHROMA at the 99th percentile, plus the calibration strip along the bottom.
    With `hgt` (uint8 cloud-top height, km x 16, already at half this width), the height field sits in
    extra rows BETWEEN the opacity and the strip, coded into the same luma levels, its chroma flat 128:
    the page warps it with the opacity's own flow. 4096 wide that is 1504 + 752 + 16 = 2272 rows, under
    the 2304 a level-5.1 decoder is promised."""
    import cv2
    h = H * width // W
    hh = height_rows(width) if hgt is not None else 0
    y = np.empty((h + hh + STRIP, width), np.uint8)
    y[:h] = np.rint(Y_LO + op.astype(np.float32) * ((Y_HI - Y_LO) / 255.0)).astype(np.uint8)
    cw, ch = width // 2, (h + hh) // 2
    f = cv2.resize(flow.astype(np.float32), (cw, h // 2), interpolation=cv2.INTER_LINEAR)
    lv = np.clip(np.rint(128 + f * (CHROMA / max(p99, 1e-6))), 128 - CHROMA - 4, 128 + CHROMA + 4).astype(np.uint8)
    u = np.full((ch + STRIP // 2, cw), 128, np.uint8); v = np.full_like(u, 128)
    u[:h // 2] = lv[..., 0]; v[:h // 2] = lv[..., 1]
    if hgt is not None:
        hs = hgt if hgt.shape == (hh, cw) else cv2.resize(hgt, (cw, hh), interpolation=cv2.INTER_AREA)
        # the field is half width: it is placed in the left half of its rows, the right half stays Y_LO (0 km)
        y[h:h + hh] = Y_LO
        y[h:h + hh, :cw] = np.rint(Y_LO + hs.astype(np.float32) * ((Y_HI - Y_LO) / 255.0)).astype(np.uint8)
    pw = width // len(PATCHES)
    for i, (py, pu, pv) in enumerate(PATCHES):
        y[h + hh:, i * pw:(i + 1) * pw] = py
        u[ch:, i * pw // 2:(i + 1) * pw // 2] = pu
        v[ch:, i * pw // 2:(i + 1) * pw // 2] = pv
    return y.tobytes() + u.tobytes() + v.tobytes()


def encode_clip(dest: Path, frames: list[Path], heights: list[Path] | None, flow: np.ndarray, p99: float,
                width: int, fps: int, crf: int, x264: str) -> int:
    """One clip of `frames` (opacity PNGs) at `width`, with the height field when `heights` is given.
    `flow` is the flow row for each frame (already sliced to match). Returns the file size."""
    import cv2
    h = H * width // W
    hh = height_rows(width) if heights else 0
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{h + hh + STRIP}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
                           "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0", "-bf", "0", "-pix_fmt", "yuv420p",
                           "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                           "-x264-params", x264,
                           "-movflags", "+faststart+write_colr", "-an", str(dest)], stdin=subprocess.PIPE)
    for i, p in enumerate(frames):
        op = np.asarray(Image.open(p).convert("L"))
        if width != W:
            op = cv2.resize(op, (width, h), interpolation=cv2.INTER_AREA)
        hgt = cv2.resize(np.asarray(Image.open(heights[i]).convert("L")), (width // 2, hh), interpolation=cv2.INTER_AREA) if heights else None
        ff.stdin.write(yuv_frame(op, flow[i], p99, width, hgt))
    ff.stdin.close(); ff.wait()
    if ff.returncode:
        raise RuntimeError(f"ffmpeg failed on {dest}")
    return dest.stat().st_size


def poster(dest: Path, frame: Path) -> None:
    """The first frame in the page's decoded form: r = opacity (full 0..255), g = b = 128 (no motion)."""
    op = Image.open(frame).convert("L").resize((2048, H // 2), Image.LANCZOS)
    Image.merge("RGB", (op, Image.new("L", op.size, 128), Image.new("L", op.size, 128))).save(dest, quality=80)


def source_id(sat: str) -> str:
    m = ROOT / "raw" / sat / "source.json"
    if m.exists():
        return json.loads(m.read_text()).get("source", "official")
    return {"abi": "abi", "ahi": "hsd", "wms": "wms"}[SATS[sat]["kind"]]


def products_summary(sats: list[str], frames: list[Path]) -> dict:
    """Per satellite: which product fields exist and for how many of these frames (a 15-min satellite's product
    counts for the frame it was made for; the held frames between are blended from it by `opacity`)."""
    names = {p.stem for p in frames}
    out = {}
    for s in sats:
        d = ROOT / "products" / s
        if not d.exists():
            continue
        fields, n = set(), 0
        for cls in d.glob("*_cls.png"):
            slot = cls.name[:-8]
            if slot in names:
                n += 1
                fields.add("clear")
                for k in ("cod", "cth"):
                    if (d / f"{slot}_{k}.png").exists():
                        fields.add(k)
        if n:
            out[s] = {"fields": sorted(fields), "frames": n}
    return out


def seam_stats(root: Path, frames: list[Path]) -> tuple[dict, dict, list[str]]:
    """The seam numbers and hold counts from the blend (frames.jsonl), averaged over these frames."""
    names = {p.stem for p in frames}
    rows = [json.loads(l) for l in (root / "frames.jsonl").read_text().splitlines() if l.strip()] if (root / "frames.jsonl").exists() else []
    rows = {r["t"]: r for r in rows if r["t"] in names}.values()
    seam, held = {}, {}
    for r in rows:
        for k, v in r.get("seam", {}).items():
            seam.setdefault(k, []).append((v["bias"], v["mad"]))
        for s, back in r.get("held", {}).items():
            if back:
                held[s] = held.get(s, 0) + 1
    seam = {k: {"bias": round(float(np.mean([a for a, _ in v])), 4), "mad": round(float(np.mean([b for _, b in v])), 4), "frames": len(v)} for k, v in seam.items()}
    sats = sorted({s for k in seam for s in k.split("-")} | set(held))
    return seam, held, sats


@cli.command()
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option("--name", default="clouds", show_default=True)
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--fps", default=12, show_default=True, help="real frames per second of playback")
@click.option("--crf", default=26, show_default=True, help="26 keeps the Chile cloud streets; measured 1% mean error vs the PNGs")
@click.option("--crf-small", default=26, show_default=True)
@click.option("--aq", default="3:1.2", show_default=True, help="x264 aq-mode:aq-strength; mode 3 with 1.2 gives the near-clear ocean "
              "(opacity 0..0.1, where the eye looks and the codec spends nothing) its own bits; '' for x264's default")
@click.option("--flow-cache", default=None, type=click.Path(path_type=Path), help="reuse/save the flow fields (.npz)")
@click.option("--per-day", is_flag=True, help="one clip per UTC day (<name>_<date>.mp4, _2k, _poster) and a manifest with a `days` list; "
              "the page swaps clips at midnight. Without it, one clip of every frame, as before.")
@click.option("--assets", default="", help="base URL the page loads the clips and tiles from (Cloudflare R2), written into the "
              "manifest as `assets`; '' = relative to the page")
@click.option("--root", default=None, type=click.Path(path_type=Path), help="where frames/, height/ and frames.jsonl are (default data/clouds)")
@click.option("--days-only", default="", help="comma-separated dates to (re)encode with --per-day; other days keep their clips if "
              "the manifest already lists them")
def encode(out, name, start, end, fps, crf, crf_small, aq, flow_cache, per_day, assets, root, days_only):
    root = root or ROOT
    out.mkdir(parents=True, exist_ok=True)
    frames = sorted(p for p in (root / "frames").glob("*.png")
                    if (not start or parse_slot(p.name) >= utc(start)) and (not end or parse_slot(p.name) <= utc(end)))
    t0 = time.time()
    if flow_cache and flow_cache.exists():
        z = np.load(flow_cache); flow, p99 = z["flow"], float(z["p99"])
        if len(flow) != len(frames):
            raise click.ClickException(f"{flow_cache} has {len(flow)} frames, the selection {len(frames)}: recompute it")
    else:
        flow, p99 = flow_fields(frames)
        if flow_cache:
            np.savez(flow_cache, flow=flow, p99=p99)
    mag = np.hypot(flow[..., 0].astype(np.float32), flow[..., 1].astype(np.float32))
    click.echo(f"flow: {len(frames)} frames in {time.time() - t0:.0f}s; |flow| p50 {np.percentile(mag, 50):.2f} p90 {np.percentile(mag, 90):.2f} "
               f"p99 {p99:.2f} p99.9 {np.percentile(mag, 99.9):.2f} px at {W}; coded +/-{CHROMA} levels = +/-{p99:.2f} px")
    heights = [root / "height" / p.name for p in frames]
    with_height = bool(frames) and all(q.exists() for q in heights)
    click.echo(f"height field: {'in the clip' if with_height else 'NOT built (run `height` first); clip is opacity only'}")
    x264 = "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv"
    if aq:
        mode, strength = aq.split(":")
        x264 += f":aq-mode={mode}:aq-strength={strength}"
    seam, held, sats = seam_stats(root, frames)
    manifest = {"frames": [p.stem for p in frames], "fps": fps, "width": W, "height": H, "lat_max": LAT_MAX,
                "sizes": {}, "poster": f"{name}_poster.webp", "seam": seam, "held": held, "sats": sats,
                "curve": {"vis_k": PARAMS["vis_k"], "bt_k": PARAMS["bt_k"],        # the page inverts vis_k for the cloud's brightness
                          "cod_a": PRODUCTS["cod_a"], "cod_asym": PRODUCTS["cod_asym"], "clear_damp": PRODUCTS["clear_damp"]},
                # where each satellite's two bands came from (abi = NOAA's CMIP; ptree/fci/seviri = the agency's calibrated L1
                # via `fetch --source official`; hsd = raw Himawari segments; wms = EUMETView greys through a LUT) and which
                # agency cloud retrievals (cod, cth, clear, ice) were folded into how many of these frames per satellite
                "sources": {s: source_id(s) for s in sats}, "products": products_summary(sats, frames),
                "code": {"y_lo": Y_LO, "y_hi": Y_HI, "chroma": CHROMA, "strip": STRIP, "patches": PATCHES,
                         # height_rows: rows of cloud-top height under the opacity (at `width`; scale by the clip's actual
                         # width), the field itself in the LEFT half of those rows, luma y_lo..y_hi = 0..height_km_max
                         "height_rows": height_rows(W) if with_height else 0, "height_km_max": HEIGHT_MAX_KM,
                         "height_lapse_k_per_km": LAPSE_K_PER_KM, "height_min_op": HEIGHT_MIN_OP,
                         "flow_p99_px": round(p99, 3), "flow_width": W,
                         "flow_stats_px": {k: round(float(np.percentile(mag, q)), 3) for k, q in (("p50", 50), ("p90", 90), ("p99", 99), ("p999", 99.9))}}}
    if assets:
        manifest["assets"] = assets if assets.endswith("/") else assets + "/"
    if not per_day:
        for width, c, suffix in ((W, crf, ""), (W // 2, crf_small, "_2k")):
            dest = out / f"{name}{suffix}.mp4"
            manifest["sizes"][dest.name] = encode_clip(dest, frames, heights if with_height else None, flow, p99, width, fps, c, x264)
            click.echo(f"{dest}  {dest.stat().st_size / 1e6:.1f} MB  ({time.time() - t0:.0f}s)")
        poster(out / f"{name}_poster.webp", frames[0])
    else:
        # One clip per UTC day, the flow still continuous across midnight (the last frame of a day carries the motion to
        # the first of the next). The manifest's `days` carry each day's frames, files and sizes; the top level keeps
        # every frame in order and the same `code`, so a page reading a one-clip manifest still reads this one.
        old = json.loads((out / f"{name}.json").read_text()) if days_only and (out / f"{name}.json").exists() else {}
        old_days = {d["date"]: d for d in old.get("days", [])}
        redo = set(days_only.split(",")) if days_only else None
        dates = sorted({p.stem[:10] for p in frames})
        manifest["days"] = []
        for date in dates:
            sel = [i for i, p in enumerate(frames) if p.stem[:10] == date]
            fr = [frames[i] for i in sel]
            d = {"date": date, "off": sel[0], "n": len(sel), "frames": [p.stem for p in fr],
                 "clips": {"4k": f"{name}_{date}.mp4", "2k": f"{name}_{date}_2k.mp4"}, "poster": f"{name}_{date}_poster.webp", "sizes": {}}
            d["seam"], d["held"], _ = seam_stats(root, fr)
            if redo is not None and date not in redo and date in old_days and all((out / f).exists() for f in old_days[date]["clips"].values()):
                d["sizes"] = old_days[date]["sizes"]
                click.echo(f"{date}: kept ({d['n']} frames)")
            else:
                for width, c, key in ((W, crf, "4k"), (W // 2, crf_small, "2k")):
                    dest = out / d["clips"][key]
                    d["sizes"][key] = encode_clip(dest, fr, [heights[i] for i in sel] if with_height else None, flow[sel[0]:sel[-1] + 1], p99, width, fps, c, x264)
                    click.echo(f"{dest}  {dest.stat().st_size / 1e6:.1f} MB  ({time.time() - t0:.0f}s)")
                poster(out / d["poster"], fr[0])
            manifest["days"].append(d)
        for key, fname in (("4k", f"{name}.mp4"), ("2k", f"{name}_2k.mp4")):       # totals, under the one-clip names the page's footer reads
            manifest["sizes"][fname] = sum(d["sizes"][key] for d in manifest["days"])
        poster(out / f"{name}_poster.webp", frames[0])
        click.echo(f"{len(dates)} days, {dates[0]} to {dates[-1]}: 4k {manifest['sizes'][name + '.mp4'] / 1e6:.0f} MB, 2k {manifest['sizes'][name + '_2k.mp4'] / 1e6:.0f} MB")
    (out / f"{name}.json").write_text(json.dumps(manifest))
    click.echo(f"manifest {out / (name + '.json')}")

@cli.command()
@click.option("--out", default="site/globe", type=click.Path(path_type=Path), show_default=True)
def basemap(out):
    """Blue Marble (shaded relief + bathymetry) at 8192/4096/2048 wide and VIIRS city lights at 4096,
    from GIBS's 500m tile set, level 4 (20x10 tiles of 512 = 10240x5120), resized down."""
    from fetch_earth import get
    out.mkdir(parents=True, exist_ok=True)
    url = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/{layer}/default/{t}/500m/4/{y}/{x}.jpeg"

    def mosaic(layer):
        canvas = Image.new("RGB", (512 * 20, 512 * 10))
        tiles = [(x, y) for y in range(10) for x in range(20)]

        def one(xy):
            raw = get(url.format(layer=layer, t="default", y=xy[1], x=xy[0]), timeout=60, tries=3)
            return xy, raw

        with ThreadPoolExecutor(16) as ex:
            for (x, y), raw in ex.map(one, tiles):
                if raw is None:
                    raise RuntimeError(f"missing tile {x},{y} of {layer}")
                import io
                canvas.paste(Image.open(io.BytesIO(raw)).convert("RGB"), (x * 512, y * 512))
        return canvas

    base = mosaic("BlueMarble_ShadedRelief_Bathymetry")
    for w in (8192, 4096, 2048):
        base.resize((w, w // 2), Image.LANCZOS).save(out / f"base_{w}.jpg", quality=86 if w == 8192 else 88, optimize=True, progressive=True)
        click.echo(f"base_{w}.jpg  {(out / f'base_{w}.jpg').stat().st_size / 1e6:.2f} MB")
    lights = mosaic("VIIRS_CityLights_2012").convert("L")
    lights.resize((4096, 2048), Image.LANCZOS).save(out / "lights_4096.jpg", quality=85, optimize=True)
    click.echo(f"lights_4096.jpg  {(out / 'lights_4096.jpg').stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    cli()
