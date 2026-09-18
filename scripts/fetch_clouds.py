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
encode    Grey H.264 clips for the page, plus a poster frame and a manifest.
"""
from __future__ import annotations

import json
import math
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
ROOT = Path("data/clouds")
VIS_SCALE, BT_SCALE = 40000.0, 100.0
ZEN_FULL, ZEN_ZERO = 58.0, 66.0               # satellite zenith angle: full weight .. none
DIURNAL_K = 5.0                               # clear-sky bt over water is never below (warmest of all - this)
DIURNAL_LAND_K = 32.0                         # over land (deserts cool 20-30 K overnight)
R_EARTH, R_GEO = 6378.137, 42164.16

SATS = {
    "goes19": {"bucket": "noaa-goes19", "lon": -75.0, "kind": "abi", "name": "GOES-East"},
    "goes18": {"bucket": "noaa-goes18", "lon": -137.0, "kind": "abi", "name": "GOES-West"},
    "himawari9": {"bucket": "noaa-himawari9", "lon": 140.7, "kind": "ahi", "name": "Himawari-9"},
    # EUMETView serves contrast-stretched 8-bit greys, not numbers: `calibrate` maps them onto a
    # neighbour's physical values by quantile matching in the overlap (mtg <- goes19, iodc <- mtg)
    "mtg": {"lon": 0.0, "kind": "wms", "name": "Meteosat MTG-I1", "step": 10, "ref": "goes19",
            "vis": ("mtg_fd:vis06_hrfi", ""), "ir": ("mtg_fd:ir105_hrfi", "mtg_fd:mtg_fd_ir105_hrfi_grayscale")},
    "iodc": {"lon": 45.5, "kind": "wms", "name": "Meteosat-9", "step": 15, "ref": "mtg",
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
    """Fractional (row, col) in a 2x2-averaged geostationary fixed grid of every output pixel.
    `colrow(ax, ay)` turns scan angles (radians, x east, y north) into fractional 0-based native
    (col, row); ABI and AHI differ only in that function and the sweep axis."""

    def __init__(self, sub_lon: float, sweep: str, n: int, colrow):
        from pyproj import Transformer
        h = 35786023.0
        lon, lat = grid_lonlat()
        geos = f"+proj=geos +h={h} +lon_0={sub_lon} +sweep={sweep} +a=6378137 +b=6356752.31414 +units=m +no_defs"
        gx, gy = Transformer.from_crs("EPSG:4326", geos, always_xy=True).transform(lon.astype(np.float64), lat.astype(np.float64))
        ok = np.isfinite(gx) & np.isfinite(gy)
        cols, rows = colrow(np.where(ok, gx, 0) / h, np.where(ok, gy, 0) / h)
        ok &= sat_zenith(lon, lat, sub_lon) < ZEN_ZERO + 1
        self.ok, self.n = ok, n
        self.rows = ((rows - 0.5) / 2).astype(np.float32)
        self.cols = ((cols - 0.5) / 2).astype(np.float32)

    @classmethod
    def abi(cls, sub_lon: float, scale: float = 5.6e-05, off: float = 0.151844):
        return cls(sub_lon, "x", 5424, lambda ax, ay: ((ax + off) / scale, (off - ay) / scale))

    @classmethod
    def ahi(cls, sub_lon: float, cfac: float = 20466275, coff: float = 2750.5):
        f = cfac / 65536 * 180 / math.pi                       # HSD: pixel = COFF + angle_deg * CFAC / 2^16, 1-based
        return cls(sub_lon, "y", 5500, lambda ax, ay: (coff + ax * f - 1, coff - ay * f - 1))

    def sample(self, raw: np.ndarray) -> np.ndarray:
        """raw: (n, n) float32 with NaN for fill. Returns the output grid, NaN where unseen."""
        from scipy.ndimage import gaussian_filter, map_coordinates
        m = self.n // 2
        a = raw.reshape(m, 2, m, 2)
        good = np.isfinite(a)
        cnt = good.sum((1, 3))
        pooled = np.where(good, a, 0).sum((1, 3)) / np.maximum(cnt, 1)
        wgt = gaussian_filter((cnt > 0).astype(np.float32), 1.0)
        pooled = gaussian_filter(np.where(cnt > 0, pooled, 0).astype(np.float32), 1.0) / np.maximum(wgt, 1e-3)
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


def load_band(sat: str, t: datetime, band: str, lut: dict | None = None) -> np.ndarray | None:
    """Physical values (reflectance or K) for any satellite, NaN = no data."""
    p = ROOT / "raw" / sat / f"{slot_name(t)}_{band}.png"
    if not p.exists():
        return None
    if SATS[sat]["kind"] != "wms":
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
def fetch(sat, start, end, workers):
    cfg = SATS[sat]
    out = ROOT / "raw" / sat
    out.mkdir(parents=True, exist_ok=True)
    log = open(ROOT / "fetch.jsonl", "a")
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
    lut = json.loads((ROOT / f"lut_{sat}.json").read_text()) if SATS[sat]["kind"] == "wms" else None
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
    if SATS[sat]["kind"] == "wms":
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


PARAMS = {"bt_margin": 3.0, "bt_k": 16.0, "vis_margin": 0.03, "vis_margin_mu": 0.012, "vis_margin_rel": 0.12, "vis_k": 0.26,
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
    op[~np.isfinite(bt)] = np.nan
    return op


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
    lut = json.loads((ROOT / f"lut_{sat}.json").read_text()) if SATS[sat]["kind"] == "wms" else None

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


@cli.command()
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option("--name", default="clouds", show_default=True)
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--fps", default=12, show_default=True, help="real frames per second of playback")
@click.option("--crf", default=26, show_default=True, help="26 keeps the Chile cloud streets; measured 1% mean error vs the PNGs")
@click.option("--crf-small", default=26, show_default=True)
def encode(out, name, start, end, fps, crf, crf_small):
    out.mkdir(parents=True, exist_ok=True)
    frames = sorted(p for p in (ROOT / "frames").glob("*.png")
                    if (not start or parse_slot(p.name) >= utc(start)) and (not end or parse_slot(p.name) <= utc(end)))
    lst = ROOT / "_encode.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\nduration {1 / fps}\n" for p in frames))
    sizes = {}
    for width, c, suffix in ((W, crf, ""), (W // 2, crf_small, "_2k")):
        dest = out / f"{name}{suffix}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", f"scale={width}:{H * width // W}:flags=area,format=yuv420p", "-r", str(fps),
                        "-c:v", "libx264", "-preset", "slow", "-crf", str(c),
                        "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0", "-bf", "0",
                        "-color_range", "pc", "-movflags", "+faststart", "-an", str(dest)], check=True)
        sizes[dest.name] = dest.stat().st_size
        click.echo(f"{dest}  {dest.stat().st_size / 1e6:.1f} MB")
    Image.open(frames[0]).resize((2048, H // 2), Image.LANCZOS).save(out / f"{name}_poster.webp", quality=80)
    # the seam numbers and hold counts from the blend, averaged over these frames
    names = {p.stem for p in frames}
    rows = [json.loads(l) for l in (ROOT / "frames.jsonl").read_text().splitlines() if l.strip()]
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
    manifest = {"frames": [p.stem for p in frames], "fps": fps, "width": W, "height": H, "lat_max": LAT_MAX,
                "sizes": sizes, "poster": f"{name}_poster.webp", "seam": seam, "held": held, "sats": sats}
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
