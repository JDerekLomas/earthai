"""What kind of model does a place's sky need? Three measurements over a month of frames.

PRIOR ART: none -- scripts/seam_check.py and repeat_check.py measure generated images, and
compare_timelapse.py / month_sheet.py only render the archive. Nothing measured the archive's
own dynamics, which is what decides between a motion model, a conditioned model and a learned
one. The three analyses were first run ad hoc on 13-14 Sep 2026; this makes them repeatable
on the complete month and writes the numbers the month page shows.

    python scripts/sky_memory.py --dir data/goes/california_x3_ir --lon -122 --place california

1. MEMORY   correlation between frames a lag apart, after removing the solar-hour mean map
            (fixed geography AND the daily cycle), so what remains is weather. The e-folding
            time sets how many independent skies a month holds.
2. MOTION   persistence (the next frame is this frame) against advection (this frame pushed
            along its own measured motion). If advection wins big, the sky mostly MOVES and a
            cheap physics-style extrapolation carries the short range. If it barely wins, the
            sky mostly FORMS AND DISSOLVES in place, and a model needs the drivers -- time of
            day, surface, sea temperature -- not a better motion estimate.
            Positive control first: a real frame shifted rigidly by a known amount must be
            recovered exactly, or no advection number is trusted.
3. BUDGET   variance split, per surface type, into a fixed per-pixel map, the daily cycle
            (solar-hour map minus the fixed map) and the remainder. The first two are exactly
            what conditioning inputs could supply; the remainder is what a model must learn.

Infrared, not GeoColor: it is continuous through the night, and its daily cycle is not an
illumination change.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates, median_filter, zoom


def stamp(p: Path) -> datetime:
    return datetime.strptime(p.stem, "%Y-%m-%dT%H%M%SZ")


class Archive:
    def __init__(self, src: Path, n: int, lon: float):
        self.by = {stamp(p): p for p in src.glob("*.jpg")}
        self.times = sorted(self.by)
        self.n, self.lon, self._c = n, lon, {}
        hours = defaultdict(list)
        for t in self.times:
            hours[self.solar_hour(t)].append(t)
        self.hmean = {h: np.mean([self.load(t) for t in random.Random(h).sample(v, min(60, len(v)))], axis=0)
                      for h, v in hours.items()}

    def solar_hour(self, t):
        return int((t.hour + t.minute / 60 + self.lon / 15) % 24)

    def load(self, t):
        if t not in self._c:
            self._c[t] = np.asarray(Image.open(self.by[t]).convert("L").resize((self.n, self.n), Image.BOX)).astype(np.float32)
        return self._c[t]

    def anom(self, t):
        return self.load(t) - self.hmean[self.solar_hour(t)]


def corr(p, q):
    p = p - p.mean(); q = q - q.mean()
    return float((p * q).sum() / (np.sqrt((p * p).sum() * (q * q).sum()) + 1e-9))


# ---------------------------------------------------------------- 1. memory
def memory(ar: Archive, lags, per_lag=250):
    out = []
    for lag in lags:
        d = timedelta(minutes=lag)
        ts = [t for t in ar.times if t + d in ar.by]
        if len(ts) < 15:
            continue
        ts = random.Random(lag).sample(ts, min(per_lag, len(ts)))
        out.append((lag, len(ts), float(np.mean([corr(ar.anom(t), ar.anom(t + d)) for t in ts]))))
    c10 = out[0][2]
    tau = next((lag for lag, _, c in out if c < c10 / np.e), None)
    return out, tau


# ---------------------------------------------------------------- 2. motion
def make_flow(n, block=64, step=32):
    win = np.outer(np.hanning(block), np.hanning(block)).astype(np.float32)

    def shift(a, b):
        R = np.fft.fft2(b * win) * np.conj(np.fft.fft2(a * win)); R /= np.abs(R) + 1e-9
        c = np.abs(np.fft.ifft2(R)); iy, ix = np.unravel_index(np.argmax(c), c.shape)
        return (iy - block if iy > block // 2 else iy), (ix - block if ix > block // 2 else ix)

    def flow(a, b):
        ys = range(0, n - block + 1, step)
        fy = np.zeros((len(ys), len(ys)), np.float32); fx = np.zeros_like(fy)
        for i, y in enumerate(ys):
            for j, x in enumerate(ys):
                fy[i, j], fx[i, j] = shift(a[y:y + block, x:x + block], b[y:y + block, x:x + block])
        fy, fx = median_filter(fy, 3), median_filter(fx, 3)      # blocks can lock onto noise
        return zoom(fy, n / fy.shape[0], order=1), zoom(fx, n / fx.shape[0], order=1)

    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    advect = lambda a, fy, fx: map_coordinates(a, [yy - fy, xx - fx], order=1, mode="nearest")
    return flow, advect


def motion(ar: Archive, lags, km_per_px, per_lag=140, margin=24):
    flow, advect = make_flow(ar.n)
    m = margin
    base = ar.anom(ar.times[len(ar.times) // 2])
    f1, f2 = np.roll(base, (3, 5), (0, 1)), np.roll(base, (6, 10), (0, 1))
    fy, fx = flow(base, f1)
    ctl = dict(persistence=corr(f1[m:-m, m:-m], f2[m:-m, m:-m]),
               advection=corr(advect(f1, fy, fx)[m:-m, m:-m], f2[m:-m, m:-m]),
               recovered=[float(np.median(fy)), float(np.median(fx))], wanted=[3, 5])
    ctl["pass"] = bool(ctl["advection"] > ctl["persistence"] + 0.05 and abs(ctl["recovered"][1] - 5) < 1)
    rows = []
    for lag in lags:
        d = timedelta(minutes=lag)
        ts = [t for t in ar.times if t - d in ar.by and t + d in ar.by]
        if len(ts) < 15:
            continue
        ts = random.Random(lag).sample(ts, min(per_lag, len(ts)))
        P, A, S = [], [], []
        for t in ts:
            prev, now, nxt = ar.anom(t - d), ar.anom(t), ar.anom(t + d)
            fy, fx = flow(prev, now)
            P.append(corr(now[m:-m, m:-m], nxt[m:-m, m:-m]))
            A.append(corr(advect(now, fy, fx)[m:-m, m:-m], nxt[m:-m, m:-m]))
            S.append(float(np.hypot(np.median(fy), np.median(fx))))
        km = float(np.median(S)) * km_per_px
        rows.append(dict(lag=lag, n=len(ts), persistence=float(np.mean(P)), advection=float(np.mean(A)),
                         km_per_step=km, kmh=km / (lag / 60)))
    return ctl, rows


# ---------------------------------------------------------------- 3. budget
def budget(ar: Archive, land: np.ndarray):
    n = ar.n
    S1 = np.zeros((n, n)); S2 = np.zeros((n, n)); cnt = 0
    hn = defaultdict(int); hS = defaultdict(lambda: np.zeros((n, n)))
    for t in ar.times:
        x = ar.load(t).astype(np.float64); h = ar.solar_hour(t)
        S1 += x; S2 += x * x; cnt += 1; hn[h] += 1; hS[h] += x
    M = S1 / cnt
    H = {h: hS[h] / hn[h] for h in hn}
    out = {}
    for name, mask in (("ocean", ~land), ("land", land)):
        if not mask.any():
            continue
        g = S1[mask].sum() / (cnt * mask.sum())
        total = (S2[mask].sum() / cnt - 2 * g * S1[mask].sum() / cnt + g * g * mask.sum()) / mask.sum()
        static = ((M[mask] - g) ** 2).mean()
        diurnal = sum(hn[h] * ((H[h][mask] - M[mask]) ** 2).mean() for h in H) / cnt
        out[name] = dict(fixed_map=static / total, daily_cycle=diurnal / total,
                         weather=(total - static - diurnal) / total)
    return out


@click.command()
@click.option("--dir", "src", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--place", required=True, help="terrain name under data/terrain, for the land mask")
@click.option("--lon", default=-122.0, type=float)
@click.option("--px", default=192, type=int, help="analysis resolution")
@click.option("--m-per-px", default=1906.0, type=float, help="ground size of one frame pixel")
@click.option("--frame-px", default=768, type=int)
@click.option("--out", default=None, type=click.Path(path_type=Path))
def main(src, place, lon, px, m_per_px, frame_px, out):
    ar = Archive(src, px, lon)
    span_days = (ar.times[-1] - ar.times[0]).total_seconds() / 86400
    click.echo(f"{src}: {len(ar.times)} frames over {span_days:.1f} days\n")

    mem, tau = memory(ar, (10, 30, 60, 180, 360, 720, 1080, 1440, 2160, 2880, 4320))
    click.echo("1. MEMORY (weather anomaly correlation)")
    for lag, n, c in mem:
        click.echo(f"   {lag:>5} min  n={n:<4} {c:.3f}")
    click.echo(f"   e-folding {tau} min -> ~{24 * 60 / (2 * tau):.1f} independent skies per day\n" if tau else "   no e-fold\n")

    km_per_px = m_per_px * frame_px / px / 1000
    ctl, mot = motion(ar, (10, 30, 60, 120, 180, 360), km_per_px)
    click.echo(f"2. MOTION  control: persistence {ctl['persistence']:.3f} advection {ctl['advection']:.3f} "
               f"flow {ctl['recovered']} want {ctl['wanted']} -> {'PASS' if ctl['pass'] else 'FAIL'}")
    if not ctl["pass"]:
        click.echo("   control failed: advection numbers below are not trustworthy")
    for r in mot:
        click.echo(f"   {r['lag']:>4} min  persistence {r['persistence']:.3f}  advection {r['advection']:.3f}  "
                   f"gain {r['advection'] - r['persistence']:+.3f}  {r['kmh']:.0f} km/h")

    tpath = Path("data/terrain") / f"{place}_elev.npy"
    land = (np.asarray(Image.fromarray((np.load(tpath) >= 0).astype(np.uint8) * 255).resize((px, px), Image.NEAREST)) > 0
            if tpath.exists() else np.zeros((px, px), bool))
    bud = budget(ar, land)
    click.echo("\n3. BUDGET (share of variance)")
    for k, v in bud.items():
        click.echo(f"   {k:6} fixed map {v['fixed_map']:.1%}  daily cycle {v['daily_cycle']:.1%}  weather {v['weather']:.1%}")

    result = dict(source=src.name, frames=len(ar.times), days=round(span_days, 1),
                  memory=[dict(lag=l, n=n, corr=round(c, 3)) for l, n, c in mem], efold_min=tau,
                  motion_control=ctl, motion=[{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in mot],
                  budget={k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in bud.items()})
    out = out or Path("site/month") / f"{src.name}_memory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    click.echo(f"\n-> {out}")


if __name__ == "__main__":
    main()
