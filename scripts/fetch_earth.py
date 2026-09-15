"""The whole Earth every ten minutes: five geostationary satellites on one equirectangular grid.

PRIOR ART: scripts/fetch_goes.py pulls ONE place from ONE satellite in epsg3857 and keeps the
tile crop; scripts/goes_qc.py holds the white-wedge test. Neither stitches disks, reprojects
to a globe, or blends overlaps -- this does, on GIBS's epsg4326 grid plus EUMETSAT's open WMS.

    python scripts/fetch_earth.py --at 2026-09-14T18:00Z --debug          # one instant, per-source pngs
    python scripts/fetch_earth.py --start 2026-09-12T00:00Z --days 3      # 432 mosaics into data/earth
    python scripts/fetch_earth.py --seams site/earth/seams.png            # static zone map
    python scripts/fetch_earth.py --encode site/earth --width 2048        # mp4 + manifest for the page
    python scripts/fetch_earth.py --smooth site/earth --width 3072 --fps 48 --crf 28   # RIFE 4x version + manifest entry

Sources, all free and keyless (measured 15 Sep 2026):
  GOES-East  75.2W   GeoColor      NASA GIBS, epsg4326, 10-min, day and night     goes_east
  GOES-West 137.0W   GeoColor      NASA GIBS                                       goes_west
  Himawari-9 140.7E  Band 13 IR    NASA GIBS -- GIBS has NO Himawari GeoColor       himawari
  MTG-I1      0.0    GeoColour     EUMETView WMS (view.eumetsat.int), 10-min       mtg
  Meteosat-9 45.5E   IR 10.8 um    EUMETView WMS, 15-min (nearest slot is used)    iodc

Two kinds of picture. GeoColor is the satellite's own true colour by day and an infrared
rendering over city lights by night. Where there is no GeoColor (Himawari, Meteosat-9) the
clouds are PAINTED: cloud-top temperature from the infrared band becomes an opacity and is
laid over the Blue Marble basemap, lit by the computed sun and dark with VIIRS city lights by
night, so the two kinds sit next to each other without a hard edge in brightness. The
infrared kind cannot see warm low cloud (marine stratocumulus, fog) and shows cold ground
(Tibet, Antarctica, winter Siberia) as if it were cloud. The seams map says which is which.

Blend: each satellite weighs a pixel by how steeply it looks at it: w = (cos z - cos 70) /
(1 - cos 70) clipped at 0, where z is the satellite zenith angle, so a pixel seen at nadir
weighs 1, at 70 degrees off nadir 0. The mosaic is the weighted mean. Where no satellite
weighs anything (poleward of about 62 degrees at the sub-satellite longitude, more in the
gaps between disks) the basemap shows through, dimmed, and the page says so.

Broken renders: GIBS answers 200 for a partly failed tile with the missing swath filled
flat: pure white (scripts/goes_qc.py) or, in GeoColor, exactly RGB (140,191,250); a Band 13
tile's wedge is pure white, which the palette reads as -92 C and paints as the brightest
cloud. Measured 2026-09-15 over whole disks: no clean GeoColor tile has any bright colour on
more than 1.2% of its pixels and no clean Band 13 tile has ANY pure white, while the bad
tiles read 5-70%. So any such colour covering more than WEDGE_SHARE of a tile's used area
is masked out pixel by pixel (dilated 2 px for the antialiased edge) rather than the tile
dropped whole. Whatever a satellite is missing at an instant -- a masked wedge, a tile GIBS
did not answer, a WMS slot with transparent rows, a whole housekeeping gap -- is filled
from that satellite's previous picture for up to HOLD_MIN, so a miss reads as ten minutes
of stale cloud in one place, not a dark rectangle. Meteosat-9's 15-minute slots are
time-interpolated (in grey, before painting) so its zone moves every ten minutes like the
others. earth_qc.py measures what is left.

Writes data/earth/<UTC>.jpg (4096x2048, ~1 MB) and earth.jsonl, one row per mosaic with the
share of each satellite's zone that actually carried data (`cover`), the share filled from
its previous picture (`filled`), and the satellites whose whole picture was reused (`held`).
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

UA = {"User-Agent": "earthai-earth/0.1 (research; github.com/JDerekLomas/earthai)"}
GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/{layer}/default/{t}/{tms}/3/{y}/{x}.{ext}"
WMS = ("https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
       "&layers={layer}&crs=EPSG:4326&bbox={s},{w},{n},{e}&width={W}&height={H}"
       "&format={fmt}&transparent=true&time={t}")
CMAP = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/Clean_Longwave_Infrared_Window_Band.xml"
# GIBS geographic level 3: 10 x 5 tiles of 512 px, each 36 degrees, 5120x2560 for the globe
TILE, COLS, ROWS = 512, 10, 5
CUT_DEG = 70.0             # satellite zenith angle past which a source weighs nothing
R_EARTH, R_GEO = 6378.137, 42164.0

SATS = {
    "goes_west": dict(lon=-137.0, kind="geocolor", src="gibs", layer="GOES-West_ABI_GeoColor", tms="1km", label="GOES-West"),
    "goes_east": dict(lon=-75.2, kind="geocolor", src="gibs", layer="GOES-East_ABI_GeoColor", tms="1km", label="GOES-East"),
    "mtg": dict(lon=0.0, kind="geocolor", src="wms", layer="mtg_fd:rgb_geocolour", step=10, label="Meteosat (MTG-I1)"),
    "iodc": dict(lon=45.5, kind="ir_grey", src="wms", layer="msg_iodc:ir108", step=15, label="Meteosat-9 (IODC)"),
    "himawari": dict(lon=140.7, kind="ir_palette", src="gibs", layer="Himawari_AHI_Band13_Clean_Infrared", tms="2km", label="Himawari-9"),
}
# seams-map tint per satellite, chosen to read apart on a globe
TINT = {"goes_west": (55, 120, 200), "goes_east": (220, 140, 50), "mtg": (70, 170, 110),
        "iodc": (170, 90, 190), "himawari": (210, 70, 70)}

# infrared -> cloud opacity. Himawari comes as the GIBS palette (decoded to degrees C);
# Meteosat-9 ir108 as an 8-bit grey. Calibrated 2026-09-14 18:00Z on their overlap
# (72-108E, +-18 lat, n=262k): T = 18.3 - 0.234 L, resid 12.6 C, so 15 C is L~20 and
# -45 C would be L~270 -- the grey style saturates near 230. Both map warm->0, cold->1.
T_WARM, T_OPAQUE, T_BRIGHT = 15.0, -25.0, -80.0     # opacity 0 -> 1 over WARM..OPAQUE; brightness keeps rising to BRIGHT
L_TO_T = (18.3, -0.234)                                  # Meteosat-9 grey -> degrees C, from the overlap fit
HOLD_MIN = 40   # a satellite that misses a slot keeps its previous picture for up to this long (Himawari skips 02:40Z daily for housekeeping)
FILL_RGB = (140, 191, 250)   # GIBS's flat fill for an unrendered swath of a GeoColor tile (2026-09-13 09:30Z GOES-West (2,1): 21% of the tile, this exact colour)
WEDGE_SHARE = 0.02           # a fill colour on more than this share of a tile's used area is a wedge (clean tiles: <= 1.2% GeoColor, 0.0 Band 13)
FULL_SHARE = 0.97            # a source whose raw picture covers at least this much of its zone counts as complete (resets the hold clock)
HOLE_PX = 30    # palette collision: -80..-70 C tops decode as warm ground; enclosed warm specks up to this many px inside cold cloud are refilled
CLOUD_DAY = np.array([240, 242, 246], np.float32)
CLOUD_NIGHT = np.array([132, 142, 160], np.float32)
NIGHT_TINT = np.array([5, 8, 18], np.float32)


# ---------------------------------------------------------------- geometry
def grid(W: int, H: int):
    lon = (-180 + (np.arange(W, dtype=np.float32) + 0.5) * 360 / W)[None, :]
    lat = (90 - (np.arange(H, dtype=np.float32) + 0.5) * 180 / H)[:, None]
    return lat, lon


def weight_map(lat, lon, sat_lon: float) -> np.ndarray:
    """(cos zenith - cos CUT) / (1 - cos CUT), clipped at 0: 1 at nadir, 0 at CUT degrees off."""
    la, dl = np.radians(lat), np.radians(lon - sat_lon)
    cosg = np.cos(la) * np.cos(dl)                                   # central angle
    d = np.sqrt(R_EARTH ** 2 + R_GEO ** 2 - 2 * R_EARTH * R_GEO * cosg)
    cosz = (R_GEO * cosg - R_EARTH) / d                              # cos of satellite zenith
    c0 = np.cos(np.radians(CUT_DEG))
    return np.clip((cosz - c0) / (1 - c0), 0, 1).astype(np.float32)


def sun(t: datetime):
    """Subsolar latitude and longitude, degrees. Spencer-style declination and a three-term
    equation of time: within ~0.3 deg, which on a 0.09 deg grid is a few pixels of terminator."""
    n = t.timetuple().tm_yday - 1 + (t.hour + t.minute / 60) / 24
    g = 2 * np.pi * n / 365.25
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    b = 2 * np.pi * (n - 81) / 364
    eot_min = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)
    lon = -15 * (t.hour + t.minute / 60 + eot_min / 60 - 12)
    return float(np.degrees(decl)), float((lon + 180) % 360 - 180)


def sun_elevation(lat, lon, t: datetime) -> np.ndarray:
    decl, slon = sun(t)
    la, de = np.radians(lat), np.radians(decl)
    s = np.sin(la) * np.sin(de) + np.cos(la) * np.cos(de) * np.cos(np.radians(lon - slon))
    return np.degrees(np.arcsin(np.clip(s, -1, 1))).astype(np.float32)


# ---------------------------------------------------------------- fetching
def get(url: str, timeout=60, tries=2):
    for k in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200 and r.content[:2] in (b"\x89P", b"\xff\xd8"):
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1 + 2 * k)
    return None


def wedge_mask(a: np.ndarray, used: np.ndarray, kind: str) -> np.ndarray | None:
    """Pixels of a tile that are a render wedge: a flat fill colour covering more than
    WEDGE_SHARE of the tile's used area. GeoColor wedges are pure white or FILL_RGB; Band 13
    wedges are pure white (its real -92 C white never occurs: 0.0 in every clean tile
    measured). Judged only where the blend uses the tile: the limb of a GeoColor disk
    saturates to white legitimately, and GOES-West's (0, 4) tile at 18:00Z reads 17% white,
    all of it past the 70-degree cut. Dilated 2 px for the antialiased edge."""
    from scipy.ndimage import binary_dilation
    vis = a[..., 3] > 0
    n = float((used & vis).sum())
    if n < 2000:
        return None
    cands = [a[..., :3].min(-1) >= 253]
    if kind == "geocolor":
        cands.append((a[..., 0] == FILL_RGB[0]) & (a[..., 1] == FILL_RGB[1]) & (a[..., 2] == FILL_RGB[2]))
    bad = None
    for c in cands:
        c = c & vis
        if float((c & used).sum()) / n > WEDGE_SHARE:
            bad = c if bad is None else (bad | c)
    if bad is None:
        return None
    return binary_dilation(bad, iterations=2)


def gibs_tiles(layer: str, tms: str, t: str, tiles, ext="png", judge=None, kind="geocolor", workers=16) -> Image.Image:
    """A 5120x2560 RGBA canvas with the requested (x, y) tiles pasted; the rest transparent.
    judge: {(x, y): bool 512x512} -- the pixels of each tile the blend will actually use;
    render wedges there (wedge_mask) are made transparent so the mosaic fills them from the
    satellite's previous picture instead of showing a flat blue or white block."""
    def one(xy):
        x, y = xy
        raw = get(GIBS.format(layer=layer, tms=tms, t=t, y=y, x=x, ext=ext))
        if raw is None:
            return xy, None
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        if judge is not None:
            a = np.asarray(im)
            bad = wedge_mask(a, judge[xy], kind)
            if bad is not None:
                a = a.copy(); a[bad, 3] = 0
                im = Image.fromarray(a)
        return xy, im
    canvas = Image.new("RGBA", (TILE * COLS, TILE * ROWS), (0, 0, 0, 0))
    with ThreadPoolExecutor(workers) as ex:
        for (x, y), im in ex.map(one, tiles):
            if im is not None:
                canvas.paste(im, (x * TILE, y * TILE))
    return canvas


