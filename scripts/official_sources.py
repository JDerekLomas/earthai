"""The agencies' own numbers for the cloud layer: JAXA P-Tree (Himawari-9) and the EUMETSAT Data
Store (MTG-I1 FCI, Meteosat-9 SEVIRI), plus their cloud products and NOAA's GOES L2 cloud products.

PRIOR ART: scripts/fetch_clouds.py reads Himawari from raw HSD segments with a home-made Planck
reader and MTG/Meteosat-9 from EUMETView's contrast-stretched 8-bit WMS pictures quantile-matched
against a neighbour. This module gives fetch_clouds the same two bands (0.64 um reflectance factor,
10.4 um brightness temperature) calibrated by the agencies, and the cloud retrievals (optical depth,
top height, phase) that Stage B turns into opacity. fetch_clouds keeps the old readers as fallback.

Credentials come from `.secrets.json` in the repo root (gitignored) or the file named by
$EARTHAI_SECRETS; they are never printed. Keys: EUMETSAT_CONSUMER_KEY / _SECRET (Data Store,
bearer minted by client_credentials, 1 h), PTREE_FTP_HOST / _USER / _PASS (plain FTP).

Sources and what one slot costs (measured 2026-09-19 from the laptop):
  Himawari-9 L1  ftp.ptree.jaxa.jp /jma/netcdf/YYYYMM/DD/NC_H09_<t>_R21_FLDK.02801_02401.nc: the full disc
                 on a 0.05 deg lat/lon grid (2801 x 2401, 70-210 E, +/-60), 16 bands already calibrated
                 (albedo_NN = reflectance x cos(solar zenith), tbb_NN in K), 106-143 MB. Read by FTP byte
                 range (REST + RETR, closed early) like the GOES HDF5 path: albedo_03 + tbb_13 + SOZ come
                 to ~14.6 MB in 9 requests. The 0.02 deg file (697 MB) is not needed at a 10 km output pixel.
  Himawari-9 L2  /pub/himawari/L2/CLP/010/YYYYMM/DD/HH/NC_H09_<t>_L2CLP010_FLDK.02401_02401.nc: cloud optical
                 thickness (CLOT), top height (CLTH, km), top temperature, effective radius, ISCCP type, QA
                 on the same 0.05 deg grid (80-200 E); 0.5-2.6 MB, daytime retrievals only.
  MTG-I1 L1c     EO:EUM:DAT:0662 (FCI FDHSI): a 10-min slot is ~40 chunk files of row bands, ~20 MB each, all
                 16 channels in every chunk, plus a trailer; ~815 MB whole. vis_06 (1 km, 11136 wide) and
                 ir_105 (2 km, 5568) read straight from each chunk's HDF5 (radiance -> reflectance factor by
                 pi d^2 / E_sun; radiance -> BT by the file's c1, c2, wavenumber, a, b). Whole-chunk download
                 by default (the Data Store answers a byte range in ~1 s, a chunk needs ~11 of them, so ranges
                 cost more time than bytes); `--range` reads ~210 MB a slot in ~440 requests instead.
  Meteosat-9     EO:EUM:DAT:MSG:HRSEVIRI-IODC: one .nat of 271 MB every 15 min (zipped download ~133 MB),
                 read by satpy's seviri_l1b_native (VIS006 reflectance %, IR_108 K) from a temp file.
  Cloud products EUMETSAT: EO:EUM:DAT:0684 Optimal Cloud Analysis (MTG, COT + CTP + phase, ~167 MB),
                 EO:EUM:DAT:0681 CTTH (MTG, ~77 MB), EO:EUM:DAT:MSG:CTH-IODC (Meteosat-9 top height, GRIB,
                 0.4 MB; there is no OCA for the Indian Ocean service). NOAA: ABI-L2-CODF / ACHAF / ACMF /
                 ACTPF on noaa-goes19 and noaa-goes18, anonymous.
Terms: JAXA/JMA data may not be redistributed as numbers; the page ships derived pictures and credits
"Himawari data: JAXA/JMA (P-Tree)". EUMETSAT: "Meteosat data: EUMETSAT". NOAA: "GOES data: NOAA".
"""
from __future__ import annotations

import ftplib
import io
import json
import math
import os
import re
import socket
import tempfile
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

