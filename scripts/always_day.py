"""Always day: the infrared month rendered as one continuous daytime sky.

PRIOR ART: scripts/hrrr_agreement.py owns the infrared JPEG -> temperature inversion (IRPalette,
with the two-grey-ramp trap already handled) and the GeoColor cloud test lives in
build_dataset.stats. Neither renders anything. scripts/month_sheet.py turns a directory of
frames into the calendar and clips and is reused unchanged on this script's output.

    python scripts/always_day.py --dir data/goes/california_x3_ir --place california
    python scripts/always_day.py --dir data/goes/california_x3_ir --place california --limit 40 --sample 3

GeoColor changes character twice a day: true colour by day, an infrared rendering over city
lights at night. Band 13 infrared is the same picture at noon and at 2 am, but it is a
temperature map, not a photograph. This turns every infrared frame into a daytime-looking
one, deterministically, with no model:

  1. BASEMAP  a cloud-free noon Earth on exactly the frame grid, composited from the GeoColor
     month itself over solar 10:00-14:00. Land: per pixel, the mean colour of the frames whose
     luminance sits between that pixel's 10th and 30th percentile (the darkest tenth is cloud
     shadow and noise; above the third decile is cloud). Sea: the deck covers the far-offshore
     pixels nine frames in ten, so the same band is still cloud there; instead the 2nd-8th
     percentile band, then smoothed over 12 px, because clear sea has no texture at 2 km/px
     and the smoothing removes the shadow-and-noise grain the low band picks up. Using
     GeoColor rather than an external mosaic keeps land and sea the colours the player
     already shows. Cached at data/basemap/<place>_day.jpg.
  2. CLEAR-SKY REFERENCE  T_clear[solar hour][pixel] = the 90th percentile of temperature over
     the month at that solar hour (clear is warmest). The low deck sits within a few degrees
     of the sea it floats on, so an absolute threshold cannot see it; a per-pixel, per-hour
     reference can. Cached at data/basemap/<place>_tclear.npy.
  3. OPACITY  from d = T_clear - T, through a MEASURED curve rather than a guessed ramp:
     the calibration step pairs 60 daytime infrared frames (solar 10-15, spread over the month)
     with GeoColor at the same stamp and records, per 1 K bin of d, the mean GeoColor
     luminance, separately over sea and land (data/basemap/<place>_curve.json). Opacity is
     that curve rescaled so d <= 0 is clear and its plateau is fully cloudy. Measured for
     California: over sea the brightness climbs from clear at 0 K to the plateau by 5 K (a
     10 K ramp, the first guess, missed most of the deck); over land it rises slowly out to
     about 30 K, because the land reference is the month's hottest days. Each frame also
     subtracts its own land offset (the 20th percentile of d over land). What the curve
     cannot fix: the deck that sits within 1 K of the sea, about a third of the pixels in
     the 1-2 K bin and 7% of those in the 0-1 K bin, stays clear; that is the honest cost.
  4. COLOUR  cloud is a warm white whose brightness rises from 0.84 at 0 C to 0.98 at -40 C
     and below (GeoColor's deck reads about 0.7 luminance, its cold tops 0.9); the basemap
     is darkened a little under a blurred, slightly displaced copy of the opacity so cloud
     reads as sitting above the ground.
  5. CONTROL  --control-n N pairs N random DAYTIME instants (solar 09-16, never one used for
     calibration) with the real GeoColor frame at the same stamp and applies the same cloud test to both: agreement, and the
     share of GeoColor's cloud that the always-day frame also calls cloud. Writes
     control.json and a strip of side-by-side pairs to the site directory.

Solar time is longitude / 15 h, as in month_sheet.py.
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import click
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter, shift as ndshift

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402
from hrrr_agreement import CMAP, IRPalette  # noqa: E402
from make_timelapse import stamp  # noqa: E402
from month_sheet import solar  # noqa: E402

LON = {"california": -122.0, "conus": -96.0}
CLOUD_RGB = np.array([252, 251, 247], np.float32) / 255
LON_CUR = -122.0


def smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def luminance(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


class FineGrey:
    """Sub-level temperature on the palette's main grey ramp.

    IRPalette decides WHICH palette entry a pixel is (through a 6-bit colour cube, so 4 counts
    of grey, about 1.5 K, per step). The sea ramp here is only 5 K wide, so that step size
    posterised the deck into three flat tones. The main ramp is linear, half a degree per grey
    level, so for a pixel the palette already puts on it (T > -19 C, and grey) the grey value
    itself interpolates the temperature to a quarter degree. It never touches the cold ramp
    the palette dropped: a pixel is refined only where the palette's own answer is on the main
    ramp, so the two-ramp trap stays handled by IRPalette. Self-checked against the palette."""
    def __init__(self, pal: IRPalette):
        g = (pal.rgb[:, 0] == pal.rgb[:, 1]) & (pal.rgb[:, 1] == pal.rgb[:, 2]) & (pal.temp > -19.5)
        o = np.argsort(pal.rgb[g, 0])
        self.grey, self.temp = pal.rgb[g, 0][o].astype(np.float32), pal.temp[g][o]
        self.pal = pal
        probe = np.repeat(np.arange(0, 256, dtype=np.uint8)[:, None, None], 3, axis=2)   # every grey, as a 256x1 image
        lut, fine = pal(probe)[:, 0], np.interp(np.arange(256, dtype=np.float32), self.grey, self.temp)
        on = (lut > -19.5) & (np.arange(256) >= 2) & (np.arange(256) <= 197)
        self.selfcheck_max_error_c = float(np.abs(lut[on] - fine[on]).max())
        if self.selfcheck_max_error_c > 1.6:          # a 4-count cube step is 2 K at most
            raise RuntimeError(f"grey ramp interpolation disagrees with the palette by {self.selfcheck_max_error_c:.1f} C")

    def __call__(self, a: np.ndarray) -> np.ndarray:
        t = self.pal(a).astype(np.float32)
        spread = a.max(-1).astype(np.int16) - a.min(-1).astype(np.int16)
        on = (t > -19.5) & (spread <= 8)
        grey = a.mean(-1, dtype=np.float32)
        t[on] = np.interp(grey[on], self.grey, self.temp)
        return t


def read_t(p: Path, pal) -> np.ndarray:
    """Temperature in C per pixel; nan where the render has a hole (exactly black)."""
    a = np.asarray(Image.open(p).convert("RGB"))
    t = pal(a).astype(np.float32)
    t[a.max(-1) <= 2] = np.nan
    return t


# ---------------------------------------------------------------- 1. basemap
def band_mean(frames: list[Path], lum: np.ndarray, lo_pct: float, hi_pct: float) -> np.ndarray:
    """Per pixel, the mean colour of the frames whose luminance lies in [lo_pct, hi_pct] of that pixel."""
    n, h, w = lum.shape
    k_lo, k_hi = int(n * lo_pct / 100), int(n * hi_pct / 100)
    part = np.partition(lum, (k_lo, k_hi), axis=0)   # stays uint8: np.percentile would make a float64 copy
    lo, hi = part[k_lo].copy(), part[k_hi].copy()
    del part
    acc = np.zeros((h, w, 3), np.float32)
    cnt = np.zeros((h, w), np.float32)
    for i, p in enumerate(frames):
        a = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
        m = (lum[i] >= lo) & (lum[i] <= hi)
        acc[m] += a[m]
        cnt[m] += 1
    return acc / np.maximum(cnt, 1)[..., None]


def build_basemap(geo_dir: Path, lon: float, dest: Path, land: np.ndarray, hours=(10, 14),
                  land_band=(10, 30), sea_band=(2, 8), sea_smooth=12.0) -> dict:
    frames = sorted(p for p in geo_dir.glob("*.jpg") if hours[0] <= solar(stamp(p), lon).hour < hours[1])
    if len(frames) < 50:
        raise SystemExit(f"only {len(frames)} GeoColor frames in solar {hours[0]}-{hours[1]}: not enough for a basemap")
    h, w = np.asarray(Image.open(frames[0])).shape[:2]
    lum = np.empty((len(frames), h, w), np.uint8)
    for i, p in enumerate(frames):
        a = np.asarray(Image.open(p).convert("RGB"))
        l = luminance(a).astype(np.uint8)
        l[a.max(-1) <= 2] = 255            # a hole must never be picked as the darkest frame
        lum[i] = l
    land_rgb = band_mean(frames, lum, *land_band)
    sea_rgb = band_mean(frames, lum, *sea_band)
    del lum
    # normalised convolution over the sea only, so the coast does not bleed land colour offshore
    sea = (~land).astype(np.float32)
    wsum = gaussian_filter(sea, sea_smooth)
    sea_rgb = np.stack([gaussian_filter(sea_rgb[..., c] * sea, sea_smooth) / np.maximum(wsum, 1e-3) for c in range(3)], -1)
    m = gaussian_filter(land.astype(np.float32), 1.0)[..., None]
    base = land_rgb * m + sea_rgb * (1 - m)
    dest.parent.mkdir(parents=True, exist_ok=True)
    base8 = np.clip(base, 0, 255).astype(np.uint8)
    Image.fromarray(base8).save(dest, quality=94)
    s = stats(base8)
    return dict(frames=len(frames), solar_hours=list(hours), land_band=list(land_band), sea_band=list(sea_band),
                sea_smooth_px=sea_smooth, residual_cloud_share=round(s["cloud"], 4), mean_luminance=round(s["mean"], 3))


# ---------------------------------------------------------------- 2. clear-sky reference
def build_tclear(ir_dir: Path, lon: float, pal, dest: Path, pct=90) -> dict:
    by_hour: dict[int, list[Path]] = defaultdict(list)
    for p in ir_dir.glob("*.jpg"):
        by_hour[solar(stamp(p), lon).hour].append(p)
    h, w = np.asarray(Image.open(next(iter(by_hour[0])))).shape[:2]
    tc = np.empty((24, h, w), np.float32)
    counts = []
    for hour in range(24):
        ps = sorted(by_hour[hour])
        stack = np.empty((len(ps), h, w), np.float32)
        for i, p in enumerate(ps):
            stack[i] = read_t(p, pal)
        # nanpercentile along an axis walks every column in Python (hours for 590K columns);
        # a hole sorted to the cold end cannot touch the 90th percentile unless a pixel is
        # missing in more than a tenth of its frames, which the QC'd archive does not have.
        stack = np.nan_to_num(stack, nan=-200.0)
        k = int(len(ps) * pct / 100)
        tc[hour] = np.partition(stack, k, axis=0)[k]
        counts.append(len(ps))
        del stack
    tc = np.stack([gaussian_filter(tc[i], 0.8) for i in range(24)])   # takes the JPEG grain off the reference
    np.save(dest, tc)
    return dict(percentile=pct, frames_per_hour=counts,
                sea_reference_c=[round(float(v), 1) for v in tc[:, h // 2, w // 8]],
                land_reference_c=[round(float(v), 1) for v in tc[:, h // 2, w * 7 // 8]])


# ---------------------------------------------------------------- 3. calibration
BINS = np.arange(-8.0, 61.0, 1.0)


def calibrate(pairs: list[tuple[datetime, Path, Path]], pal, tclear: np.ndarray, land: np.ndarray, lon: float) -> dict:
    """Mean GeoColor luminance per 1 K bin of d = T_clear - T, over sea and over land."""
    acc = {k: np.zeros((len(BINS) - 1, 3)) for k in ("sea", "land")}     # n, cloud, luminance
    for t, ir_p, geo_p in pairs:
        d = tclear[solar(t, lon).hour] - read_t(ir_p, pal)
        g = np.asarray(Image.open(geo_p).convert("RGB"))
        m, lum = cloud_mask(g), luminance(g.astype(np.float32) / 255)
        ok = np.isfinite(d)
        for k, mask in (("sea", ~land & ok), ("land", land & ok)):
            dd = d[mask]
            if k == "land":
                dd = dd - land_offset(d, land)
            idx = np.digitize(dd, BINS) - 1
            v = (idx >= 0) & (idx < len(BINS) - 1)
            np.add.at(acc[k][:, 0], idx[v], 1)
            np.add.at(acc[k][:, 1], idx[v], m[mask][v])
            np.add.at(acc[k][:, 2], idx[v], lum[mask][v])
    out = dict(stamps=[t.strftime("%Y-%m-%dT%H:%M:00Z") for t, _, _ in pairs], bin_edges_k=BINS.tolist())
    for k, a in acc.items():
        n = np.maximum(a[:, 0], 1)
        out[k] = dict(n=a[:, 0].astype(int).tolist(),
                      p_cloud=[round(float(v), 4) if c > 0 else None for v, c in zip(a[:, 1] / n, a[:, 0])],
                      luminance=[round(float(v), 4) if c > 0 else None for v, c in zip(a[:, 2] / n, a[:, 0])])
    return out


def land_offset(d: np.ndarray, land: np.ndarray) -> float:
    return float(np.clip(np.percentile(d[land], 20), 0, 12)) if land.any() else 0.0


class Curve:
    """d (K) -> opacity in [0, 1], from the calibrated mean luminance: 0 at d <= 0, 1 at the plateau."""
    def __init__(self, cal: dict, key: str, min_n=2000):
        c = np.array([np.nan if v is None else v for v in cal[key]["luminance"]], np.float64)
        n = np.array(cal[key]["n"])
        c[n < min_n] = np.nan
        mid = (BINS[:-1] + BINS[1:]) / 2
        ok = np.isfinite(c)
        mid, c = mid[ok], c[ok]
        # monotone envelope: the luminance never goes back down as the cloud gets colder
        c = np.maximum.accumulate(c)
        lo = c[np.searchsorted(mid, 0.5)]                 # the 0..1 K bin: clear
        hi = np.nanpercentile(c[mid >= 5], 50) if key == "sea" else c[-1]   # sea plateaus by 5 K
        self.x, self.y = mid, np.clip((c - lo) / max(hi - lo, 1e-3), 0, 1)
        self.y[mid <= 0.5] = 0.0
        self.lo, self.hi = float(lo), float(hi)

    def __call__(self, d: np.ndarray) -> np.ndarray:
        return np.interp(d, self.x, self.y)


# ---------------------------------------------------------------- 4. one frame
class Renderer:
    def __init__(self, base: np.ndarray, tclear: np.ndarray, land: np.ndarray, cal: dict, blur=0.8):
        self.base = base.astype(np.float32) / 255
        self.tclear, self.land, self.blur = tclear, land, blur
        self.sea_curve, self.land_curve = Curve(cal, "sea"), Curve(cal, "land")

    def opacity(self, t: np.ndarray, hour: int) -> tuple[np.ndarray, float]:
        d = self.tclear[hour] - t
        d = np.nan_to_num(d, nan=0.0)                  # a hole renders as clear ground, not as cloud
        off = land_offset(d, self.land)
        a = np.where(self.land, self.land_curve(d - off), self.sea_curve(d)).astype(np.float32)
        return gaussian_filter(a, self.blur), off

    def render(self, t: np.ndarray, hour: int) -> tuple[np.ndarray, float]:
        a, off = self.opacity(t, hour)
        tt = np.nan_to_num(t, nan=20.0)
        bright = 0.84 + 0.14 * np.clip(-tt / 40.0, 0, 1)
        cloud = CLOUD_RGB[None, None, :] * bright[..., None]
        shadow = ndshift(gaussian_filter(a, 3.0), (-3, 2), order=1, mode="nearest") * 0.30
        ground = self.base * (1 - shadow)[..., None]
        out = ground * (1 - a)[..., None] + cloud * a[..., None]
        return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), off


_JOB: dict = {}          # what a render worker needs; filled in main() before the pool forks


def _render_one(p: Path):
    pal, ren, out, lon = _JOB["pal"], _JOB["ren"], _JOB["out"], _JOB["lon"]
    t = read_t(p, pal)
    img, off = ren.render(t, solar(stamp(p), lon).hour)
    Image.fromarray(img).save(out / p.name, quality=90)
    return bool(np.isnan(t).any()), off


# ---------------------------------------------------------------- 5. control
def cloud_mask(a: np.ndarray) -> np.ndarray:
    f = a.astype(np.float32) / 255
    lum = luminance(f)
    mx, mn = f.max(-1), f.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    return (lum > 0.42) & (sat < 0.35)


def control(pairs: list[tuple[datetime, Path, Path]], land: np.ndarray, site: Path, strip_n=4) -> dict:
    rows = []
    sea = ~land
    for t, day_p, geo_p in pairs:
        g = np.asarray(Image.open(geo_p).convert("RGB"))
        d = np.asarray(Image.open(day_p).convert("RGB"))
        mg, md = cloud_mask(g), cloud_mask(d)
        rows.append(dict(
            t=t.strftime("%Y-%m-%dT%H:%M:00Z"), solar_hour=solar(t, LON_CUR).hour,
            geo_cloud=float(mg.mean()), day_cloud=float(md.mean()),
            agree=float((mg == md).mean()), agree_sea=float((mg == md)[sea].mean()), agree_land=float((mg == md)[land].mean()),
            hit=float(md[mg].mean()) if mg.any() else None,               # GeoColor cloud that always-day also shows
            hit_sea=float(md[mg & sea].mean()) if (mg & sea).any() else None,
            precision=float(mg[md].mean()) if md.any() else None))         # always-day cloud that GeoColor confirms

    def mean(k):
        v = [r[k] for r in rows if r[k] is not None]
        return round(float(np.mean(v)), 4) if v else None
    summary = {k: mean(k) for k in ("agree", "agree_sea", "agree_land", "hit", "hit_sea", "precision", "geo_cloud", "day_cloud")}
    summary["n"] = len(rows)
    # the strip: four pairs, GeoColor left, always-day right, spread across the sample
    picks = [pairs[i] for i in np.linspace(0, len(pairs) - 1, min(strip_n, len(pairs))).round().astype(int)]
    S, HEAD, COLS = 384, 22, 2                      # two pairs a row: a page is ~1100 px wide
    rows_n = (len(picks) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * (2 * S + 8) + 8, rows_n * (S + HEAD + 8) + 8), "#06111c")
    dr = ImageDraw.Draw(sheet)
    for i, (t, day_p, geo_p) in enumerate(picks):
        x, y = 8 + (i % COLS) * (2 * S + 8), 8 + (i // COLS) * (S + HEAD + 8)
        sheet.paste(Image.open(geo_p).convert("RGB").resize((S, S), Image.BOX), (x, y + HEAD))
        sheet.paste(Image.open(day_p).convert("RGB").resize((S, S), Image.BOX), (x + S, y + HEAD))
        dr.text((x + 2, y + 6), f"{solar(t, LON_CUR).strftime('%d %b %H:%M')} solar   GeoColor | always day", fill="#93a4b6")
    sheet.save(site / "control_strip.jpg", quality=88)
    return dict(summary=summary, pairs=rows, strip="control_strip.jpg",
                strip_pairs=[p[0].strftime("%Y-%m-%dT%H:%M:00Z") for p in picks])


@click.command()
@click.option("--dir", "ir_dir", required=True, type=click.Path(exists=True, path_type=Path), help="infrared frames")
@click.option("--place", required=True, help="which place: basemap / terrain / solar longitude follow it")
@click.option("--geo-dir", default=None, type=click.Path(path_type=Path), help="GeoColor frames; default data/goes/<place>_x3")
@click.option("--out", default=None, type=click.Path(path_type=Path), help="default data/goes/<place>_x3_day")
@click.option("--site", default=None, type=click.Path(path_type=Path), help="where control.json goes; default site/month/<out name>")
@click.option("--lon", default=None, type=float)
@click.option("--limit", default=0, type=int, help="render only this many frames (a spread across the month), for tuning")
@click.option("--sample", default=0, type=int, help="also write <site>/sample.jpg: this many (GeoColor, always-day) pairs, daytime")
@click.option("--control-n", default=20, type=int)
@click.option("--rebuild", is_flag=True, help="rebuild the cached basemap and clear-sky reference")
@click.option("--rebuild-basemap", is_flag=True, help="rebuild only the basemap (the reference takes minutes)")
@click.option("--force", is_flag=True, help="re-render frames that already exist")
@click.option("--recalibrate", is_flag=True, help="re-measure the opacity curve from 60 daytime pairs")
@click.option("--blur", default=0.8, type=float, help="px of blur on the opacity, to take the JPEG grain off the 1-2 K band")
@click.option("--seed", default=0, type=int)
@click.option("--workers", default=6, type=int, help="render processes")
def main(ir_dir, place, geo_dir, out, site, lon, limit, sample, control_n, rebuild, rebuild_basemap, force, recalibrate, blur, seed, workers):
    global LON_CUR
    lon = LON[place] if lon is None else lon
    LON_CUR = lon
    geo_dir = geo_dir or Path("data/goes") / f"{place}_x3"
    out = out or Path("data/goes") / f"{place}_x3_day"
    site = site or Path("site/month") / out.name
    out.mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True, exist_ok=True)
    base_p = Path("data/basemap") / f"{place}_day.jpg"
    tc_p = Path("data/basemap") / f"{place}_tclear.npy"
    meta_p = Path("data/basemap") / f"{place}_day.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() and not rebuild else {}

    pal0 = IRPalette(CMAP)
    pal = FineGrey(pal0)
    click.echo(f"palette: {len(pal0.temp)} colours kept, self-check {pal0.selfcheck_max_error_c:.1f} C; "
               f"grey-ramp interpolation self-check {pal.selfcheck_max_error_c:.1f} C")

    elev_p = Path("data/terrain") / f"{place}_elev.npy"
    if not elev_p.exists():
        raise SystemExit(f"{elev_p} missing: run scripts/fetch_terrain.py --place {place} first (the basemap needs the land mask)")
    land = np.load(elev_p) > 0
    if rebuild or rebuild_basemap or not base_p.exists():
        meta["basemap"] = build_basemap(geo_dir, lon, base_p, land)
        click.echo(f"basemap: {meta['basemap']}")
    if rebuild or not tc_p.exists():
        meta["tclear"] = build_tclear(ir_dir, lon, pal, tc_p)
        click.echo(f"clear-sky reference: sea by hour {meta['tclear']['sea_reference_c']}")
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(json.dumps(meta, indent=1))

    base = np.asarray(Image.open(base_p).convert("RGB"))
    tclear = np.load(tc_p)
    geo = {stamp(p): p for p in geo_dir.glob("*.jpg")}
    ir = {stamp(p): p for p in ir_dir.glob("*.jpg")}
    curve_p = Path("data/basemap") / f"{place}_curve.json"
    if rebuild or recalibrate or not curve_p.exists():
        both = sorted(t for t in ir if t in geo and 10 <= solar(t, lon).hour < 15)
        picks = [both[i] for i in np.linspace(0, len(both) - 1, 60).round().astype(int)]
        cal = calibrate([(t, ir[t], geo[t]) for t in picks], pal, tclear, land, lon)
        curve_p.write_text(json.dumps(cal, indent=1))
        click.echo(f"calibrated on {len(picks)} daytime pairs -> {curve_p}")
    cal = json.loads(curve_p.read_text())
    ren = Renderer(base, tclear, land, cal, blur)
    click.echo(f"opacity curve: sea clear {ren.sea_curve.lo:.3f} -> cloudy {ren.sea_curve.hi:.3f} luminance, "
               f"half at {np.interp(0.5, ren.sea_curve.y, ren.sea_curve.x):.1f} K; land half at {np.interp(0.5, ren.land_curve.y, ren.land_curve.x):.1f} K")

    frames = sorted(ir_dir.glob("*.jpg"), key=stamp)
    if limit:
        frames = [frames[i] for i in np.linspace(0, len(frames) - 1, limit).round().astype(int)]
    todo = [p for p in frames if force or not (out / p.name).exists()]
    skipped = len(frames) - len(todo)
    done = holes = 0
    offs = []

    _JOB.update(pal=pal, ren=ren, out=out, lon=lon)
    one = _render_one

    # a frame is ~0.4 s of numpy (three blurs, a percentile, JPEG both ways); fork shares the
    # basemap, reference and curve with the workers without pickling them
    if workers > 1 and len(todo) > 8:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers) as pool:
            for hole, off in pool.imap(one, todo, chunksize=8):
                holes += hole; offs.append(off); done += 1
                if done % 500 == 0:
                    click.echo(f"  {done} rendered")
    else:
        for p in todo:
            hole, off = one(p)
            holes += hole; offs.append(off); done += 1
    click.echo(f"{out}: {done} rendered, {skipped} already there, {holes} frames with a hole; "
               f"land offset median {np.median(offs) if offs else 0:.1f} K, max {max(offs) if offs else 0:.1f} K")

    # ---- control: random daytime pairs where both layers have the exact stamp
    day = {stamp(p): p for p in out.glob("*.jpg")}
    used = {datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ") for x in cal["stamps"]}
    both = sorted(t for t in day if t in geo and t not in used and 9 <= solar(t, lon).hour < 16)
    rng = random.Random(seed)
    pairs = [(t, day[t], geo[t]) for t in sorted(rng.sample(both, min(control_n, len(both))))]
    if pairs:
        c = control(pairs, land, site)
        c["params"] = dict(blur=blur, seed=seed, daytime_pairs_available=len(both), calibration_pairs=len(cal["stamps"]))
        (site / "control.json").write_text(json.dumps(c, indent=1))
        click.echo(f"control ({len(pairs)} daytime pairs): {c['summary']}")
    if sample and both:
        picks = [both[i] for i in np.linspace(0, len(both) - 1, sample).round().astype(int)]
        S = 512
        sheet = Image.new("RGB", (2 * S, S * len(picks)))
        for i, t in enumerate(picks):
            sheet.paste(Image.open(geo[t]).convert("RGB").resize((S, S), Image.BOX), (0, i * S))
            sheet.paste(Image.open(day[t]).convert("RGB").resize((S, S), Image.BOX), (S, i * S))
        sheet.save(site / "sample.jpg", quality=88)
        click.echo(f"sample.jpg: {[t.strftime('%d %b %H:%M') for t in picks]} UTC")


if __name__ == "__main__":
    main()