def wms_image(layer: str, t: str, bbox, size, fmt="image/png") -> Image.Image | None:
    s, w, n, e = bbox
    raw = get(WMS.format(layer=layer, s=s, w=w, n=n, e=e, W=size[0], H=size[1], fmt=fmt, t=t), timeout=180)
    if raw is None:
        return None
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    a = np.asarray(im)
    # a slot with no data comes back blank: fully transparent, or (jpeg) a flat field
    if (a[..., 3] > 0).mean() < 0.2 or a[..., :3][a[..., 3] > 0].std() < 4:
        return None
    return im


class IRPalette:
    """Nearest-colour lookup through a 6-bit RGB cube, built once from the GIBS colormap.
    Same construction as scripts/hrrr_agreement.py including the colliding cold grey ramp
    it drops (memory: earthai-hrrr-traps); copied rather than imported because that module
    pulls the HRRR grid machinery in at import time."""
    def __init__(self, xml_text: str):
        ents = re.findall(r'rgb="(\d+),(\d+),(\d+)"[^>]*sourceValue="\(([-\d.]+),([-\d.]+)\]"', xml_text)
        rgb = np.array([[int(r), int(g), int(b)] for r, g, b, _, _ in ents], np.int16)
        temp = np.array([(float(a) + float(b)) / 2 for *_, a, b in ents], np.float32)
        grey = (rgb[:, 0] == rgb[:, 1]) & (rgb[:, 1] == rgb[:, 2])
        keep = ~(grey & (temp < -20) & (temp > -85))
        self.rgb, self.temp = rgb[keep], temp[keep]
        q = np.arange(64) * 4 + 2
        cube = np.stack(np.meshgrid(q, q, q, indexing="ij"), -1).reshape(-1, 3).astype(np.int16)
        idx = np.empty(len(cube), np.int32)
        for s in range(0, len(cube), 16384):
            idx[s:s + 16384] = np.abs(cube[s:s + 16384, None, :] - self.rgb[None]).sum(-1).argmin(1)
        self.lut = self.temp[idx].reshape(64, 64, 64)
        worst = max(float(np.abs(self(np.clip(self.rgb + d, 0, 255).astype(np.uint8)) - self.temp).max()) for d in (-2, 0, 2))
        if worst > 2.51:
            raise RuntimeError(f"palette inversion is ambiguous: {worst:.1f} C error under +-2 noise")

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        return self.lut[rgb[..., 0] >> 2, rgb[..., 1] >> 2, rgb[..., 2] >> 2]