AU_KM = 149597870.7
FCI_COLLECTION = "EO:EUM:DAT:0662"
FCI_OCA, FCI_CTTH = "EO:EUM:DAT:0684", "EO:EUM:DAT:0681"
SEVIRI_IODC = "EO:EUM:DAT:MSG:HRSEVIRI-IODC"
IODC_CTH, IODC_CLM = "EO:EUM:DAT:MSG:CTH-IODC", "EO:EUM:DAT:MSG:CLM-IODC"
EUM_API = "https://api.eumetsat.int"
PTREE_L1 = "/jma/netcdf/{t:%Y%m}/{t:%d}/NC_H09_{t:%Y%m%d_%H%M}_R21_FLDK.02801_02401.nc"
PTREE_CLP = "/pub/himawari/L2/CLP/010/{t:%Y%m}/{t:%d}/{t:%H}/NC_H09_{t:%Y%m%d_%H%M}_L2CLP010_FLDK.02401_02401.nc"
PTREE_GRID = {"lat0": 60.0, "lon0": 70.0, "step": 0.05, "nlat": 2401, "nlon": 2801}       # L1
CLP_GRID = {"lat0": 60.0, "lon0": 80.0, "step": 0.05, "nlat": 2401, "nlon": 2401}         # L2 CLP


def secrets() -> dict:
    p = Path(os.environ.get("EARTHAI_SECRETS", ".secrets.json"))
    if not p.exists():
        raise FileNotFoundError(f"no credentials at {p} (set EARTHAI_SECRETS)")
    return json.loads(p.read_text())


# ---------------------------------------------------------------- HDF5 helpers shared by every netCDF4 source

class Reads:
    """File-like adapter over a `_range(lo, hi) -> bytes` method, in BLOCK-sized pieces kept for the
    session, so h5py can walk a remote netCDF4's superblock, headers and chunk index in a handful of
    requests. Subclasses supply `_range` and `size`."""
    BLOCK = 1 << 18

    def __init__(self):
        self.pos, self.blocks, self.fetched, self.reqs = 0, {}, 0, 0

    def _block(self, i: int) -> bytes:
        if i not in self.blocks:
            lo = i * self.BLOCK
            self.blocks[i] = self._range(lo, min(lo + self.BLOCK, self.size))
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

    def tell(self) -> int: return self.pos
    def readable(self): return True
    def seekable(self): return True
    def writable(self): return False
    def flush(self): pass
    def close(self): pass


def chunk_plan(h5, names: tuple[str, ...]) -> dict:
    """For each named dataset: shape, chunk shape, scale/offset/fill, and every chunk's (byte offset,
    size, first row). Works on any h5py.File, local or remote."""
    plan = {}
    for v in names:
        d = h5[v]
        info = [d.id.get_chunk_info(i) for i in range(d.id.get_num_chunks())]
        at = d.attrs
        fill = at["_FillValue"][0] if "_FillValue" in at else (at["missing_value"][0] if "missing_value" in at else None)
        plan[v] = {"shape": d.shape, "chunks": d.chunks, "dtype": d.dtype,
                   "sf": float(at["scale_factor"][0]) if "scale_factor" in at else 1.0,
                   "ao": float(at["add_offset"][0]) if "add_offset" in at else 0.0,
                   "fill": None if fill is None else int(fill), "shuffle": bool(d.shuffle),
                   "info": [(c.byte_offset, c.size, c.chunk_offset) for c in info]}
    return plan


def assemble(buf: bytes, lo: int, p: dict) -> np.ndarray:
    """Inflate one variable's chunks out of the byte span `buf` (starting at file offset `lo`) into a
    float32 array with NaN for fill. Handles the HDF5 shuffle filter and any dtype width."""
    dt = np.dtype(p["dtype"])
    arr = np.full(p["shape"], p["fill"] if p["fill"] is not None else 0, dt)
    ch = p["chunks"]
    for o, s, off in p["info"]:
        raw = zlib.decompress(buf[o - lo:o - lo + s])
        b = np.frombuffer(raw, np.uint8)
        if p["shuffle"] and dt.itemsize > 1:
            b = b.reshape(dt.itemsize, -1).T.copy()
        b = b.view(dt).reshape(ch)
        sl = tuple(slice(off[k], min(off[k] + ch[k], p["shape"][k])) for k in range(len(ch)))
        arr[sl] = b[tuple(slice(0, sl[k].stop - sl[k].start) for k in range(len(ch)))]
    a = arr.astype(np.float32) * p["sf"] + p["ao"]
    if p["fill"] is not None:
        a[arr == p["fill"]] = np.nan
    return a


