"""Does HRRR put cloud where GOES saw cloud? Hourly, over a month, at the scale of our frame.

PRIOR ART: scripts/sky_memory.py measures the archive against ITSELF (memory, motion, budget)
and defines the "weather anomaly" used here: what is left after the per-solar-hour mean map is
removed. Nothing compared the archive against a physics model. This does, with the two
controls a physics skeleton must beat before it is worth building on.

    python scripts/hrrr_agreement.py --place california

Pairs every HRRR analysis hour (data/hrrr/<place>, from fetch_hrrr.py) with the GOES frames at
the same instant (Band 13 infrared at :00; GeoColor at the nearest slot, :50 or :10).

(a) TEMPERATURE  HRRR's simulated 10.7 um window brightness temperature (SBT114 -- NOT SBT113,
    which is the water-vapour channel; see fetch_hrrr.py) against the real band 13 frame. The GIBS frame is a colour-mapped JPEG, not Kelvin: every one of the
    237 palette colours is distinct, so the nearest palette entry gives the temperature back
    (99.4% of pixels sit within an L1 distance of 6 of an entry -- JPEG noise). Spearman and
    Pearson over the covered frame, raw and as weather anomaly.
(b) CLOUD MASK   HRRR low / middle+high / total cloud fraction against the GeoColor cloud
    test (build_dataset.stats: bright and unsaturated) in daylight over ocean, block-averaged,
    at a ladder of scales, and by distance-from-coast band.

Controls, per hour: SHUFFLED -- HRRR from a random OTHER day at the same hour of day (same
diurnal phase, different weather; the floor a model earns by climatology alone). PERSISTENCE --
GOES itself 24 h earlier, and 1 h earlier (what a nowcast starts from). Both are shaped
exactly like the real comparison: same grid, same mask, same metric.

Everything is computed at 384 px (3.8 km/px, about HRRR's own 3 km) and only where HRRR covers
the frame: its western edge cuts through, so the far-offshore third is outside the model.
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent))
from hrrr_grid import frame_to_hrrr  # noqa: E402

N = 384                      # working grid (frame 768 -> 2x2 block mean)
CMAP = Path("data/hrrr/gibs_band13_colormap.xml")
DAYLIGHT_UTC = {17, 18, 19, 20, 21, 22, 23, 0}   # solar 09-16 at lon -122
LADDER = [1, 2, 4, 8, 16, 32]                   # blocks of the 384 grid: 3.8 .. 122 km
BANDS = [("400+ km offshore", 400, 1e9), ("150-400 km offshore", 150, 400), ("50-150 km offshore", 50, 150),
         ("0-50 km offshore", 0, 50), ("0-100 km inland", -100, 0), ("100+ km inland", -1e9, -100)]


# ---------------------------------------------------------------- GOES readers
def stamp(p: Path) -> datetime:
    return datetime.strptime(p.stem, "%Y-%m-%dT%H%M%SZ")


class IRPalette:
    """Nearest-colour lookup through a 6-bit RGB cube, built once from the GIBS colormap."""
    def __init__(self, xml: Path):
        ents = re.findall(r'rgb="(\d+),(\d+),(\d+)"[^>]*sourceValue="\(([-\d.]+),([-\d.]+)\]"', xml.read_text())
        rgb = np.array([[int(r), int(g), int(b)] for r, g, b, _, _ in ents], np.int16)
        temp = np.array([(float(a) + float(b)) / 2 for *_, a, b in ents], np.float32)
        # TRAP (found 2026-09-14): the palette carries TWO grey ramps. The main one runs from
        # -18.9 C (197) to 56.7 C (2), one grey level per half degree. A second, coarser one
        # sits at -79.6..-70.6 C (230, 204, 177, 155, 129, 102, 76, 54, 27, 5) and lands ON the
        # first: (102,102,102) is both -74.6 C and one JPEG count from 18.1 C. Nearest-colour
        # then read a tenth of warm land as deep-cold cloud tops. The cold ramp is dropped:
        # tops between -80 and -70 C cannot be recovered from this render and will read warm.
        grey = (rgb[:, 0] == rgb[:, 1]) & (rgb[:, 1] == rgb[:, 2])
        self.dropped = grey & (temp < -20) & (temp > -85)
        self.rgb, self.temp = rgb[~self.dropped], temp[~self.dropped]
        q = np.arange(64) * 4 + 2
        cube = np.stack(np.meshgrid(q, q, q, indexing="ij"), -1).reshape(-1, 3).astype(np.int16)
        idx = np.empty(len(cube), np.int32)
        for s in range(0, len(cube), 16384):
            d = np.abs(cube[s:s + 16384, None, :] - self.rgb[None]).sum(-1)
            idx[s:s + 16384] = d.argmin(1)
        self.lut = self.temp[idx].reshape(64, 64, 64)
        # self-check: every kept colour, perturbed by JPEG-sized noise, must invert to itself
        worst = 0.0
        for d in (-2, 0, 2):
            noisy = np.clip(self.rgb + d, 0, 255).astype(np.uint8)
            worst = max(worst, float(np.abs(self(noisy) - self.temp).max()))
        if worst > 2.51:   # half-degree grey steps are ~1.3 levels, so +-2 noise is up to ~2 C; the bug read 93 C
            raise RuntimeError(f"palette inversion is ambiguous: {worst:.1f} C error under +-2 noise")
        self.selfcheck_max_error_c = worst

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        r, g, b = (rgb[..., 0] >> 2, rgb[..., 1] >> 2, rgb[..., 2] >> 2)
        return self.lut[r, g, b]


def block(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[0] // k, a.shape[1] // k
    return a[:h * k, :w * k].reshape(h, k, w, k).mean((1, 3))


def read_ir(p: Path, pal: IRPalette):
    a = np.asarray(Image.open(p).convert("RGB"))
    nodata = a.max(-1) <= 2
    t = pal(a)
    t[nodata] = np.nan
    return block(t, 768 // N)          # nan propagates: a block with any hole is missing


def read_cloud(p: Path):
    f = np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255
    lum = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    mx, mn = f.max(-1), f.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    cloud = ((lum > 0.42) & (sat < 0.35)).astype(np.float32)
    return block(cloud, 768 // N)


# ---------------------------------------------------------------- HRRR reader
class Hrrr:
    def __init__(self, src: Path, place: str):
        self.by = {datetime.strptime(p.stem, "%Y-%m-%dT%HZ"): p for p in src.glob("*.npz")}
        I, J, inside, meta = frame_to_hrrr(place, N)
        self.I, self.J, self.inside, self.meta = I, J, inside, meta

    def field(self, t: datetime, key: str) -> np.ndarray | None:
        p = self.by.get(t)
        if p is None:
            return None
        z = np.load(p)
        a = z[key].astype(np.float32)
        if z[key].dtype == np.uint8:
            a[a > 100] = np.nan                        # 255 = missing
        out = map_coordinates(a, [self.J - float(z["j0"]), self.I - float(z["i0"])], order=1, mode="constant", cval=np.nan)
        out[~self.inside] = np.nan
        return out


# ---------------------------------------------------------------- metrics
def corr(a: np.ndarray, b: np.ndarray, mask: np.ndarray, rank: bool = False) -> float:
    m = mask & np.isfinite(a) & np.isfinite(b)
    if m.sum() < 50:
        return float("nan")
    x, y = a[m], b[m]
    if rank:
        return float(spearmanr(x, y).statistic)
    if x.std() < 1e-6 or y.std() < 1e-6:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def nanmean(v):
    v = [x for x in v if x == x]
    return float(np.mean(v)) if v else float("nan")


def summarize(rows: dict[str, list[float]]) -> dict:
    return {k: dict(mean=round(nanmean(v), 4), median=round(float(np.nanmedian(v)), 4) if len(v) else None,
                    n=int(np.isfinite(v).sum())) for k, v in rows.items()}



# ---------------------------------------------------------------- figures
def _grey(a, lo, hi):
    g = np.clip((np.nan_to_num(a, nan=lo) - lo) / (hi - lo), 0, 1)
    return (g * 255).astype(np.uint8)


def _coast(land):
    from scipy.ndimage import binary_dilation
    return binary_dilation(land, iterations=1) & ~land


def _paint(img8, inside, land, tint_outside=True):
    """grey field -> RGB with the coastline in ochre and the uncovered region dimmed."""
    rgb = np.stack([img8] * 3, -1).astype(np.int16)
    if tint_outside:
        rgb[~inside] = (rgb[~inside] * 0.35 + np.array([20, 30, 45])).astype(np.int16)
    rgb[_coast(land)] = [179, 101, 42]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def _rmap_rgb(r, inside, land):
    """correlation map: blue (negative) .. dark (0) .. warm white (1); uncovered dimmed."""
    v = np.nan_to_num(r, nan=0.0)
    pos = np.clip(v, 0, 1); neg = np.clip(-v, 0, 1)
    R = 18 + 237 * pos; G = 28 + 200 * pos + 60 * neg; B = 45 + 120 * pos + 180 * neg
    rgb = np.stack([R, G, B], -1)
    rgb[~inside] = [12, 16, 24]
    rgb[_coast(land)] = [179, 101, 42]
    return Image.fromarray(rgb.astype(np.uint8))


def _label(im, text):
    from PIL import ImageFont
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=15)
    d.rectangle([0, 0, im.width, 24], fill=(6, 17, 28))
    d.text((8, 4), text, fill=(228, 236, 244), font=f)
    return im


def render_maps(out, rmap_T, rmap_C, inside, land, meta):
    a = _label(_rmap_rgb(rmap_T, inside, land), "infrared anomaly: r per pixel, all hours")
    b = _label(_rmap_rgb(rmap_C, inside, land), "cloud mask anomaly: r per pixel, daylight")
    strip = Image.new("RGB", (N * 2 + 8, N), (6, 17, 28))
    strip.paste(a, (0, 0)); strip.paste(b, (N + 8, 0))
    strip.save(out / "agreement_maps.jpg", quality=88)


def render_examples(out, hours, C, G, H, L, geo, inside, land, series):
    """Five hours: clear-ish, mixed and overcast by GOES cloud share, plus the best and worst hourly agreement."""
    day = [t for t in sorted(C)]
    if not day:
        return []
    share = {t: float(np.nanmean(C[t].astype(np.float32)[inside & ~land])) for t in day}
    ranked = sorted(day, key=lambda t: share[t])
    picks = {"clear-ish": ranked[len(ranked) // 5], "mixed": ranked[len(ranked) // 2], "overcast": ranked[4 * len(ranked) // 5]}
    by_t = {s["t"]: s for s in series}
    scored = [t for t in day if t.strftime("%Y-%m-%dT%HZ") in by_t and by_t[t.strftime("%Y-%m-%dT%HZ")]["anom"] == by_t[t.strftime("%Y-%m-%dT%HZ")]["anom"]]
    if scored:
        picks["best agreement"] = max(scored, key=lambda t: by_t[t.strftime("%Y-%m-%dT%HZ")]["anom"])
        picks["worst agreement"] = min(scored, key=lambda t: by_t[t.strftime("%Y-%m-%dT%HZ")]["anom"])
    rows = []
    for name, t in picks.items():
        g = geo.get(t + timedelta(minutes=10)) or geo.get(t - timedelta(minutes=10)) or geo.get(t)
        geo_im = Image.open(g).convert("RGB").resize((N, N), Image.BOX)
        geo_im = _label(geo_im, f"GeoColor  {t:%d %b %H}Z")
        ir = _label(_paint(_grey(G[t].astype(np.float32), -60, 25), np.ones_like(inside), land, False), "GOES band 13, -60..25 C")
        sb = _label(_paint(_grey(H[t].astype(np.float32), -60, 25), inside, land), "HRRR simulated 10.7 um")
        cm = _label(_paint(_grey(C[t].astype(np.float32), 0, 1), np.ones_like(inside), land, False), "GOES cloud mask")
        lc = _label(_paint(_grey(L[t]["LCDC"].astype(np.float32), 0, 1), inside, land), "HRRR low cloud fraction")
        strip = Image.new("RGB", (N * 5 + 32, N), (6, 17, 28))
        for k, im in enumerate([geo_im, ir, sb, cm, lc]):
            strip.paste(im, (k * (N + 8), 0))
        fn = f"example_{name.replace(' ', '_')}.jpg"
        strip.save(out / fn, quality=86)
        st = by_t.get(t.strftime("%Y-%m-%dT%HZ"), {})
        rows.append(dict(name=name, t=t.strftime("%Y-%m-%dT%HZ"), file=fn, cloud_share=round(share[t], 3), anom_r=st.get("anom"), shuffled_r=st.get("shuf")))
    return rows


# ---------------------------------------------------------------- main
@click.command()
@click.option("--place", default="california")
@click.option("--lon", default=-122.0, type=float)
@click.option("--limit", default=0, type=int, help="first N HRRR hours only (smoke test)")
@click.option("--out", default=None, type=click.Path(path_type=Path), help="default site/hrrr/<place>")
@click.option("--seed", default=0, type=int)
def main(place, lon, limit, out, seed):
    random.seed(seed)
    out = out or Path("site/hrrr") / place
    out.mkdir(parents=True, exist_ok=True)
    pal = IRPalette(CMAP)
    print(f"palette: {len(pal.temp)} colours kept, {int(pal.dropped.sum())} ambiguous cold greys dropped, "
          f"worst inversion error under +-2 noise {pal.selfcheck_max_error_c:.2f} C")
    hr = Hrrr(Path("data/hrrr") / place, place)
    ir = {stamp(p): p for p in (Path("data/goes") / f"{place}_x3_ir").glob("*.jpg")}
    geo = {stamp(p): p for p in (Path("data/goes") / f"{place}_x3").glob("*.jpg")}
    land = np.asarray(Image.open(Path("data/terrain") / f"{place}_land.png").convert("L").resize((N, N), Image.BOX)) > 127
    mpp = hr.meta["m_per_px_mid"] * 768 / N
    coast_km = np.where(land, -distance_transform_edt(land), distance_transform_edt(~land)) * mpp / 1000
    ocean = ~land
    inside = hr.inside
    print(f"{place}: HRRR covers {inside.mean():.1%} of the frame; ocean {ocean.mean():.1%}; "
          f"covered ocean {(inside & ocean).mean():.1%}")

    hours = sorted(t for t in hr.by if t in ir)
    if limit:
        hours = hours[:limit]
    print(f"{len(hours)} HRRR hours with an infrared frame")

    # ---- pass 1: load everything at 384 (float16 to keep the month in memory)
    G, H, C, L = {}, {}, {}, {}
    for t in hours:
        G[t] = read_ir(ir[t], pal).astype(np.float16)
        H[t] = (hr.field(t, "SBT114") - 273.15).astype(np.float16)
        if t.hour in DAYLIGHT_UTC:
            g = geo.get(t + timedelta(minutes=10)) or geo.get(t - timedelta(minutes=10)) or geo.get(t)
            if g is not None:
                C[t] = read_cloud(g).astype(np.float16)
                L[t] = {"LCDC": (hr.field(t, "LCDC") / 100).astype(np.float16), "TCDC": (hr.field(t, "TCDC") / 100).astype(np.float16)}
                L[t]["MHCDC"] = np.maximum(hr.field(t, "MCDC"), hr.field(t, "HCDC")).astype(np.float16) / 100
    print(f"loaded: {len(G)} IR pairs, {len(C)} daylight GeoColor pairs")

    # ---- per-solar-hour mean maps (fixed geography + daily cycle) -> anomalies
    def hour_means(store, keyf=lambda v: v):
        acc, cnt = defaultdict(lambda: np.zeros((N, N), np.float64)), defaultdict(lambda: np.zeros((N, N), np.float64))
        for t, v in store.items():
            a = keyf(v).astype(np.float32); m = np.isfinite(a)
            acc[t.hour][m] += a[m]; cnt[t.hour][m] += 1
        return {h: np.where(cnt[h] >= 3, acc[h] / np.maximum(cnt[h], 1), np.nan) for h in acc}
    mG, mH = hour_means(G), hour_means(H)
    mC = hour_means(C)
    mL = {k: hour_means(L, lambda v, k=k: v[k]) for k in ("LCDC", "MHCDC", "TCDC")}
    anomG = lambda t: G[t].astype(np.float32) - mG[t.hour]
    anomH = lambda t: H[t].astype(np.float32) - mH[t.hour]

    days = sorted({t.date() for t in hours})
    def other_day(t, store):
        cands = [u for u in store if u.hour == t.hour and u.date() != t.date()]
        return random.choice(cands) if cands else None

    # ---- (a) temperature
    A = defaultdict(list)          # metric -> per-hour values
    Aband = defaultdict(lambda: defaultdict(list))
    Aladder = defaultdict(lambda: defaultdict(list))
    series = []
    for t in hours:
        g, h = G[t].astype(np.float32), H[t].astype(np.float32)
        ga, ha = anomG(t), anomH(t)
        m = inside
        A["raw_spearman"].append(corr(g, h, m, rank=True))
        A["raw_pearson"].append(corr(g, h, m))
        A["raw_mae_K"].append(float(np.nanmean(np.abs(g - h)[m])))
        A["raw_bias_K"].append(float(np.nanmean((h - g)[m])))
        A["anom_pearson"].append(corr(ga, ha, m))
        A["anom_pearson_ocean"].append(corr(ga, ha, m & ocean))
        A["anom_pearson_land"].append(corr(ga, ha, m & land))
        o = other_day(t, H)
        A["ctl_shuffled_anom"].append(corr(ga, anomH(o), m) if o else float("nan"))
        A["ctl_shuffled_raw_spearman"].append(corr(g, H[o].astype(np.float32), m, rank=True) if o else float("nan"))
        for lag, name in ((24, "ctl_persist24h_anom"), (1, "ctl_persist1h_anom")):
            u = t - timedelta(hours=lag)
            A[name].append(corr(ga, anomG(u), m) if u in G else float("nan"))
        A["ctl_persist24h_raw_spearman"].append(corr(g, G[t - timedelta(hours=24)].astype(np.float32), m, rank=True)
                                                if t - timedelta(hours=24) in G else float("nan"))
        for name, lo, hi in BANDS:
            bm = m & (coast_km >= lo) & (coast_km < hi)
            Aband[name]["anom_pearson"].append(corr(ga, ha, bm))
            Aband[name]["ctl_shuffled"].append(corr(ga, anomH(o), bm) if o else float("nan"))
            Aband[name]["ctl_persist24h"].append(corr(ga, anomG(t - timedelta(hours=24)), bm) if t - timedelta(hours=24) in G else float("nan"))
        for k in LADDER:
            bm = block(m.astype(np.float32), k) > 0.99
            Aladder[k]["anom_pearson"].append(corr(block(np.nan_to_num(ga), k), block(np.nan_to_num(ha), k), bm))
            Aladder[k]["ctl_shuffled"].append(corr(block(np.nan_to_num(ga), k), block(np.nan_to_num(anomH(o)), k), bm) if o else float("nan"))
        series.append(dict(t=t.strftime("%Y-%m-%dT%HZ"), anom=A["anom_pearson"][-1], shuf=A["ctl_shuffled_anom"][-1],
                           p24=A["ctl_persist24h_anom"][-1], p1=A["ctl_persist1h_anom"][-1],
                           goes_cold_share=float(np.nanmean((g < -20)[m]))))

    # ---- (b) cloud mask, daylight, ocean
    B = defaultdict(lambda: defaultdict(list))        # field -> metric -> values
    Bband = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    Bladder = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    mo = inside & ocean
    for t in sorted(C):
        c = C[t].astype(np.float32); ca = c - mC[t.hour]
        o = other_day(t, C)
        u24, u1 = t - timedelta(hours=24), t - timedelta(hours=1)
        for k in ("LCDC", "MHCDC", "TCDC"):
            l = L[t][k].astype(np.float32); la = l - mL[k][t.hour]
            B[k]["raw_pearson"].append(corr(c, l, mo))
            B[k]["anom_pearson"].append(corr(ca, la, mo))
            B[k]["cloud_share_goes"].append(float(np.nanmean(c[mo])))
            B[k]["cloud_share_hrrr"].append(float(np.nanmean(l[mo])))
            # binary agreement at 15 km blocks
            cb, lb, mb = block(c, 4), block(np.nan_to_num(l), 4), block(mo.astype(np.float32), 4) > 0.99
            hit = ((cb >= .5) & (lb >= .5))[mb].sum(); miss = ((cb >= .5) & (lb < .5))[mb].sum()
            fa = ((cb < .5) & (lb >= .5))[mb].sum(); cn = ((cb < .5) & (lb < .5))[mb].sum()
            B[k]["hit_rate_15km"].append(hit / max(hit + miss, 1)); B[k]["false_alarm_15km"].append(fa / max(fa + hit, 1))
            B[k]["accuracy_15km"].append((hit + cn) / max(hit + miss + fa + cn, 1))
            if o:
                lo_ = L[o][k].astype(np.float32)
                B[k]["ctl_shuffled_raw"].append(corr(c, lo_, mo)); B[k]["ctl_shuffled_anom"].append(corr(ca, lo_ - mL[k][t.hour], mo))
            for name, lo, hi in BANDS[:4]:
                bm = mo & (coast_km >= lo) & (coast_km < hi)
                Bband[k][name]["anom_pearson"].append(corr(ca, la, bm))
                Bband[k][name]["raw_pearson"].append(corr(c, l, bm))
                if o:
                    Bband[k][name]["ctl_shuffled_anom"].append(corr(ca, L[o][k].astype(np.float32) - mL[k][t.hour], bm))
            for kk in LADDER:
                bm = block(mo.astype(np.float32), kk) > 0.99
                Bladder[k][kk]["anom_pearson"].append(corr(block(ca, kk), block(np.nan_to_num(la), kk), bm))
                if o:
                    Bladder[k][kk]["ctl_shuffled_anom"].append(corr(block(ca, kk), block(np.nan_to_num(L[o][k].astype(np.float32) - mL[k][t.hour]), kk), bm))
        # persistence bars for the mask (GOES vs GOES), independent of field
        B["persist"]["ctl_persist24h_anom"].append(corr(ca, C[u24].astype(np.float32) - mC[u24.hour], mo) if u24 in C else float("nan"))
        B["persist"]["ctl_persist24h_raw"].append(corr(c, C[u24].astype(np.float32), mo) if u24 in C else float("nan"))
        B["persist"]["ctl_persist1h_anom"].append(corr(ca, C[u1].astype(np.float32) - mC[u1.hour], mo) if u1 in C else float("nan"))


    # ---- per-pixel agreement maps over the month (where does HRRR know the sky?)
    def pixel_corr(pairs):
        n = np.zeros((N, N)); sx = np.zeros((N, N)); sy = np.zeros((N, N)); sxx = np.zeros((N, N)); syy = np.zeros((N, N)); sxy = np.zeros((N, N))
        for a, b in pairs:
            m = np.isfinite(a) & np.isfinite(b)
            a = np.where(m, a, 0); b = np.where(m, b, 0)
            n += m; sx += a; sy += b; sxx += a * a; syy += b * b; sxy += a * b
        cov = sxy / np.maximum(n, 1) - (sx / np.maximum(n, 1)) * (sy / np.maximum(n, 1))
        va = sxx / np.maximum(n, 1) - (sx / np.maximum(n, 1)) ** 2; vb = syy / np.maximum(n, 1) - (sy / np.maximum(n, 1)) ** 2
        r = cov / np.sqrt(np.maximum(va * vb, 1e-9))
        r[n < 20] = np.nan
        return r
    rmap_T = pixel_corr((anomG(t), anomH(t)) for t in hours)
    rmap_C = pixel_corr((C[t].astype(np.float32) - mC[t.hour], L[t]["LCDC"].astype(np.float32) - mL["LCDC"][t.hour]) for t in sorted(C))
    o_pairs = [(t, other_day(t, H)) for t in hours]
    rmap_T_shuf = pixel_corr((anomG(t), anomH(o)) for t, o in o_pairs if o)
    result_maps = dict(
        temperature_anomaly_r=dict(ocean=float(np.nanmean(rmap_T[inside & ocean])), land=float(np.nanmean(rmap_T[inside & land])),
                                   shuffled_ocean=float(np.nanmean(rmap_T_shuf[inside & ocean]))),
        cloud_anomaly_r=dict(ocean=float(np.nanmean(rmap_C[inside & ocean]))))
    render_maps(out, rmap_T, rmap_C, inside, land, hr.meta)
    examples = render_examples(out, hours, C, G, H, L, geo, inside, land, series)

    result = dict(
        place=place, maps=result_maps, examples=examples, generated=datetime.now().strftime("%Y-%m-%dT%H:%MZ"), grid_px=N, km_per_px=round(mpp / 1000, 2),
        coverage=dict(frame=round(float(inside.mean()), 3), ocean=round(float(ocean.mean()), 3),
                      covered_ocean=round(float(mo.mean()), 3),
                      band_coverage={name: round(float((inside & (coast_km >= lo) & (coast_km < hi)).sum() / max(((coast_km >= lo) & (coast_km < hi)).sum(), 1)), 3)
                                     for name, lo, hi in BANDS}),
        n=dict(hours=len(hours), days=len(days), ir_pairs=len(G), daylight_pairs=len(C)),
        temperature=dict(overall=summarize(A), by_band={b: summarize(v) for b, v in Aband.items()},
                         ladder={f"{k * mpp / 1000:.0f} km": summarize(v) for k, v in Aladder.items()}),
        cloud=dict(overall={k: summarize(v) for k, v in B.items()},
                   by_band={k: {b: summarize(v) for b, v in bb.items()} for k, bb in Bband.items()},
                   ladder={k: {f"{kk * mpp / 1000:.0f} km": summarize(v) for kk, v in ll.items()} for k, ll in Bladder.items()}),
        series=series,
    )
    (out / "agreement.json").write_text(json.dumps(result, indent=1))

    # ---- report
    T = result["temperature"]["overall"]
    print("\n(a) TEMPERATURE  band 13, HRRR simulated vs GOES, hourly means over", len(hours), "hours")
    for k in ("raw_spearman", "raw_mae_K", "raw_bias_K", "anom_pearson", "anom_pearson_ocean", "anom_pearson_land",
              "ctl_shuffled_anom", "ctl_persist24h_anom", "ctl_persist1h_anom", "ctl_shuffled_raw_spearman", "ctl_persist24h_raw_spearman"):
        print(f"   {k:30s} {T[k]['mean']:8.3f}  (median {T[k]['median']}, n={T[k]['n']})")
    print("   by band (anomaly r / shuffled / persist24h):")
    for b, v in result["temperature"]["by_band"].items():
        print(f"     {b:22s} {v['anom_pearson']['mean']:6.3f} / {v['ctl_shuffled']['mean']:6.3f} / {v['ctl_persist24h']['mean']:6.3f}   covers {result['coverage']['band_coverage'][b]:.0%}")
    print("   ladder (anomaly r / shuffled):", {k: f"{v['anom_pearson']['mean']:.3f}/{v['ctl_shuffled']['mean']:.3f}" for k, v in result["temperature"]["ladder"].items()})
    print("\n(b) CLOUD MASK  daylight, ocean,", len(C), "hours")
    for k in ("LCDC", "MHCDC", "TCDC"):
        v = result["cloud"]["overall"][k]
        print(f"   {k:8s} raw r {v['raw_pearson']['mean']:.3f}  anom r {v['anom_pearson']['mean']:.3f}  shuffled anom {v['ctl_shuffled_anom']['mean']:.3f}  "
              f"hit {v['hit_rate_15km']['mean']:.2f} FA {v['false_alarm_15km']['mean']:.2f} acc {v['accuracy_15km']['mean']:.2f}  "
              f"cloud share goes {v['cloud_share_goes']['mean']:.2f} hrrr {v['cloud_share_hrrr']['mean']:.2f}")
    pv = result["cloud"]["overall"]["persist"]
    print(f"   persistence bars: 24h anom {pv['ctl_persist24h_anom']['mean']:.3f}  24h raw {pv['ctl_persist24h_raw']['mean']:.3f}  1h anom {pv['ctl_persist1h_anom']['mean']:.3f}")
    for k in ("LCDC", "TCDC"):
        print(f"   {k} by band (anom r / shuffled):", {b: f"{v['anom_pearson']['mean']:.3f}/{v.get('ctl_shuffled_anom', {}).get('mean', float('nan')):.3f}" for b, v in result["cloud"]["by_band"][k].items()})
        print(f"   {k} ladder (anom r / shuffled):", {kk: f"{v['anom_pearson']['mean']:.3f}/{v.get('ctl_shuffled_anom', {}).get('mean', float('nan')):.3f}" for kk, v in result["cloud"]["ladder"][k].items()})
    print(f"\n-> {out / 'agreement.json'}")


if __name__ == "__main__":
    main()
