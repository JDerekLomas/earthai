"""The real night: one VIIRS Day/Night Band picture of the whole Earth per night, on a globe.

PRIOR ART: scripts/fetch_earth.py -- the geostationary mosaic, ten-minute cadence, day and
night, with the VIIRS city-lights MAP (2012) painted under its infrared zones. This is the
opposite kind of picture: the polar orbiter's own night, once per place, as it was. Imports
fetch_earth's tile fetcher and grid constants; edits nothing there.

    python scripts/fetch_night.py --nights 14                         # data/night/<date>.jpg + .mask.png + night.json
    python scripts/fetch_night.py --crop 2026-09-08 --bbox -150,38,-50,72 --level 5   # a still of the oval
    python scripts/fetch_night.py --encode site/night                 # copy what the page needs + manifest

Source (checked 16 Sep 2026): NASA GIBS epsg4326, daily, 500m TMS, png with transparency:
  VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance   primary   (2024-03-25 ..)
  VIIRS_SNPP_DayNightBand_At_Sensor_Radiance     gap fill  (gaps in its own record: 2026-07-11..15, 08-04..05)
NOAA-21's radiance layer is listed in the capabilities with NO dates and answers 404 for every
day tried, so it is not used. The stretched `VIIRS_*_DayNightBand` layers (1km) are prettier
at first glance but each swath carries its own contrast stretch, so the composite is scalloped
with bright swath edges; the radiance layers are one scale for the whole night. The palette is
a fixed compressive curve of radiance, 7 grey at 0 to 255 at 38 nW/(cm2 sr) (180 entries,
colormaps/v1.3/VIIRS_DayNightBand_At_Sensor_Radiance.xml). This script lifts it further with
a black point and a gamma (STRETCH) so moonlit cloud reads while city cores stay distinct.

What a night is. GIBS composites the passes that fall in one UTC day: a place is seen once,
near 01:30 local solar time, and the picture dated D holds, west of about 22 degrees E, the
small hours of D (the Americas, the Atlantic) and east of it the night that began on the
evening of D (Europe, Asia, Australia). Where the two ends of the day meet there is a sliver
one satellite did not cover, filled here from the other. Poleward of the terminator's
night-side limit (the Arctic in September) the band has no dark pass and the mask is 0.

Writes data/night/<date>.jpg (4096x2048, greyscale, the stretched radiance, black where no
data), <date>.mask.png (2048x1024: 255 NOAA-20, 128 filled from SNPP, 0 no data), and
night.json (per night: cover, fill share, Kp per 3 h, max Kp, moon illumination, partial).
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_earth import UA, get, gibs_tiles, COLS, ROWS, TILE  # noqa: E402

GIBS_L = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/{layer}/default/{t}/{tms}/{z}/{y}/{x}.png"
LAYERS = [("n20", "VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance"),
          ("snpp", "VIIRS_SNPP_DayNightBand_At_Sensor_Radiance")]
TMS = "500m"
KP_TXT = "https://services.swpc.noaa.gov/text/daily-geomagnetic-indices.txt"     # last 30 days, 8 planetary Kp per day, -1 = not yet
KP_JSON = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"   # last ~7 days, 3-hourly
STRETCH = dict(black=8, gamma=0.6)     # grey -> ((g - black) / (255 - black)) ** gamma; chosen by eye on 4 Sep (moonlit) and 14 Sep (new moon) 2026
STORM_KP = 5.0
NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
SYNODIC = 29.530588853


# ---------------------------------------------------------------- context
def moon_fraction(t: datetime) -> float:
    """Illuminated fraction of the Moon's disk, from the mean synodic month: within a day of the
    true phase, which for 'how much moonlight was there' is enough."""
    age = ((t - NEW_MOON).total_seconds() / 86400) % SYNODIC
    return round(float((1 - np.cos(2 * np.pi * age / SYNODIC)) / 2), 3)


def kp_by_day() -> dict[str, list[float | None]]:
    """{'YYYY-MM-DD': [8 x Kp or None]} from the SWPC 30-day text, patched from the 3-hourly JSON
    where the text still says -1 (today, and yesterday until the 12:30 UT issue)."""
    out: dict[str, list[float | None]] = {}
    txt = requests.get(KP_TXT, headers=UA, timeout=30).text
    for line in txt.splitlines():
        p = line.split()
        if len(p) >= 30 and p[0].isdigit() and len(p[0]) == 4:
            d = f"{p[0]}-{p[1]}-{p[2]}"
            vals = [float(v) for v in p[-8:]]
            out[d] = [v if v >= 0 else None for v in vals]
    try:
        js = requests.get(KP_JSON, headers=UA, timeout=30).json()
        for row in js:                       # [{'time_tag': '2026-09-09T00:00:00', 'Kp': 2.67, ...}, ...]
            if not isinstance(row, dict) or "Kp" not in row:
                continue
            t = datetime.fromisoformat(row["time_tag"]); d = t.strftime("%Y-%m-%d"); k = t.hour // 3
            out.setdefault(d, [None] * 8)
            if out[d][k] is None:
                out[d][k] = float(row["Kp"])
    except Exception as e:      # the JSON is a convenience; the text is the record
        click.echo(f"kp json: {e}")
    return out


# ---------------------------------------------------------------- tiles at any level
def level_grid(z: int):
    rows = ROWS * 2 ** (z - 3); return 2 * rows, rows, 180 / rows          # cols, rows, degrees per tile


def tiles_at(layer: str, t: str, z: int, tiles, workers=16) -> Image.Image:
    """RGBA canvas of the requested (x, y) tiles at level z; the rest transparent."""
    xs = [x for x, _ in tiles]; ys = [y for _, y in tiles]
    x0, y0 = min(xs), min(ys)
    canvas = Image.new("RGBA", (TILE * (max(xs) - x0 + 1), TILE * (max(ys) - y0 + 1)), (0, 0, 0, 0))
    def one(xy):
        x, y = xy
        raw = get(GIBS_L.format(layer=layer, t=t, tms=TMS, z=z, y=y, x=x))
        return xy, (Image.open(io.BytesIO(raw)).convert("RGBA") if raw else None)
    with ThreadPoolExecutor(workers) as ex:
        for (x, y), im in ex.map(one, tiles):
            if im is not None:
                canvas.paste(im, ((x - x0) * TILE, (y - y0) * TILE))
    return canvas


def bbox_tiles(z: int, w: float, s: float, e: float, n: float):
    cols, rows, deg = level_grid(z)
    x0, x1 = int((w + 180) // deg), int(np.ceil((e + 180) / deg)) - 1
    y0, y1 = int((90 - n) // deg), int(np.ceil((90 - s) / deg)) - 1
    x0, y0 = max(0, x0), max(0, y0)
    tiles = [(x, y) for y in range(y0, min(rows - 1, y1) + 1) for x in range(x0, min(cols - 1, x1) + 1)]
    return tiles, (x0, y0, deg)


# ---------------------------------------------------------------- the picture
def stretch(grey: np.ndarray) -> np.ndarray:
    b, g = STRETCH["black"], STRETCH["gamma"]
    x = np.clip((grey.astype(np.float32) - b) / (255 - b), 0, 1)
    return (255 * x ** g).astype(np.uint8)


def compose(layers: list[np.ndarray]):
    """First layer wins; each later one fills where everything before was transparent.
    Returns (grey uint8, mask uint8: 255 first layer, 128 second, ... 0 none, shares)."""
    H, W = layers[0].shape[:2]
    grey = np.zeros((H, W), np.uint8); mask = np.zeros((H, W), np.uint8); have = np.zeros((H, W), bool)
    shares = []
    for i, a in enumerate(layers):
        vis = (a[..., 3] > 0) & ~have
        grey[vis] = a[..., 0][vis]; mask[vis] = 255 if i == 0 else 128; have |= vis
        shares.append(float(vis.mean()))
    return grey, mask, shares


def fetch_night(d: str, out: Path, z: int, workers: int, W: int = 4096) -> dict:
    cols, rows, _ = level_grid(z)
    tiles = [(x, y) for y in range(rows) for x in range(cols)]
    arrs, timing = [], {}
    for key, layer in LAYERS:
        t0 = time.time()
        im = tiles_at(layer, d, z, tiles, workers)
        arrs.append(np.asarray(im)); timing[key] = round(time.time() - t0, 1)
    grey, mask, shares = compose(arrs)
    H = W // 2
    g = Image.fromarray(stretch(grey)).resize((W, H), Image.LANCZOS)
    g.save(out / f"{d}.jpg", quality=80, optimize=True)      # 4096x2048 grey, ~1.5 MB: the dark ocean is sensor noise and does not compress
    Image.fromarray(mask).resize((W // 2, H // 2), Image.NEAREST).save(out / f"{d}.mask.png", optimize=True)
    noon = datetime.fromisoformat(d).replace(hour=12, tzinfo=timezone.utc)
    row = dict(date=d, cover=round(sum(shares), 4), n20=round(shares[0], 4), snpp_fill=round(shares[1], 4),
               moon=moon_fraction(noon), fetch_s=timing, level=z)
    click.echo(f"{d}  cover {row['cover']:.3f}  (NOAA-20 {shares[0]:.3f} + SNPP {shares[1]:.3f})  moon {row['moon']:.2f}  {timing}", err=True)
    return row


# ---------------------------------------------------------------- cli
@click.command()
@click.option("--out", type=click.Path(path_type=Path), default=Path("data/night"))
@click.option("--nights", type=int, default=14, help="how many nights back from --end")
@click.option("--end", default=None, help="last night, YYYY-MM-DD (default: today UTC; a partial day is flagged, not dropped)")
@click.option("--level", "z", type=int, default=4, help="GIBS level: 3 = 5120 px wide source, 4 = 10240, 5 = 20480")
@click.option("--workers", type=int, default=16)
@click.option("--redo", is_flag=True, help="refetch nights already in night.json")
@click.option("--crop", default=None, help="date: write a still of --bbox at --level to <out>/crop-<date>.jpg")
@click.option("--bbox", default="-150,38,-50,72", help="w,s,e,n for --crop")
@click.option("--encode", type=click.Path(path_type=Path), default=None, help="site dir: copy the page's files there and write its night.json")
def main(out: Path, nights: int, end: str | None, z: int, workers: int, redo: bool, crop: str | None, bbox: str, encode: Path | None):
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "night.json"
    rows = {r["date"]: r for r in json.loads(manifest.read_text())} if manifest.exists() else {}

    if crop:
        w, s, e, n = (float(v) for v in bbox.split(","))
        tiles, (x0, y0, deg) = bbox_tiles(z, w, s, e, n)
        arrs = [np.asarray(tiles_at(layer, crop, z, tiles, workers)) for _, layer in LAYERS]
        grey, mask, shares = compose(arrs)
        px = TILE / deg
        X0, X1 = int((w + 180 - x0 * deg) * px), int((e + 180 - x0 * deg) * px)
        Y0, Y1 = int((90 - n - y0 * deg) * px), int((90 - s - y0 * deg) * px)
        im = Image.fromarray(stretch(grey[Y0:Y1, X0:X1]))
        p = out / f"crop-{crop}.jpg"; im.save(p, quality=90, optimize=True)
        click.echo(f"{p}  {im.size}  data {float((mask[Y0:Y1, X0:X1] > 0).mean()):.3f}")
        return

    if encode:
        encode.mkdir(parents=True, exist_ok=True)
        base = out / "_base.jpg"
        if not base.exists():
            all_tiles = [(x, y) for y in range(ROWS) for x in range(COLS)]
            gibs_tiles("BlueMarble_ShadedRelief_Bathymetry", "500m", "default", all_tiles, ext="jpeg", workers=workers) \
                .convert("RGB").resize((2048, 1024), Image.LANCZOS).save(base, quality=88)
        shutil.copy(base, encode / "base.jpg")
        good = [r for r in sorted(rows.values(), key=lambda r: r["date"]) if not r.get("partial")]
        for r in good:
            for suf in (".jpg", ".mask.png"):
                shutil.copy(out / f"{r['date']}{suf}", encode / f"{r['date']}{suf}")
        for p in sorted(out.glob("crop-*.jpg")):          # the page shows a crop at 2400 px wide; the full one stays in data/
            im = Image.open(p); im.resize((2400, round(2400 * im.size[1] / im.size[0])), Image.LANCZOS).save(encode / p.name, quality=85, optimize=True)
        bytes_ = sum((encode / f"{r['date']}.jpg").stat().st_size for r in good)
        man = dict(nights=[{k: r[k] for k in ("date", "cover", "n20", "snpp_fill", "moon", "kp", "kp_max", "storm")} for r in good],
                   stretch=STRETCH, storm_kp=STORM_KP, level=good[-1]["level"] if good else z, bytes=bytes_,
                   built=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   crops=[p.name for p in sorted(encode.glob("crop-*.jpg"))])
        (encode / "night.json").write_text(json.dumps(man, indent=1))
        click.echo(f"{len(good)} nights, {bytes_ / 1e6:.1f} MB -> {encode}")
        return

    last = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
    days = [(last - timedelta(days=i)).isoformat() for i in range(nights)][::-1]
    kp = kp_by_day()
    for d in days:
        if d not in rows or redo:
            rows[d] = fetch_night(d, out, z, workers)
        k = kp.get(d, [None] * 8)
        known = [v for v in k if v is not None]
        rows[d].update(kp=k, kp_max=max(known) if known else None, storm=bool(known) and max(known) >= STORM_KP)
        manifest.write_text(json.dumps(sorted(rows.values(), key=lambda r: r["date"]), indent=1))
    # a night still being assembled by GIBS (today) covers visibly less than the rest
    covers = sorted(r["cover"] for r in rows.values())
    med = covers[len(covers) // 2]
    for r in rows.values():
        r["partial"] = r["cover"] < 0.9 * med
    manifest.write_text(json.dumps(sorted(rows.values(), key=lambda r: r["date"]), indent=1))
    for r in sorted(rows.values(), key=lambda r: r["date"]):
        click.echo(f"{r['date']}  cover {r['cover']:.3f}  Kp max {r['kp_max']}  {'STORM' if r['storm'] else ''}  {'partial' if r['partial'] else ''}")


if __name__ == "__main__":
    main()