def read_vars(f: Reads, names: tuple[str, ...]) -> tuple[dict[str, np.ndarray], int]:
    """The named variables of a remote netCDF4 as float32 physical arrays, one range per variable
    (a variable's chunks sit contiguously in these files). Returns them and the bytes fetched."""
    import h5py
    h = h5py.File(f, "r")
    plan = chunk_plan(h, names)
    h.close()
    out = {}
    for v, p in plan.items():
        lo = min(o for o, _, _ in p["info"])
        hi = max(o + s for o, s, _ in p["info"])
        out[v] = assemble(f._range(lo, hi), lo, p)
    return out, f.fetched


# ---------------------------------------------------------------- JAXA P-Tree (FTP)

class FtpRange(Reads):
    """Byte ranges over one FTP control connection: REST <lo> then RETR, read what is wanted, close the
    data socket; the server's 426/450 for the abort (or 226 if it finished anyway) is swallowed."""
    BLOCK = 1 << 16

    def __init__(self, ftp: ftplib.FTP, path: str):
        super().__init__()
        self.ftp, self.path = ftp, path
        self.size = ftp.size(path)

    def _range(self, lo: int, hi: int) -> bytes:
        n = hi - lo
        conn = self.ftp.transfercmd("RETR " + self.path, rest=lo)
        buf = bytearray()
        try:
            while len(buf) < n:
                b = conn.recv(min(1 << 20, n - len(buf)))
                if not b:
                    break
                buf += b
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        try:
            self.ftp.voidresp()
        except (ftplib.error_temp, ftplib.error_perm):
            pass
        self.reqs += 1
        self.fetched += len(buf)
        if len(buf) < n:
            raise OSError(f"short FTP range {lo}-{hi} on {self.path}: {len(buf)}")
        return bytes(buf[:n])


_ftp_local = threading.local()


def ptree_ftp() -> ftplib.FTP:
    """One logged-in FTP connection per thread, reconnected when it has gone stale."""
    ftp = getattr(_ftp_local, "ftp", None)
    if ftp is not None:
        try:
            ftp.voidcmd("NOOP")
            return ftp
        except (ftplib.all_errors, OSError):
            pass
    s = secrets()
    ftp = ftplib.FTP(s["PTREE_FTP_HOST"], timeout=180)
    ftp.login(s["PTREE_FTP_USER"], s["PTREE_FTP_PASS"])
    ftp.voidcmd("TYPE I")
    _ftp_local.ftp = ftp
    return ftp


def ptree_exists(path: str) -> bool:
    try:
        ptree_ftp().size(path)
        return True
    except ftplib.error_perm:
        return False


def ptree_l1(t: datetime) -> tuple[dict[str, np.ndarray], int] | None:
    """Himawari-9 band 03 reflectance factor (albedo / cos SOZ), band 13 BT (K) and the solar zenith,
    each (2401, 2801) float32 with NaN outside the disc, from the 0.05 deg gridded file by FTP ranges.
    None when the slot does not exist (02:40 and 14:40 are housekeeping)."""
    path = PTREE_L1.format(t=t)
    ftp = ptree_ftp()
    if not ptree_exists(path):
        return None
    f = FtpRange(ftp, path)
    v, n = read_vars(f, ("albedo_03", "tbb_13", "SOZ", "SAZ"))
    # albedo_NN's long_name says "reflectance*cos(SOZ)", but measured against the HSD reflectance factor on
    # 2026-09-12 03Z the ratio is 0.988 FLAT across solar zenith (dividing by cos made it 1/mu): it is the
    # reflectance factor already, Earth-Sun-distance corrected (the 1.2%), so it is used as is.
    vis = np.where(v["SOZ"] < 89, v["albedo_03"], np.nan).astype(np.float32)   # night: no visible term
    seen = np.isfinite(v["SAZ"]) & (v["SAZ"] < 89)              # off the disc the grid still carries numbers (albedo 0, a BT)
    vis[~seen] = np.nan
    bt = v["tbb_13"].copy(); bt[~seen] = np.nan
    return {"vis": vis, "bt": bt}, n