def fill_holes(temp: np.ndarray) -> np.ndarray:
    """The GIBS palette's -80..-70 C greys collide with its warm greys and are dropped by the
    decoder, so the coldest core of a storm reads as warm ground: a dark pinhole in a bright
    anvil. Warm specks of at most HOLE_PX pixels fully enclosed by cloud colder than -55 C
    are set to -75 C. Measured 2026-09-14 18:00Z, Himawari zone: 0.15% of pixels were warm
    within 2 px of such cloud, most of them real edges, which the enclosure test excludes."""
    from scipy.ndimage import binary_fill_holes, label
    cold = temp < -55
    holes = binary_fill_holes(cold) & ~cold
    if not holes.any():
        return temp
    lab, n = label(holes)
    sizes = np.bincount(lab.ravel())
    small = (sizes <= HOLE_PX); small[0] = False
    out = temp.copy(); out[small[lab]] = -75.0
    return out


# ---------------------------------------------------------------- the mosaic
class Earth:
    def __init__(self, out: Path, W: int = 4096, workers: int = 16):
        self.out, self.W, self.H, self.workers = out, W, W // 2, workers
        out.mkdir(parents=True, exist_ok=True)
        self.lat, self.lon = grid(self.W, self.H)
        self.w = {k: weight_map(self.lat, self.lon, s["lon"]) for k, s in SATS.items()}
        # which level-3 tiles a satellite can contribute to at all (weight > 0 somewhere in it)
        self.tiles, self.judge = {}, {}
        for k, w in self.w.items():
            ys, xs = np.nonzero(w > 0)
            tx = (xs * COLS // self.W).astype(int); ty = (ys * ROWS // self.H).astype(int)
            self.tiles[k] = sorted(set(zip(tx.tolist(), ty.tolist())))
            self.judge[k] = {}
            for (x, y) in self.tiles[k]:
                tlon = (-180 + (x * TILE + np.arange(TILE, dtype=np.float32) + 0.5) * 360 / (TILE * COLS))[None, :]
                tlat = (90 - (y * TILE + np.arange(TILE, dtype=np.float32) + 0.5) * 180 / (TILE * ROWS))[:, None]
                self.judge[k][(x, y)] = weight_map(tlat, tlon, SATS[k]["lon"]) > 0
        self.base = self._static("BlueMarble_ShadedRelief_Bathymetry", "500m", "jpeg", "_base.jpg")
        self.lights = self._static("VIIRS_CityLights_2012", "500m", "jpeg", "_lights.jpg")
        cm = out / "_band13_colormap.xml"
        if not cm.exists():
            cm.write_text(requests.get(CMAP, headers=UA, timeout=60).text)
        self.pal = IRPalette(cm.read_text())
        self.last = {}          # sat -> (t_full, rgb, weight): its most recent picture, merged; t_full = when it was last complete
        self.slots = {}         # (sat, slot time) -> WMS canvas, for the 15-minute interpolation
        self.timing = {}

    def _static(self, layer, tms, ext, name) -> np.ndarray:
        p = self.out / name
        if not p.exists():
            tiles = [(x, y) for y in range(ROWS) for x in range(COLS)]
            im = gibs_tiles(layer, tms, "default", tiles, ext=ext, workers=self.workers)
            im.convert("RGB").resize((self.W, self.H), Image.LANCZOS).save(p, quality=92)
        return np.asarray(Image.open(p).convert("RGB")).astype(np.float32)

    # -- one satellite at one instant -> (rgb float32 HxWx3, weight HxW) or None
    def source(self, k: str, t: datetime):
        t0 = time.time(); r = self._source(k, t); self.timing[k] = round(time.time() - t0, 1)
        return r

    def _source(self, k: str, t: datetime):
        s = SATS[k]
        if s["src"] == "gibs":
            ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            im = gibs_tiles(s["layer"], s["tms"], ts, self.tiles[k], judge=self.judge[k], kind=s["kind"], workers=self.workers)
            im = im.resize((self.W, self.H), Image.BILINEAR)
            a = np.asarray(im)
            alpha = a[..., 3].astype(np.float32) / 255
            rgb = a[..., :3].astype(np.float32)
        else:
            step = s["step"]
            t0 = t - timedelta(minutes=t.minute % step, seconds=t.second)
            f = (t - t0).total_seconds() / (step * 60)
            slots = [(t0, 1 - f)] + ([(t0 + timedelta(minutes=step), f)] if f > 1e-6 else [])
            got = [(self.wms_slot(k, tt), wt) for tt, wt in slots]
            share = [float((a[..., 3] > 0).mean()) if a is not None else 0.0 for a, _ in got]
            # a slot that came back with transparent rows (2026-09-13 08:45Z: 24% missing) is
            # dropped when its partner is whole: blending it in leaves its ragged edge as a
            # dotted line across the zone, and the previous picture fills the rest
            got = [(a, wt) for (a, wt), sh in zip(got, share) if a is not None and sh >= FULL_SHARE * max(share)]
            acc = np.zeros((self.H, self.W, 3), np.float32); wa = np.zeros((self.H, self.W), np.float32)
            for a, wt in got:
                al = (a[..., 3].astype(np.float32) / 255) * wt
                acc += a[..., :3].astype(np.float32) * al[..., None]; wa += al
            if not wa.any():
                return None
            rgb = acc / np.maximum(wa, 1e-6)[..., None]
            alpha = (wa > 0).astype(np.float32)
        if s["kind"] == "geocolor":
            # GeoColor's night side is black ocean under grey cloud (GOES) or navy (MTG); the
            # painted zones are navy with the basemap faintly through. Lift the GeoColor night
            # floor to the same navy so the seams do not step in brightness after dark. Scaled
            # by darkness so clouds and city lights are untouched.
            _, f = self.ground(t)
            dark = np.clip(1 - rgb.max(-1, keepdims=True) / 90, 0, 1)
            rgb = rgb + (1 - f) * dark * (self.base * 0.08 + NIGHT_TINT)
            return rgb, self.w[k] * alpha
        if s["kind"] == "ir_palette":
            temp = fill_holes(self.pal(a[..., :3]))
        else:
            temp = L_TO_T[0] + L_TO_T[1] * rgb.mean(-1)
        return self.paint(temp, t), self.w[k] * alpha

    def wms_slot(self, k: str, tt: datetime):
        """One WMS slot as an HxWx4 uint8 canvas, cached (a 15-minute Meteosat-9 slot serves
        two or three mosaics; the cache keeps the last few)."""
        s = SATS[k]
        key = (k, tt)
        if key in self.slots:
            return self.slots[key]
        ts = tt.strftime("%Y-%m-%dT%H:%M:%SZ")
        w, e = s["lon"] - CUT_DEG, s["lon"] + CUT_DEG           # generous: weight is 0 outside anyway
        x0, x1 = int((w + 180) / 360 * self.W), int((e + 180) / 360 * self.W)
        y0, y1 = int((90 - CUT_DEG) / 180 * self.H), int((90 + CUT_DEG) / 180 * self.H)
        fmt = "image/jpeg" if s["kind"] == "geocolor" else "image/png"
        im = wms_image(s["layer"], ts, (-CUT_DEG, w, CUT_DEG, e), (x1 - x0, y1 - y0), fmt)
        a = None
        if im is not None:
            a = np.zeros((self.H, self.W, 4), np.uint8)
            a[y0:y1, x0:x1] = np.asarray(im)
        self.slots[key] = a
        for old in list(self.slots)[:-6]:
            del self.slots[old]
        return a

    def ground(self, t: datetime):
        """Basemap lit by the computed sun: Blue Marble by day, a dark navy with city lights by night."""
        f = np.clip((sun_elevation(self.lat, self.lon, t) + 6) / 12, 0, 1)[..., None]
        night = self.base * 0.08 + NIGHT_TINT + self.lights * 0.85
        return f * self.base + (1 - f) * night, f

    def paint(self, temp: np.ndarray, t: datetime) -> np.ndarray:
        """Cloud-top temperature -> a cloud over the lit basemap. Opacity rises from T_WARM to
        T_OPAQUE; past that the cloud keeps getting brighter to T_BRIGHT, so cold anvils keep
        their texture instead of clipping to one flat grey."""
        g, f = self.ground(t)
        c = (np.clip((T_WARM - temp) / (T_WARM - T_OPAQUE), 0, 1) ** 2)[..., None]   # squared: a warm sea a few degrees under T_WARM is haze, not cloud
        b = (0.78 + 0.22 * np.clip((T_OPAQUE - temp) / (T_OPAQUE - T_BRIGHT), 0, 1))[..., None]
        col = (f * CLOUD_DAY + (1 - f) * CLOUD_NIGHT) * b
        return g * (1 - c) + col * c

    def mosaic(self, t: datetime, debug: Path | None = None):
        t0 = time.time()
        with ThreadPoolExecutor(len(SATS)) as ex:
            got = dict(zip(SATS, ex.map(lambda k: self.source(k, t), SATS)))
        acc = np.zeros((self.H, self.W, 3), np.float32)
        wsum = np.zeros((self.H, self.W), np.float32)
        cover, filled, held = {}, {}, []
        for k, r in got.items():
            zone = self.w[k] > 0
            if r is None:
                rgb, w = np.zeros((self.H, self.W, 3), np.float32), np.zeros((self.H, self.W), np.float32)
            else:
                rgb, w = r
            raw = float((w[zone] > 0).mean())
            cover[k] = round(raw, 3)
            # whatever this picture lacks in its zone -- a masked wedge, an unanswered tile, a
            # transparent WMS row, a whole missed slot -- comes from the previous picture while
            # that is recent enough; a miss then reads as stale cloud, not a dark rectangle
            prev = self.last.get(k)
            if prev and (t - prev[0]).total_seconds() <= HOLD_MIN * 60 and raw < 1:
                gap = (w == 0) & (prev[2] > 0)
                if gap.any():
                    rgb = np.where(gap[..., None], prev[1], rgb); w = np.where(gap, prev[2], w)
                    filled[k] = round(float(gap[zone].mean()), 3)
            if raw < 0.5:
                held.append(k)
            t_full = t if raw >= FULL_SHARE else (prev[0] if prev else None)
            if t_full is not None:
                self.last[k] = (t_full, rgb, w)
            elif prev:
                self.last[k] = prev
            if not (w > 0).any():
                continue
            acc += rgb * w[..., None]
            wsum += w
            if debug:
                Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize((self.W // 4, self.H // 4)).save(debug / f"{k}.jpg", quality=85)
        g, _ = self.ground(t)
        gap = np.clip(1 - wsum, 0, 1)[..., None]                    # fades in where coverage runs out
        out = (acc + g * 0.45 * gap) / np.maximum(wsum, 1)[..., None]
        return np.clip(out, 0, 255).astype(np.uint8), cover, filled, held, time.time() - t0

    def seams(self, path: Path, W: int = 2048):
        H = W // 2
        acc = np.zeros((H, W, 3), np.float32); wsum = np.zeros((H, W), np.float32)
        ys = np.linspace(0, self.H - 1, H).astype(int); xs = np.linspace(0, self.W - 1, W).astype(int)
        for k, w in self.w.items():
            ws = w[ys][:, xs]
            acc += ws[..., None] * np.array(TINT[k], np.float32); wsum += ws
        rgb = acc / np.maximum(wsum, 1e-6)[..., None]
        alpha = np.where(wsum > 0, 150, 0).astype(np.uint8)
        Image.fromarray(np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])).save(path, optimize=True)
        return path


def stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H%MZ")


def parse_t(s: str) -> datetime:
    s = s.replace("Z", "").replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise click.BadParameter(s)


def encode(src: Path, dest: Path, width: int, fps: int, crf: int):
    """Every mosaic on disk, in order, into one mp4 and a manifest that maps frame -> UTC."""
    frames = sorted(src.glob("????-??-??T????Z.jpg"))
    if not frames:
        raise SystemExit(f"no mosaics in {src}")
    dest.mkdir(parents=True, exist_ok=True)
    lst = dest / "_frames.txt"
    lst.write_text("".join(f"file '{f.resolve()}'\nduration {1 / fps:.6f}\n" for f in frames))
    mp4 = dest / "earth.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", f"scale={width}:{width // 2}:flags=lanczos:out_range=tv", "-r", str(fps),
                    "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(mp4)], check=True)
    lst.unlink()
    times = [datetime.strptime(f.stem, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc) for f in frames]
    rows = {}
    if (src / "earth.jsonl").exists():
        for line in (src / "earth.jsonl").read_text().splitlines():
            r = json.loads(line); rows[r["id"]] = r
    holds = {f.stem: rows[f.stem].get("held", []) for f in frames if f.stem in rows and rows[f.stem].get("held")}
    misses = {f.stem: [k for k, v in rows[f.stem]["cover"].items() if v == 0] for f in frames if f.stem in rows}
    misses = {k: v for k, v in misses.items() if v}
    filled = {f.stem: rows[f.stem]["filled"] for f in frames if f.stem in rows and any(v >= 0.05 for v in rows[f.stem].get("filled", {}).values())}
    removed = {}
    if (src / "_qc_rejected" / "qc.jsonl").exists():          # frames earth_qc.py quarantined; the clip steps over them
        for line in (src / "_qc_rejected" / "qc.jsonl").read_text().splitlines():
            r = json.loads(line); removed[r["file"][:-4]] = r["reason"]
    man = dict(holds=holds, misses=misses, filled=filled, removed=removed, fps=fps, width=width, height=width // 2, frames=len(frames), crf=crf,
               start=times[0].strftime("%Y-%m-%dT%H:%MZ"), end=times[-1].strftime("%Y-%m-%dT%H:%MZ"),
               bytes=mp4.stat().st_size,
               sources=[dict(id=k, label=s["label"], lon=s["lon"], kind=s["kind"]) for k, s in SATS.items()],
               t=[dict(t=t.strftime("%Y-%m-%dT%H:%MZ"), sun=round(sun(t)[1], 2)) for t in times])
    (dest / "earth.json").write_text(json.dumps(man, separators=(",", ":")))
    click.echo(f"{mp4}: {len(frames)} frames, {mp4.stat().st_size / 1e6:.1f} MB, {times[0]:%Y-%m-%d %H:%M} -> {times[-1]:%Y-%m-%d %H:%M}")


def smooth(src: Path, dest: Path, width: int, fps: int, crf: int, factor: int = 4):
    """The same mosaics with RIFE 4.6 morphing `factor` frames between each real pair (through
    scripts/interpolate.py's chunked rife_encode), as <dest>/earth_smooth.mp4, recorded in
    earth.json under `smooth`. The in-between frames are learned, not observed. Frames are
    scaled to `width` first: RIFE at the mosaic's own 4096 px is slower and gains nothing the
    encode keeps. Measured 2026-09-15: 1.3 frames/s at 3072x1536 on the Mac's GPU."""
    import sys
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, str(Path(__file__).parent))
    from interpolate import rife_encode
    frames = sorted(src.glob("????-??-??T????Z.jpg"))
    if len(frames) < 2:
        raise SystemExit(f"no mosaics in {src}")
    dest.mkdir(parents=True, exist_ok=True)
    mp4 = dest / "earth_smooth.mp4"
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=dest) as td:
        def scale(f):
            q = Path(td) / (f.stem[:-1] + "00Z.jpg")        # rife_encode's stamp wants seconds
            Image.open(f).resize((width, width // 2), Image.LANCZOS).save(q, quality=94)
            return q
        with ThreadPoolExecutor(8) as ex:
            small = list(ex.map(scale, frames))
        click.echo(f"scaled {len(small)} frames to {width} in {time.time() - t0:.0f} s; running RIFE x{factor}")
        r = rife_encode(small, mp4, factor, fps, width, crf)
    if not r:
        raise SystemExit("RIFE failed")
    man_p = dest / "earth.json"
    man = json.loads(man_p.read_text()) if man_p.exists() else {}
    man["smooth"] = dict(file=mp4.name, fps=fps, factor=factor, frames=r["frames"], real_frames=r["real_frames"],
                         width=width, height=width // 2, crf=crf, bytes=mp4.stat().st_size, model="rife-v4.6")
    man_p.write_text(json.dumps(man, separators=(",", ":")))
    click.echo(f"{mp4}: {r['real_frames']} real -> {r['frames']} frames, {mp4.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f} s")


@click.command()
@click.option("--at", default=None, help="one instant, e.g. 2026-09-14T18:00Z")
@click.option("--start", default=None, help="first instant of a run (UTC)")
@click.option("--days", default=3.0, type=float, help="length of the run from --start")
@click.option("--stride", default=10, type=int, help="minutes between mosaics (10 is the native cadence)")
@click.option("--out", default="data/earth", type=click.Path(path_type=Path))
@click.option("--width", default=4096, type=int, help="mosaic width; height is half")
@click.option("--workers", default=16, type=int)
@click.option("--warm", default=0, type=int, help="fetch this many frames before --start first (not written) so the first frames can fill gaps from a previous picture")
@click.option("--debug", is_flag=True, help="also write each satellite's own contribution")
@click.option("--seams", default=None, type=click.Path(path_type=Path), help="write the static zone map here and stop")
@click.option("--encode", "encode_dir", default=None, type=click.Path(path_type=Path), help="encode data/earth into <dir>/earth.mp4 + earth.json and stop")
@click.option("--smooth", "smooth_dir", default=None, type=click.Path(path_type=Path), help="RIFE-interpolate data/earth into <dir>/earth_smooth.mp4 and stop")
@click.option("--fps", default=24, type=int)
@click.option("--crf", default=30, type=int)
def main(at, start, days, stride, out, width, workers, warm, debug, seams, encode_dir, smooth_dir, fps, crf):
    if encode_dir:
        return encode(out, encode_dir, width, fps, crf)
    if smooth_dir:
        return smooth(out, smooth_dir, width, fps, crf)
    earth = Earth(out, width, workers)
    if seams:
        click.echo(f"wrote {earth.seams(seams)}")
        return
    if at:
        times = [parse_t(at)]
    elif start:
        t0 = parse_t(start)
        times = [t0 + timedelta(minutes=stride * k) for k in range(int(days * 24 * 60 / stride))]
    else:
        raise SystemExit("give --at, --start, --seams or --encode")
    dbg = None
    if debug:
        dbg = out / "debug"; dbg.mkdir(exist_ok=True)
    log = open(out / "earth.jsonl", "a")
    done = 0
    for k in range(warm, 0, -1):
        tw = times[0] - timedelta(minutes=stride * k)
        earth.mosaic(tw)
        click.echo(f"{stamp(tw)}  warmed")
    for t in times:
        p = out / f"{stamp(t)}.jpg"
        if p.exists():
            continue
        img, cover, filled, held, secs = earth.mosaic(t, dbg)
        Image.fromarray(img).save(p, quality=88, subsampling=0)
        row = dict(id=stamp(t), t=t.strftime("%Y-%m-%dT%H:%MZ"), cover=cover, filled=filled, held=held, secs=round(secs, 1), bytes=p.stat().st_size)
        log.write(json.dumps(row) + "\n"); log.flush()
        done += 1
        click.echo(f"{stamp(t)}  {secs:5.1f}s  {p.stat().st_size / 1e6:.2f} MB  " + " ".join(f"{k}={v:.2f}" for k, v in cover.items())
                   + (f"  filled: " + ",".join(f"{k}={v:.2f}" for k, v in filled.items()) if filled else "")
                   + (f"  held: {','.join(held)}" if held else "") + "  fetch " + " ".join(f"{k[:3]}={v}" for k, v in earth.timing.items()))
    click.echo(f"{done} new mosaics in {out}")


if __name__ == "__main__":
    main()
