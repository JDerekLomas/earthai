"""Regional cloud tiles for the globe, so zooming in gets sharper instead of blurrier.

PRIOR ART: scripts/fetch_clouds.py — the same two GOES bands and the same clear-sky method, but
everything lands on ONE 4096-wide grid (10 km/px) and the satellite's native 2 km detail is
averaged away on the way in. This refetches the same bytes (~45 MB a slot by byte range) and keeps
them at native resolution on a tiled equirectangular pyramid, then cuts each tile into its own
H.264 elementary stream with a byte index, so the page can decode any frame of any tile with
WebCodecs (site/globe/tiles.js) instead of nine <video> elements that drift.

    python scripts/cloud_tiles.py fetch   --sat goes19 --start 2026-09-12T00:00 --end 2026-09-12T23:50
    python scripts/cloud_tiles.py opacity --sat goes19
    python scripts/cloud_tiles.py flow    --sat goes19
    python scripts/cloud_tiles.py encode  --sat goes19 --out site/globe/tiles
    python scripts/cloud_tiles.py sheet   --sat goes19 --lon -62 --lat 16 --span 10 --slot 2026-09-12T1700Z --name caribbean

Tile scheme ("geodetic" tiles): level L is the WHOLE sphere, 512*2^L px around and 256*2^L tall,
cut into 512 px tiles, 2^L across and 2^(L-1) down. The base clip is level 3 (4096 wide, 10 km);
this builds level 5 (16384 wide, 2.4 km at the equator: the native 2 km of the infrared band and
of the visible as MCMIPF delivers it) and level 4 (8192, 4.9 km) by 2x2 averaging. The +/-66 deg
crop of the base clip is not part of the scheme; a tile simply holds zeros where no satellite
sees. Only tiles a satellite sees at < 66 deg of zenith exist; a missing tile is clear. Level 6
(1.2 km) would need the 0.5 km L1b band 2 (ABI-L1b-RadF C02, ~150 MB a slot, daytime only) and
is not built.

fetch     Same byte-range reader as fetch_clouds (two variables of the MCMIPF file), reprojected
          at native resolution onto this satellite's WINDOW of the level-5 grid: the rectangle of
          level-5 tiles the disc touches, aligned to even tile indices so level-4 tiles nest. Kept
          as 16-bit PNGs under data/clouds/tiles/raw5/<sat>/ so the next day or level does not
          refetch. One GOES window is 7680 x 6144 (47 Mpx), about a third of the 16384 x 6016 field.
opacity   Clear-sky compositing and opacity exactly as fetch_clouds does it, but on the window and
          in ONE sliding pass: pass 1 finds the day's darkest sun-normalised reflectance and warmest
          temperature per pixel; pass 2 walks the slots with a +/-1 h ring of temperature frames in
          memory (13 x 188 MB) so the per-time-of-day clear reference is never stored. Output is a
          uint8 memmap (frames, rows, cols), 0..255 = opacity x view weight, 0 = clear or unseen.
flow      DIS optical flow (OpenCV) between consecutive opacity frames at a quarter of level 5,
          forward A->B, in level-5 texels, float16 memmap. The page warps with it exactly as the
          base clip's shader does (the flow rides in the chroma planes, centred on 128).
encode    Per level, per tile: raw yuv420p frames (Y = opacity, U/V = flow x/y) into libx264, no
          B-frames, a keyframe every 12 frames, an AUD before every access unit and SPS/PPS repeated
          at every keyframe (so a decoder can start at ANY keyframe with no out-of-band config),
          written as an Annex B elementary stream. Then the stream is parsed for the byte offset of
          every access unit and which are keyframes, into one JSON index per level.
sheet     A contact sheet of one place at levels 3 / 4 / 5 side by side, into docs/, so the gain
          is visible before the page is.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import click
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from fetch_clouds import (LAT_MAX, PARAMS, SATS, ZEN_FULL, ZEN_ZERO, DIURNAL_K, DIURNAL_LAND_K, abi_read,  # noqa: E402
                          glint_cos, load16, parse_slot, sat_zenith, save16, slot_name, utc, VIS_SCALE, BT_SCALE)
from fetch_goes_aws import cos_solar_zenith, list_keys  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

TILE = 512
L5 = 5
W5, H5 = TILE << L5, TILE << (L5 - 1)          # 16384 x 8192, the whole sphere at level 5
DEG5 = 360.0 / W5                              # degrees per level-5 pixel (0.022 deg, 2.4 km at the equator)
TROOT = Path("data/clouds/tiles")
FPS, GOP = 12, 12
FLOW_Q = 4                                     # flow is computed at level 5 / FLOW_Q


# ---------------------------------------------------------------- the window: which level-5 tiles a satellite touches

def window(sat: str) -> dict:
    """Tile rectangle [tx0, tx1) x [ty0, ty1) at level 5 covering everything this satellite sees
    inside +/- LAT_MAX, rounded out to even tile indices so level-4 tiles nest exactly."""
    sub = SATS[sat]["lon"]
    step = 32
    lon = -180 + (np.arange(0, W5, step) + 0.5) * DEG5
    lat = 90 - (np.arange(0, H5, step) + 0.5) * DEG5
    lo, la = np.meshgrid(lon.astype(np.float32), lat.astype(np.float32))
    seen = (sat_zenith(lo, la, sub) < ZEN_ZERO + 1) & (np.abs(la) <= LAT_MAX)
    rows, cols = np.nonzero(seen)
    c0, c1 = cols.min() * step, (cols.max() + 1) * step
    r0, r1 = rows.min() * step, (rows.max() + 1) * step
    tx0, tx1 = (c0 // TILE) // 2 * 2, -(-c1 // TILE)
    ty0, ty1 = (r0 // TILE) // 2 * 2, -(-r1 // TILE)
    tx1 += tx1 % 2
    ty1 += ty1 % 2
    return {"tx0": int(tx0), "tx1": int(tx1), "ty0": int(ty0), "ty1": int(ty1),
            "c0": int(tx0 * TILE), "c1": int(tx1 * TILE), "r0": int(ty0 * TILE), "r1": int(ty1 * TILE)}


def win_lonlat(win: dict, step: int = 1) -> tuple[np.ndarray, np.ndarray]:
    lon = -180 + (np.arange(win["c0"], win["c1"], step) + 0.5 * step) * DEG5
    lat = 90 - (np.arange(win["r0"], win["r1"], step) + 0.5 * step) * DEG5
    return np.meshgrid(lon.astype(np.float32), lat.astype(np.float32))


def win_weight(sat: str, win: dict) -> np.ndarray:
    lon, lat = win_lonlat(win)
    z = sat_zenith(lon, lat, SATS[sat]["lon"])
    t = np.clip((ZEN_ZERO - z) / (ZEN_ZERO - ZEN_FULL), 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


class NativeGeometry:
    """Fractional native (row, col) in the ABI 2 km fixed grid of every window pixel: fetch_clouds'
    DiskGeometry without the 2x2 pooling, on the window instead of the 4096 grid."""

    def __init__(self, sub_lon: float, win: dict, scale: float = 5.6e-05, off: float = 0.151844):
        from pyproj import Transformer
        h = 35786023.0
        lon, lat = win_lonlat(win)
        geos = f"+proj=geos +h={h} +lon_0={sub_lon} +sweep=x +a=6378137 +b=6356752.31414 +units=m +no_defs"
        gx, gy = Transformer.from_crs("EPSG:4326", geos, always_xy=True).transform(lon.astype(np.float64), lat.astype(np.float64))
        ok = np.isfinite(gx) & np.isfinite(gy)
        ax, ay = np.where(ok, gx, 0) / h, np.where(ok, gy, 0) / h
        self.cols = ((ax + off) / scale).astype(np.float32)
        self.rows = ((off - ay) / scale).astype(np.float32)
        self.ok = ok & (sat_zenith(lon, lat, sub_lon) < ZEN_ZERO + 1)
        self.shape = lon.shape

    def sample(self, raw: np.ndarray) -> np.ndarray:
        """raw: (5424, 5424) float32 with NaN for fill -> the window, NaN where unseen. A light blur
        (sigma 0.6 native px) before bilinear sampling, because a level-5 pixel is ~1.2 native px."""
        from scipy.ndimage import gaussian_filter, map_coordinates
        good = np.isfinite(raw)
        wgt = gaussian_filter(good.astype(np.float32), 0.6)
        sm = gaussian_filter(np.where(good, raw, 0).astype(np.float32), 0.6) / np.maximum(wgt, 1e-3)
        sm[wgt < 0.5] = np.nan
        out = map_coordinates(sm, [self.rows.ravel(), self.cols.ravel()], order=1, mode="constant", cval=np.nan).reshape(self.shape)
        out[~self.ok] = np.nan
        return out


# ---------------------------------------------------------------- commands

@click.group()
def cli():
    pass


def have(out: Path, slot) -> bool:
    return (out / f"{slot_name(slot)}_vis.png").exists() and (out / f"{slot_name(slot)}_bt.png").exists()


@cli.command()
@click.option("--sat", required=True, type=click.Choice(["goes19", "goes18"]))
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--workers", default=8, show_default=True)
@click.option("--limit", default=0, show_default=True, help="stop after this many slots (a test)")
def fetch(sat, start, end, workers, limit):
    cfg = SATS[sat]
    win = window(sat)
    out = TROOT / "raw5" / sat
    out.mkdir(parents=True, exist_ok=True)
    (out / "window.json").write_text(json.dumps(win))
    log = open(TROOT / "fetch.jsonl", "a")
    keys = list_keys(cfg["bucket"], "ABI-L2-MCMIPF", utc(start), utc(end) + timedelta(minutes=9), None)
    todo = []
    for ts, key, size in keys:
        slot = ts.replace(minute=ts.minute // 10 * 10, second=0)
        if not have(out, slot):
            todo.append((slot, key, size))
    if limit:
        todo = todo[:limit]
    click.echo(f"{sat}: window tiles x {win['tx0']}..{win['tx1']} y {win['ty0']}..{win['ty1']} = {win['c1'] - win['c0']} x {win['r1'] - win['r0']} px; "
               f"{len(keys)} slots listed, {len(todo)} to fetch")
    if not todo:
        return
    geo = NativeGeometry(cfg["lon"], win)

    def one(item):
        slot, key, size = item
        url = f"https://{cfg['bucket']}.s3.amazonaws.com/{key}"
        for attempt in range(6):
            try:
                t0 = time.time()
                bands, nbytes = abi_read(url, ("CMI_C02", "CMI_C13"))
                t1 = time.time()
                save16(out / f"{slot_name(slot)}_vis.png", geo.sample(bands["CMI_C02"]), VIS_SCALE)
                save16(out / f"{slot_name(slot)}_bt.png", geo.sample(bands["CMI_C13"]), BT_SCALE)
                return slot, size, nbytes, t1 - t0, time.time() - t1
            except Exception as e:  # noqa: BLE001
                click.echo(f"   retry {attempt + 1} {slot_name(slot)}: {e}")
                time.sleep(10 * (attempt + 1))
        return slot, size, 0, 0.0, 0.0

    total, t_all = 0, time.time()
    with ThreadPoolExecutor(workers) as pool:
        for n, (slot, size, nbytes, dt, ds) in enumerate(pool.map(one, todo), 1):
            if not nbytes:
                click.echo(f"   FAILED {slot_name(slot)}")
                continue
            total += nbytes
            log.write(json.dumps({"sat": sat, "level": 5, "slot": slot_name(slot), "file_bytes": size, "fetched_bytes": nbytes,
                                  "fetch_s": round(dt, 1), "sample_s": round(ds, 1)}) + "\n")
            log.flush()
            click.echo(f"   {n}/{len(todo)} {slot_name(slot)}  {nbytes / 1e6:.1f} MB  fetch {dt:.0f}s sample {ds:.0f}s  "
                       f"({total / 1e9:.2f} GB, {(time.time() - t_all) / 60:.0f} min)")
    click.echo(f"fetched {total / 1e9:.2f} GB in {(time.time() - t_all) / 60:.0f} min")


def raw_slots(sat: str) -> list:
    return sorted(parse_slot(p.name) for p in (TROOT / "raw5" / sat).glob("*_bt.png"))


def base_window(win: dict) -> np.ndarray:
    """Blue Marble on the window, float32 sRGB 0..1, from the 8192 basemap (level 4) resized 2x."""
    im = Image.open("site/globe/base_8192.jpg").convert("RGB")
    crop = im.crop((win["c0"] // 2, win["r0"] // 2, win["c1"] // 2, win["r1"] // 2))
    crop = crop.resize((win["c1"] - win["c0"], win["r1"] - win["r0"]), Image.BILINEAR)
    return np.asarray(crop).astype(np.float32) / 255


@cli.command()
@click.option("--sat", required=True)
def opacity(sat):
    """Clear-sky reference and opacity on the window, one sliding pass (see the module docstring)."""
    win = json.loads((TROOT / "raw5" / sat / "window.json").read_text())
    raw = TROOT / "raw5" / sat
    slots = raw_slots(sat)
    n = len(slots)
    Hw, Ww = win["r1"] - win["r0"], win["c1"] - win["c0"]
    lon, lat = win_lonlat(win)
    click.echo(f"{sat}: {n} slots, window {Ww} x {Hw}")
    # pass 1: the day's extremes
    vmin = np.full((Hw, Ww), np.inf, np.float32)
    bt_all = np.full((Hw, Ww), -np.inf, np.float32)
    t0 = time.time()
    for i, t in enumerate(slots):
        mu = cos_solar_zenith(t, lon, lat).astype(np.float32)
        vis = load16(raw / f"{slot_name(t)}_vis.png", VIS_SCALE)
        rn = np.where((mu > 0.35) & np.isfinite(vis), vis / np.maximum(mu, 0.35), np.inf)
        np.minimum(vmin, rn, out=vmin)
        del vis, rn
        bt = np.nan_to_num(load16(raw / f"{slot_name(t)}_bt.png", BT_SCALE), nan=-np.inf)
        np.maximum(bt_all, bt, out=bt_all)
        del bt
        if i % 24 == 0:
            click.echo(f"   pass 1 {slot_name(t)}  {(time.time() - t0) / 60:.1f} min")
    vmin[~np.isfinite(vmin)] = np.nan
    base = base_window(win)
    r, b = base[..., 0], base[..., 2]
    water = (b > r * 1.25) & (r < 70 / 255)
    albedo = np.where(r <= 0.04045, r / 12.92, ((r + 0.055) / 1.055) ** 2.4).astype(np.float32)
    del base, r, b
    cap = np.maximum(albedo * 1.4 + 0.04, 0.09).astype(np.float32)
    vis_clear = np.where(np.isfinite(vmin), np.minimum(vmin, cap), cap)
    vis_clear = np.nan_to_num(vis_clear, nan=0.08)
    del vmin, cap, albedo
    floor = np.where(water, bt_all - DIURNAL_K, bt_all - DIURNAL_LAND_K).astype(np.float32)
    del bt_all
    (TROOT / "clear5").mkdir(parents=True, exist_ok=True)
    save16(TROOT / "clear5" / f"{sat}_vis_clear.png", vis_clear, VIS_SCALE)
    weight = win_weight(sat, win)
    # pass 2: per slot, the warmest within +/-1 h of its time of day (a ring of 13 frames), then opacity
    out_dir = TROOT / "op5"
    out_dir.mkdir(parents=True, exist_ok=True)
    op = np.lib.format.open_memmap(out_dir / f"{sat}.npy", mode="w+", dtype=np.uint8, shape=(n, Hw, Ww))
    cache: dict[int, np.ndarray] = {}
    tod = [(t.hour * 60 + t.minute) // 10 for t in slots]
    p = PARAMS
    c_in, c_out = math.cos(math.radians(p["glint_in"])), math.cos(math.radians(p["glint_out"]))
    sub = SATS[sat]["lon"]
    t0 = time.time()
    for i, t in enumerate(slots):
        near = [j for j in range(n) if min((tod[j] - tod[i]) % 144, (tod[i] - tod[j]) % 144) <= 6]
        for j in list(cache):
            if j not in near:
                del cache[j]
        for j in near:
            if j not in cache:
                cache[j] = np.nan_to_num(load16(raw / f"{slot_name(slots[j])}_bt.png", BT_SCALE), nan=-np.inf)
        bt_clear = np.maximum(np.maximum.reduce([cache[j] for j in near]), floor)
        bt = cache[i]
        vis = load16(raw / f"{slot_name(t)}_vis.png", VIS_SCALE)
        mu = cos_solar_zenith(t, lon, lat).astype(np.float32)
        ir = 1 - np.exp(-np.clip(bt_clear - bt - p["bt_margin"], 0, None) / p["bt_k"])
        rn = vis / np.maximum(mu, 0.08)
        margin = p["vis_margin"] + p["vis_margin_mu"] / np.maximum(mu, 0.08) + p["vis_margin_rel"] * vis_clear
        vo = 1 - np.exp(-np.clip(rn - vis_clear - margin, 0, None) / p["vis_k"])
        wv = np.clip((mu - 0.10) / 0.20, 0, 1)
        wv = wv * wv * (3 - 2 * wv)
        g = np.clip((c_in - glint_cos(t, lon, lat, sub)) / (c_in - c_out), 0, 1)
        wv = np.where(water, wv * g, wv)
        o = ir + wv * np.clip(np.nan_to_num(vo, nan=0.0) - ir, 0, None)
        o = np.where(np.isfinite(bt), o, 0.0) * weight
        op[i] = np.clip(np.nan_to_num(o, nan=0.0) * 255, 0, 255).round().astype(np.uint8)
        del vis, mu, ir, rn, margin, vo, wv, g, o, bt_clear
        if i % 12 == 0:
            click.echo(f"   pass 2 {slot_name(t)}  mean {op[i].mean() / 255:.3f}  {(time.time() - t0) / 60:.1f} min")
    op.flush()
    meta = {"sat": sat, "slots": [slot_name(t) for t in slots], "window": win, "shape": [n, Hw, Ww], "level": L5}
    (out_dir / f"{sat}.json").write_text(json.dumps(meta))
    click.echo(f"opacity memmap {out_dir / (sat + '.npy')}  {n} x {Hw} x {Ww}")


@cli.command()
@click.option("--sat", required=True)
def flow(sat):
    """Forward optical flow between consecutive opacity frames, at level 5 / FLOW_Q, in level-5 texels."""
    import cv2
    meta = json.loads((TROOT / "op5" / f"{sat}.json").read_text())
    op = np.load(TROOT / "op5" / f"{sat}.npy", mmap_mode="r")
    n, Hw, Ww = op.shape
    q = FLOW_Q
    Hq, Wq = Hw // q, Ww // q
    out = np.lib.format.open_memmap(TROOT / "op5" / f"{sat}_flow.npy", mode="w+", dtype=np.float16, shape=(n, Hq, Wq, 2))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)

    def down(i):
        return op[i].reshape(Hq, q, Wq, q).mean((1, 3)).astype(np.uint8)

    prev = down(0)
    mags = []
    t0 = time.time()
    for i in range(n - 1):
        nxt = down(i + 1)
        f = dis.calc(prev, nxt, None)                       # (Hq, Wq, 2), quarter-res px, x then y
        f = cv2.GaussianBlur(f, (0, 0), 1.5) * q              # smooth (cloud motion is), scale to level-5 texels
        out[i] = f.astype(np.float16)
        mags.append(float(np.percentile(np.hypot(f[..., 0], f[..., 1])[::8, ::8], 99)))
        prev = nxt
        if i % 24 == 0:
            click.echo(f"   {i}/{n - 1}  p99 {mags[-1]:.1f} texels  {(time.time() - t0) / 60:.1f} min")
    out[n - 1] = 0
    out.flush()
    p99 = float(np.percentile(mags, 90))                    # a robust "typical fast" magnitude across the day
    meta["flow"] = {"q": q, "p99_texels": round(p99, 2), "levels_per_texel": round(100.0 / max(p99, 1e-3), 4)}
    (TROOT / "op5" / f"{sat}.json").write_text(json.dumps(meta))
    click.echo(f"flow: p99 magnitude {p99:.1f} level-5 texels -> {meta['flow']['levels_per_texel']:.3f} levels/texel")


# ---------------------------------------------------------------- encode: one Annex B stream per tile plus an index

def parse_annexb(buf: bytes) -> dict:
    """Access units of an Annex B stream (an AUD opens each one): byte offsets, keyframes, codec string."""
    starts = []                                            # (offset of the start code, nal type)
    for m in re.finditer(b"\x00\x00\x01", buf):
        p = m.start()
        off = p - 1 if p > 0 and buf[p - 1] == 0 else p
        if p + 3 < len(buf):
            starts.append((off, buf[p + 3] & 0x1F))
    aus, codec = [], None
    for off, typ in starts:
        if typ == 9:
            aus.append({"off": off, "key": False})
        elif typ == 5 and aus:
            aus[-1]["key"] = True
        elif typ == 7 and codec is None:
            p = off + (4 if buf[off + 2] == 0 else 3) + 1     # past the start code and the NAL header
            codec = f"avc1.{buf[p]:02x}{buf[p + 1]:02x}{buf[p + 2]:02x}"
    offsets = [a["off"] for a in aus] + [len(buf)]
    return {"offsets": offsets, "keys": [i for i, a in enumerate(aus) if a["key"]], "codec": codec}


def encode_tile(frames_yuv, dest: Path, crf: int) -> dict:
    """frames_yuv: iterable of (Y uint8 512x512, U uint8 256x256, V uint8 256x256)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{TILE}x{TILE}", "-r", str(FPS), "-i", "pipe:0",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0", "-bf", "0",
           # tagged limited range with the values left as they are (0..255 = opacity): a stream tagged full range
           # comes out of the Mac hardware decoder squeezed into 16..235, and the page reads the planes raw
           "-x264-params", "aud=1:repeat-headers=1", "-color_range", "tv", "-f", "h264", str(dest)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for y, u, v in frames_yuv:
        proc.stdin.write(y.tobytes()); proc.stdin.write(u.tobytes()); proc.stdin.write(v.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {dest}")
    buf = dest.read_bytes()
    idx = parse_annexb(buf)
    idx["bytes"] = len(buf)
    return idx


@cli.command()
@click.option("--sat", required=True)
@click.option("--out", default="site/globe/tiles", type=click.Path(path_type=Path), show_default=True)
@click.option("--levels", default="5,4", show_default=True)
@click.option("--crf", default=26, show_default=True)
@click.option("--workers", default=8, show_default=True)
@click.option("--only", default="", help="tx_ty of one tile, for a test")
def encode(sat, out, levels, crf, workers, only):
    import cv2
    meta = json.loads((TROOT / "op5" / f"{sat}.json").read_text())
    op = np.load(TROOT / "op5" / f"{sat}.npy", mmap_mode="r")
    fl = np.load(TROOT / "op5" / f"{sat}_flow.npy", mmap_mode="r")
    n = op.shape[0]
    win = meta["window"]
    lpt = meta["flow"]["levels_per_texel"]                  # at level 5; chroma levels are the same at every level (see below)
    q = FLOW_Q
    weight = win_weight(sat, win)
    for L in [int(x) for x in levels.split(",")]:
        s = 1 << (L5 - L)                                    # level-5 px per px at this level
        lout = out / f"L{L}"
        lout.mkdir(parents=True, exist_ok=True)
        tx0, tx1, ty0, ty1 = win["tx0"] // s, win["tx1"] // s, win["ty0"] // s, win["ty1"] // s
        tiles = []
        for ty in range(ty0, ty1):
            for tx in range(tx0, tx1):
                r0, c0 = (ty - ty0) * TILE * s, (tx - tx0) * TILE * s
                if weight[r0:r0 + TILE * s, c0:c0 + TILE * s].max() > 0:
                    tiles.append((tx, ty, r0, c0))
        if only:
            tiles = [t for t in tiles if f"{t[0]}_{t[1]}" == only]
        click.echo(f"level {L}: {len(tiles)} tiles of {(tx1 - tx0) * (ty1 - ty0)} in the window")

        def one(tile):
            tx, ty, r0, c0 = tile

            def frames():
                for i in range(n):
                    y = op[i, r0:r0 + TILE * s, c0:c0 + TILE * s]
                    if s > 1:
                        y = y.reshape(TILE, s, TILE, s).mean((1, 3)).round().astype(np.uint8)
                    # flow for this tile at chroma resolution (256x256): the flow field is at level 5 / q,
                    # so the tile's patch is (TILE*s/q) square; resize to 256. Levels = 128 + texels5 * lpt,
                    # which is the same number at level 4, where the texel is 2x bigger and lpt 2x smaller.
                    f = fl[i, r0 // q:(r0 + TILE * s) // q, c0 // q:(c0 + TILE * s) // q].astype(np.float32)
                    f = cv2.resize(f, (TILE // 2, TILE // 2), interpolation=cv2.INTER_LINEAR)
                    uv = np.clip(np.round(128 + f * lpt), 0, 255).astype(np.uint8)
                    yield np.ascontiguousarray(y), np.ascontiguousarray(uv[..., 0]), np.ascontiguousarray(uv[..., 1])

            dest = lout / f"{tx}_{ty}.h264"
            idx = encode_tile(frames(), dest, crf)
            if len(idx["offsets"]) - 1 != n:
                raise RuntimeError(f"{dest}: {len(idx['offsets']) - 1} access units for {n} frames")
            return f"{tx}_{ty}", idx

        index = {"level": L, "tile": TILE, "width": TILE << L, "height": TILE << (L - 1), "fps": FPS, "gop": GOP,
                 "frames": meta["slots"], "lat_max": LAT_MAX, "sats": [sat],
                 "flow_levels_per_texel": lpt / s, "tiles": {}}
        t0 = time.time()
        with ThreadPoolExecutor(workers) as pool:
            for k, (name, idx) in enumerate(pool.map(one, tiles), 1):
                index["tiles"][name] = idx
                if index.get("codec") is None:
                    index["codec"] = idx["codec"]
                if k % 10 == 0 or k == len(tiles):
                    tot = sum(v["bytes"] for v in index["tiles"].values())
                    click.echo(f"   {k}/{len(tiles)}  {tot / 1e6:.0f} MB  {(time.time() - t0) / 60:.1f} min")
        for v in index["tiles"].values():
            v.pop("codec", None)
        (out / f"L{L}.json").write_text(json.dumps(index, separators=(",", ":")))
        tot = sum(v["bytes"] for v in index["tiles"].values())
        click.echo(f"level {L}: {len(tiles)} tiles, {tot / 1e6:.1f} MB, {tot / 1e6 / max(len(tiles), 1):.2f} MB/tile, codec {index.get('codec')}, index {out / f'L{L}.json'}")


# ---------------------------------------------------------------- the contact sheet

@cli.command()
@click.option("--sat", required=True)
@click.option("--lon", required=True, type=float)
@click.option("--lat", required=True, type=float)
@click.option("--span", default=10.0, show_default=True, help="degrees of longitude shown")
@click.option("--slot", required=True)
@click.option("--name", required=True)
@click.option("--out", default="docs/globe-2026-09-18", type=click.Path(path_type=Path), show_default=True)
def sheet(sat, lon, lat, span, slot, name, out):
    """One place at level 3 (the base clip's frame), 4 and 5, bilinearly upsampled to the same size."""
    meta = json.loads((TROOT / "op5" / f"{sat}.json").read_text())
    op = np.load(TROOT / "op5" / f"{sat}.npy", mmap_mode="r")
    i = meta["slots"].index(slot)
    win = meta["window"]
    w5 = int(round(span / DEG5)) // 4 * 4; h5 = int(round(w5 * 0.75)) // 4 * 4
    c0 = int(round((lon - span / 2 + 180) / DEG5)) - win["c0"]
    r0 = int(round((90 - lat) / DEG5 - h5 / 2)) - win["r0"]
    a5 = op[i, r0:r0 + h5, c0:c0 + w5].astype(np.float32)
    a4 = a5.reshape(h5 // 2, 2, w5 // 2, 2).mean((1, 3))
    from fetch_clouds import W as W3
    base = np.asarray(Image.open(f"data/clouds/frames/{slot}.png")).astype(np.float32)
    d3 = 360 / W3
    c3 = int(round((lon - span / 2 + 180) / d3)); r3 = int(round((LAT_MAX - lat) / d3 - (h5 // 4) / 2))
    a3 = base[r3:r3 + h5 // 4, c3:c3 + w5 // 4]
    panels = []
    for lab, a in (("level 3 - 10 km - the clip today", a3), ("level 4 - 4.9 km", a4), ("level 5 - 2.4 km - native", a5)):
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((w5, h5), Image.BILINEAR)
        panels.append((lab, im))
    pad, cap = 12, 34
    sheet_im = Image.new("RGB", (3 * w5 + 4 * pad, h5 + cap + 2 * pad + 18), (4, 9, 15))
    d = ImageDraw.Draw(sheet_im)
    for k, (lab, im) in enumerate(panels):
        x = pad + k * (w5 + pad)
        sheet_im.paste(im.convert("RGB"), (x, pad + cap))
        d.text((x, pad + 8), lab, fill=(201, 214, 226))
    d.text((pad, h5 + cap + pad + 4), f"{sat}  {slot}  centre {lon:.1f}E {lat:.1f}N  {span:.0f} deg wide  (opacity x view weight, grey = cloud)", fill=(120, 140, 160))
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"tiles_levels_{name}.jpg"
    sheet_im.save(dest, quality=88)
    click.echo(f"{dest}  {sheet_im.size}")


if __name__ == "__main__":
    cli()