def ptree_clp(t: datetime) -> tuple[dict[str, np.ndarray], int] | None:
    """Himawari-9 L2 cloud properties (JAXA CLP v010): optical thickness, top height (km), top temperature,
    ISCCP type and QA on the 0.05 deg grid (2401 x 2401, 80-200 E). Whole file (0.5-2.6 MB)."""
    path = PTREE_CLP.format(t=t)
    ftp = ptree_ftp()
    if not ptree_exists(path):
        return None
    buf = io.BytesIO()
    ftp.retrbinary("RETR " + path, buf.write, blocksize=1 << 20)
    import h5py
    h = h5py.File(buf, "r")
    out = {}
    for k in ("CLOT", "CLTH", "CLTT", "CLTYPE", "QA"):
        d = h[k]
        a = d[()]
        v = a.astype(np.float32) * float(d.attrs["scale_factor"][0]) + float(d.attrs["add_offset"][0])
        # missing_value says -32768 but the files hold -32766/-32767 (and 255 for the type): anything under
        # valid_min (0 for every one of these) is no retrieval
        v[(a == d.attrs["missing_value"][0]) | (a < 0)] = np.nan
        out[k] = v
    h.close()
    return out, buf.tell()


# ---------------------------------------------------------------- EUMETSAT Data Store

class EumToken:
    """A client-credentials bearer, refreshed a few minutes before it expires; thread-safe."""

    def __init__(self):
        self.tok, self.exp, self.lock = None, 0.0, threading.Lock()

    def get(self) -> str:
        with self.lock:
            if self.tok is None or time.time() > self.exp - 300:
                s = secrets()
                r = requests.post(f"{EUM_API}/token", data={"grant_type": "client_credentials"},
                                  auth=(s["EUMETSAT_CONSUMER_KEY"], s["EUMETSAT_CONSUMER_SECRET"]), timeout=60)
                r.raise_for_status()
                j = r.json()
                self.tok, self.exp = j["access_token"], time.time() + float(j.get("expires_in", 3600))
            return self.tok

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get()}"}


TOKEN = EumToken()
_eum_local = threading.local()


def eum_session() -> requests.Session:
    s = getattr(_eum_local, "s", None)
    if s is None:
        s = _eum_local.s = requests.Session()
    return s


def eum_search(collection: str, t0: datetime, t1: datetime, n: int = 200) -> list[dict]:
    """Products of `collection` whose sensing window overlaps [t0, t1], oldest first, as the OpenSearch
    features (id, properties.date 'start/end', links.sip-entries, links.data)."""
    out, start = [], 0
    while True:
        r = eum_session().get(f"{EUM_API}/data/search-products/1.0.0/os", headers=TOKEN.headers(), timeout=120,
                              params={"format": "json", "pi": collection, "dtstart": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "dtend": t1.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": n, "si": start, "sort": "start,time,0"})
        r.raise_for_status()
        j = r.json()
        feats = j.get("features", [])
        out += feats
        if len(feats) < n:
            return out
        start += n


def eum_product_start(f: dict) -> datetime:
    return datetime.strptime(f["properties"]["date"].split("/")[0][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def eum_entries(f: dict, pattern: str = "") -> list[dict]:
    """The product's files ({title, href}) whose name matches `pattern`."""
    ents = f["properties"]["links"].get("sip-entries", [])
    return [e for e in ents if re.search(pattern, e.get("title", ""))]


def eum_download(href: str, dest: Path | None = None, tries: int = 4) -> bytes | Path:
    """One entry, streamed into memory (dest None) or to a file; retried on the Data Store's stalls."""
    for attempt in range(tries):
        try:
            with eum_session().get(href, headers=TOKEN.headers(), timeout=(30, 300), stream=True) as g:
                g.raise_for_status()
                if dest is None:
                    return b"".join(g.iter_content(1 << 20))
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as out:
                    for b in g.iter_content(1 << 20):
                        out.write(b)
                tmp.rename(dest)
                return dest
        except (requests.RequestException, OSError) as e:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


class HttpRange(Reads):
    """Byte ranges on a Data Store entry (it answers 206 with the bearer)."""

    def __init__(self, href: str):
        super().__init__()
        self.href = href
        g = eum_session().get(href, headers={**TOKEN.headers(), "Range": "bytes=0-0"}, timeout=120)
        g.raise_for_status()
        self.size = int(g.headers["Content-Range"].split("/")[1])

    def _range(self, lo: int, hi: int) -> bytes:
        for attempt in range(4):
            try:
                g = eum_session().get(self.href, headers={**TOKEN.headers(), "Range": f"bytes={lo}-{hi - 1}"}, timeout=(30, 300))
                g.raise_for_status()
                self.reqs += 1
                self.fetched += len(g.content)
                return g.content
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))


# ---------------------------------------------------------------- MTG-I1 FCI L1c

FCI_VIS, FCI_IR = "vis_06", "ir_105"


def fci_chunk(h5, ch: str) -> tuple[np.ndarray, int, dict]:
    """One channel of one FCI chunk file (an open h5py.File): the calibrated rows (reflectance factor for a
    solar channel, BT in K for a thermal one), the 0-based first row on the reference grid, and the grid's
    packing (x/y scale and offset, ssd)."""
    g = h5[f"data/{ch}/measured"]
    d = g["effective_radiance"]
    cnt = d[()]
    rad = cnt.astype(np.float32) * float(d.attrs["scale_factor"][0]) + float(d.attrs["add_offset"][0])
    bad = (cnt == 65535) | (cnt > 4095)
    if float(g["channel_effective_solar_irradiance"][()]) < 1e30:       # a solar channel
        esd = h5["state/celestial/earth_sun_distance"][()]
        esd = float(np.nanmean(np.where(esd < 1e30, esd, np.nan))) / AU_KM
        val = rad * math.pi * esd * esd / float(g["channel_effective_solar_irradiance"][()])
    else:
        c1, c2 = float(g["radiance_to_bt_conversion_constant_c1"][()]), float(g["radiance_to_bt_conversion_constant_c2"][()])
        vc = float(g["radiance_to_bt_conversion_coefficient_wavenumber"][()])
        a, b = float(g["radiance_to_bt_conversion_coefficient_a"][()]), float(g["radiance_to_bt_conversion_coefficient_b"][()])
        with np.errstate(divide="ignore", invalid="ignore"):
            val = ((c2 * vc / np.log1p(c1 * vc ** 3 / np.maximum(rad, 1e-9))) - b) / a
        bad |= rad <= 0
    val[bad] = np.nan
    row0 = int(g["start_position_row"][()]) - 1
    x, y = g["x"], g["y"]
    pack = {"xscale": float(x.attrs["scale_factor"][0]), "xoff": float(x.attrs["add_offset"][0]),
            "yscale": float(y.attrs["scale_factor"][0]), "yoff": float(y.attrs["add_offset"][0]),
            "n": int(g["end_position_column"][()])}
    return val.astype(np.float32), row0, pack



def fci_slot(t: datetime, use_range: bool = False, workers: int = 6, log=None) -> tuple[dict, int] | None:
    """MTG-I1 vis_06 (11136 x 11136, reflectance factor) and ir_105 (5568 x 5568, K) for the 10-min slot
    starting at `t`, assembled from the product's chunk files (south-up: array row = FCI row - 1). Returns
    the arrays, each channel's grid packing, and bytes fetched; None when the Data Store has no product."""
    from concurrent.futures import ThreadPoolExecutor
    import h5py
    feats = [f for f in eum_search(FCI_COLLECTION, t - timedelta(minutes=1), t + timedelta(minutes=9))
             if abs((eum_product_start(f) - t).total_seconds()) < 300]
    if not feats:
        return None
    f = feats[0]
    chunks = eum_entries(f, r"CHK-BODY.*\.nc$")
    if len(chunks) < 30:
        return None
    full = {FCI_VIS: None, FCI_IR: None}
    packs = {}
    total = [0]
    lock = threading.Lock()

    def one(e):
        if use_range:
            src = HttpRange(e["href"])
            h = h5py.File(src, "r")
            got = {ch: fci_chunk(h, ch) for ch in (FCI_VIS, FCI_IR)}
            h.close()
            n = src.fetched
        else:
            raw = eum_download(e["href"])
            n = len(raw)
            h = h5py.File(io.BytesIO(raw), "r")
            got = {ch: fci_chunk(h, ch) for ch in (FCI_VIS, FCI_IR)}
            h.close()
        with lock:
            total[0] += n
            for ch, (val, row0, pack) in got.items():
                if full[ch] is None:
                    full[ch] = np.full((pack["n"], pack["n"]), np.nan, np.float32)
                    packs[ch] = pack
                full[ch][row0:row0 + val.shape[0]] = val
        return e["title"]

    with ThreadPoolExecutor(workers) as ex:
        for _ in ex.map(one, chunks):
            pass
    return {"vis": full[FCI_VIS], "bt": full[FCI_IR], "pack": packs, "chunks": len(chunks)}, total[0]


# ---------------------------------------------------------------- Meteosat-9 SEVIRI (native, via satpy)

def seviri_slot(t: datetime, workdir: Path | None = None) -> tuple[dict, int] | None:
    """Meteosat-9 (IODC, 45.5 E) VIS006 reflectance factor and IR_108 BT, (3712, 3712) float32 with NaN
    outside the disc, plus the satpy AreaDefinition, for the 15-min repeat cycle whose nominal start is
    `t` (the product's own start time is up to a few seconds later). The .nat (271 MB) is streamed to a
    temp file for satpy and deleted."""
    import warnings
    warnings.filterwarnings("ignore")
    feats = [f for f in eum_search(SEVIRI_IODC, t - timedelta(minutes=1), t + timedelta(minutes=14))
             if 0 <= (eum_product_start(f) - t).total_seconds() < 600]
    if not feats:
        return None
    ents = eum_entries(feats[0], r"\.nat$")
    if not ents:
        return None
    e = ents[0]
    d = Path(tempfile.mkdtemp(prefix="seviri_", dir=str(workdir) if workdir else None))
    try:
        dest = eum_download(e["href"], d / e["title"])
        n = dest.stat().st_size
        from satpy import Scene
        sc = Scene(filenames=[str(dest)], reader="seviri_l1b_native")
        sc.load(["VIS006", "IR_108"])
        vis = sc["VIS006"].values.astype(np.float32) / 100.0
        bt = sc["IR_108"].values.astype(np.float32)
        area = sc["IR_108"].attrs["area"]
        return {"vis": vis, "bt": bt, "area": area}, n
    finally:
        for p in d.glob("*"):
            p.unlink()
        d.rmdir()


# ---------------------------------------------------------------- NOAA GOES L2 cloud products (AWS, anonymous)

GOES_L2 = {"cod": ("ABI-L2-CODF", "COD"), "height": ("ABI-L2-ACHAF", "HT"), "mask": ("ABI-L2-ACMF", "BCM"), "phase": ("ABI-L2-ACTPF", "Phase")}


class S3Range(Reads):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.s = requests.Session()
        r = self.s.head(url, timeout=60)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])

    def _range(self, lo: int, hi: int) -> bytes:
        r = self.s.get(self.url, headers={"Range": f"bytes={lo}-{hi - 1}"}, timeout=300)
        r.raise_for_status()
        self.reqs += 1
        self.fetched += len(r.content)
        return r.content


def goes_l2(bucket: str, product: str, var: str, t: datetime) -> dict | None:
    """One GOES ABI L2 full-disc product variable and its DQF for the slot starting at `t`, by byte range,
    with the fixed-grid packing of that product's own resolution (COD is 4 km, ACHA 10 km, ACTP 2 km).
    Returns {var, dqf, pack: {xscale, xoff, yscale, yoff}, bytes} or None when the slot is absent."""
    import sys
    import h5py
    sys.path.insert(0, str(Path(__file__).parent))
    from fetch_goes_aws import list_keys
    keys = list_keys(bucket, product, t - timedelta(seconds=30), t + timedelta(minutes=9, seconds=30), None)
    if not keys:
        return None
    url = f"https://{bucket}.s3.amazonaws.com/{keys[0][1]}"
    f = S3Range(url)
    h = h5py.File(f, "r")
    pack = {"xscale": float(h["x"].attrs["scale_factor"][0]), "xoff": float(h["x"].attrs["add_offset"][0]),
            "yscale": float(h["y"].attrs["scale_factor"][0]), "yoff": float(h["y"].attrs["add_offset"][0])}
    h.close()
    v, n = read_vars(f, (var, "DQF"))
    return {"value": v[var], "dqf": v["DQF"], "pack": pack, "bytes": n}


# ---------------------------------------------------------------- cloud products, one shape for every agency
#
# Each `*_products(t)` returns None (no product for the slot) or a dict of fields on the SOURCE grid, each
# float32 with NaN where the retrieval says nothing:
#   cod    cloud optical thickness (0.55-0.64 um), 0 where the product classifies the pixel as clear
#   cth    cloud-top height in metres; NaN where clear or not retrieved
#   clear  1 where the classification is clear sky, 0 where cloud, NaN where unclassified
#   ice    1 where the top is ice (or mixed / multilayer with ice above), 0 where water, NaN where unclassified
# plus `geom`, what fetch_clouds needs to place it: ("abi", pack) / ("fci", pack) / ("latlon", grid) /
# ("area", satpy_area), one per field when the fields sit on different grids.

def goes_products(bucket: str, t: datetime) -> tuple[dict, int] | None:
    """NOAA's ABI L2: COD (4 km, daytime), ACHA height (10 km), ACTP phase (2 km: 0 clear, 1 liquid,
    2 supercooled, 3 mixed, 4 ice, 5 unknown). ~16 MB a slot by byte range."""
    cod = goes_l2(bucket, "ABI-L2-CODF", "COD", t)
    ht = goes_l2(bucket, "ABI-L2-ACHAF", "HT", t)
    ph = goes_l2(bucket, "ABI-L2-ACTPF", "Phase", t)
    if ht is None or ph is None:
        return None
    out, n = {}, ht["bytes"] + ph["bytes"]
    phase = ph["value"]
    known = np.isfinite(phase) & (phase != 5)
    out["clear"] = np.where(known, (phase == 0).astype(np.float32), np.nan).astype(np.float32)
    out["ice"] = np.where(known & (phase > 0), (phase >= 3).astype(np.float32), np.nan).astype(np.float32)
    out["cth"] = np.where(np.isfinite(ht["value"]) & (ht["dqf"] == 0), ht["value"], np.nan).astype(np.float32)
    geom = {"clear": ("abi", ph["pack"]), "ice": ("abi", ph["pack"]), "cth": ("abi", ht["pack"])}
    if cod is not None:
        n += cod["bytes"]
        # DQF 0 = good retrieval; the product leaves clear pixels at 0 with a non-zero DQF, keep those as 0
        c = cod["value"]
        out["cod"] = np.where(np.isfinite(c), c, np.nan).astype(np.float32)
        geom["cod"] = ("abi", cod["pack"])
    out["geom"] = geom
    return out, n


def clp_products(t: datetime) -> tuple[dict, int] | None:
    """JAXA's CLP: optical thickness, top height (km -> m), ISCCP type (0 clear; 1-3 Ci/Cs/Dc high, 4-6 Ac/As/Ns
    mid, 7-9 Cu/Sc/St low). Daytime only; at night the fields are all missing and None is returned."""
    got = ptree_clp(t)
    if got is None:
        return None
    d, n = got
    ty = d["CLTYPE"]
    if np.isfinite(d["CLOT"]).mean() < 0.01:
        return None
    known = np.isfinite(ty)
    out = {"cod": np.where(known & (ty == 0), 0.0, d["CLOT"]).astype(np.float32),
           "cth": np.where(known & (ty > 0), d["CLTH"] * 1000.0, np.nan).astype(np.float32),
           "clear": np.where(known, (ty == 0).astype(np.float32), np.nan).astype(np.float32),
           "ice": np.where(known & (ty > 0), (ty <= 3).astype(np.float32), np.nan).astype(np.float32)}
    out["geom"] = {k: ("latlon", CLP_GRID) for k in ("cod", "cth", "clear", "ice")}
    return out, n


def oca_products(t: datetime) -> tuple[dict, int] | None:
    """EUMETSAT's Optimal Cloud Analysis for MTG-I1 (2 km, day and night): COT in log10 for up to two layers
    (summed), top height in m, phase (0 none, 1 water, 2 ice, 3 multilayer ice over water, 4 other). The file
    interleaves its variables' chunks, so it comes whole (~167 MB)."""
    import h5py
    feats = [f for f in eum_search(FCI_OCA, t - timedelta(minutes=1), t + timedelta(minutes=9))
             if abs((eum_product_start(f) - t).total_seconds()) < 300]
    if not feats:
        return None
    ents = eum_entries(feats[0], r"OCA--FD------NC4E.*\.nc$")
    if not ents:
        return None
    raw = eum_download(ents[0]["href"])
    h = h5py.File(io.BytesIO(raw), "r")
    cot = h["retrieved_cloud_optical_thickness"]
    c = cot[()]
    fill = cot.attrs["_FillValue"][0]
    tau = np.zeros(c.shape[:2], np.float32)
    seen = np.zeros(c.shape[:2], bool)
    for layer in range(c.shape[2]):
        v = c[..., layer]
        ok = v != fill
        tau[ok] += 10.0 ** (v[ok].astype(np.float32) * float(cot.attrs["scale_factor"][0]))
        seen |= ok
    cth = h["retrieved_cloud_top_height"][()].astype(np.float32)
    cth[h["retrieved_cloud_top_height"][()] == 65535] = np.nan
    phase = h["retrieved_cloud_phase"][()].astype(np.int16)
    pack = {"xscale": float(h["x"].attrs["scale_factor"][0]), "xoff": float(h["x"].attrs["add_offset"][0]),
            "yscale": float(h["y"].attrs["scale_factor"][0]), "yoff": float(h["y"].attrs["add_offset"][0])}
    h.close()
    # phase 0 is "no retrieval": clear sky, or a pixel the scheme could not process (off the disc, bad data).
    # Inside the disc, 0 with no COT is read as clear.
    clear = np.where(phase == 0, 1.0, 0.0).astype(np.float32)
    out = {"cod": np.where(seen, tau, np.where(phase == 0, 0.0, np.nan)).astype(np.float32),
           "cth": np.where(seen, cth, np.nan).astype(np.float32),
           "clear": clear,
           "ice": np.where(phase > 0, ((phase == 2) | (phase == 3)).astype(np.float32), np.nan).astype(np.float32)}
    out["geom"] = {k: ("fci", pack) for k in ("cod", "cth", "clear", "ice")}
    return out, len(raw)


def iodc_products(t: datetime, workdir: Path | None = None) -> tuple[dict, int] | None:
    """EUMETSAT's Meteosat-9 (IODC) cloud-top height (GRIB, 9 km, 320 m steps) and cloud mask (GRIB, 3 km:
    0 clear over water, 1 clear over land, 2 cloud, 3 no data), read by satpy's seviri_l2_grib. No optical
    thickness exists for the Indian Ocean service; `cod` is absent and fetch_clouds keeps its own opacity."""
    import warnings
    warnings.filterwarnings("ignore")
    from satpy import Scene
    got, n = {}, 0
    d = Path(tempfile.mkdtemp(prefix="iodc_", dir=str(workdir) if workdir else None))
    try:
        for col, name, key in ((IODC_CTH, "cloud_top_height", "cth"), (IODC_CLM, "cloud_mask", "clm")):
            feats = [f for f in eum_search(col, t - timedelta(minutes=1), t + timedelta(minutes=14))
                     if 0 <= (eum_product_start(f) - t).total_seconds() < 900]
            if not feats:
                continue
            ents = eum_entries(feats[0], r"\.grb$")
            if not ents:
                continue
            dest = eum_download(ents[0]["href"], d / ents[0]["title"])
            n += dest.stat().st_size
            sc = Scene(filenames=[str(dest)], reader="seviri_l2_grib")
            sc.load([name])
            got[key] = (sc[name].values.astype(np.float32), sc[name].attrs["area"])
    finally:
        for p in d.glob("*"):
            p.unlink()
        d.rmdir()
    if "clm" not in got:
        return None
    clm, clm_area = got["clm"]
    known = np.isfinite(clm) & (clm != 3)
    out = {"clear": np.where(known, (clm <= 1).astype(np.float32), np.nan).astype(np.float32)}
    geom = {"clear": ("area", clm_area)}
    if "cth" in got:
        cth, cth_area = got["cth"]
        out["cth"] = np.where(np.isfinite(cth) & (cth > 0), cth, np.nan).astype(np.float32)
        geom["cth"] = ("area", cth_area)
    out["geom"] = geom
    return out, n
