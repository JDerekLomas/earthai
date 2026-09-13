"""Pull time sequences from the geostationary satellites: the same place, every 10 minutes.

MODIS and VIIRS orbit, so they see a given place twice a day at whatever angle they happen
to have. GOES-East, GOES-West and Himawari hang over the equator and photograph the same
disk every ten minutes, which is the only free source that shows a sky BECOMING another
sky rather than a collection of unrelated skies.

    python scripts/fetch_goes.py --place gulf --hours 6 --out data/goes
    python scripts/fetch_goes.py --all --days 3 --stride 20 --out data/goes

Writes data/goes/<place>/<UTC timestamp>.jpg plus goes.jsonl, one row per frame, with the
frame's own cloud / black / contrast numbers and its position in its sequence. Rolling
archive: GIBS keeps roughly the last month at 10-minute cadence.
"""
from __future__ import annotations

import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402

UA = {"User-Agent": "earthai-goes/0.1 (research; github.com/JDerekLomas/earthai)"}
URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{t}/"
       "{tms}/{z}/{y}/{x}.png")

# Which rendering of the same sky. Measured 13 Sep 2026: all three serve every 10 minutes,
# day AND night, for at least the last 28 days.
#   geocolor  true colour by day; at night an infrared rendering over city lights -- a
#             different-looking picture, but the cloud field is continuous through dusk
#   ir        Band 13 clean infrared, cloud-top temperature: looks the same at noon and 2am
#   airmass   RGB composite of water-vapour and ozone channels; shows jet streaks, dry
#             intrusions and the air masses clouds form in, round the clock
LAYERS = {"geocolor": ("ABI_GeoColor", "GoogleMapsCompatible_Level7"),
          "ir": ("ABI_Band13_Clean_Infrared", "GoogleMapsCompatible_Level6"),
          "airmass": ("ABI_Air_Mass", "GoogleMapsCompatible_Level6")}


def layer_name(sat: str, layer: str) -> str:
    return SATS[sat].split("_ABI")[0] + "_" + LAYERS[layer][0]


def tms_for(layer_full: str) -> str:
    for suffix, tms in LAYERS.values():
        if layer_full.endswith(suffix):
            return tms
    return "GoogleMapsCompatible_Level7"

SATS = {
    "goes_east": "GOES-East_ABI_GeoColor",
    "goes_west": "GOES-West_ABI_GeoColor",
    "himawari": "Himawari_AHI_Band3_Red_Visible_1km",
}

# (satellite, zoom, tile x, tile y, the hours UTC when the sun is up there)
PLACES = {
    "gulf":         ("goes_east", 6, 17, 26, (13, 22)),   # Florida, Cuba, afternoon convection
    "amazon":       ("goes_east", 6, 19, 31, (12, 21)),   # ITCZ convection over the basin
    "atlantic_itcz":("goes_east", 6, 22, 29, (11, 20)),   # open ocean, trade cumulus
    "andes":        ("goes_east", 6, 17, 34, (13, 22)),   # orographic, strong diurnal cycle
    "california":   ("goes_west", 6, 9, 24, (16, 24)),    # the stratocumulus deck
    "peru":         ("goes_west", 6, 14, 33, (13, 22)),   # the other big deck
    "pacific_itcz": ("goes_west", 6, 5, 30, (17, 24)),    # deep convection, open ocean
}


def tile(layer, t, z, x, y):
    try:
        r = requests.get(URL.format(layer=layer, tms=tms_for(layer), t=t, z=z, y=y, x=x), headers=UA, timeout=40)
    except requests.RequestException:
        return None
    if r.status_code != 200 or len(r.content) < 900:
        return None
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def fetch(layer, t, z, x, y, span=1):
    """One frame. span>1 stitches a span x span block of neighbouring tiles around (x, y),
    because a single 256 px tile is too small to look at -- the sky is the subject, and at
    z6 one tile is a postage stamp of it. The named tile stays near the centre of the block."""
    if span == 1:
        return tile(layer, t, z, x, y)
    x0, y0 = x - span // 2, y - span // 2
    grid = [(dx, dy) for dy in range(span) for dx in range(span)]
    # the tiles of one frame are independent, and a tile is ~1 s; fetching them in sequence
    # made a 3x3 frame a 9 s operation and the whole archive an overnight job.
    with ThreadPoolExecutor(min(len(grid), 9)) as ex:
        ims = list(ex.map(lambda g: tile(layer, t, z, x0 + g[0], y0 + g[1]), grid))
    if any(im is None for im in ims):
        return None                  # a partial frame is a hole in the sky, not a frame
    canvas = Image.new("RGB", (256 * span, 256 * span))
    for (dx, dy), im in zip(grid, ims):
        canvas.paste(im, (256 * dx, 256 * dy))
    return canvas


@click.command()
@click.option("--place", default=None, help=f"one of: {', '.join(PLACES)}")
@click.option("--all", "all_places", is_flag=True)
@click.option("--days", default=2, type=int, help="how many days back to sample")
@click.option("--hours", default=None, type=int, help="instead: one continuous run of this many hours, ending now")
@click.option("--stride", default=10, type=int, help="minutes between frames (10 is the native cadence)")
@click.option("--out", default="data/goes", type=click.Path(path_type=Path))
@click.option("--workers", default=6, type=int)
@click.option("--layer", default="geocolor", type=click.Choice(list(LAYERS)),
              help="which rendering; non-geocolor layers get their own directory")
@click.option("--allday", is_flag=True,
              help="keep night frames too. Without it GeoColor is daylight-only, because its night "
                   "rendering is a different picture; the comparison timelapse wants both")
@click.option("--places", default=None, help="comma-separated subset of places")
@click.option("--span", default=1, type=int, help="stitch an NxN block of tiles per frame (3 = 768 px); costs N^2 requests")
@click.option("--max-black", default=0.01, type=float, help="drop frames with more pure-black (missing) pixels than this")
@click.option("--min-mean", default=0.18, type=float, help="drop night frames (GeoColor goes infrared after dark)")
def main(place, all_places, days, hours, stride, out, workers, max_black, min_mean, layer, allday, places, span):
    names = list(PLACES) if all_places else (places.split(",") if places else [place])
    if not names or names == [None]:
        raise SystemExit("give --place or --all")
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "goes.jsonl", "a")
    seen = {json.loads(l)["id"] for l in open(out / "goes.jsonl")} if (out / "goes.jsonl").exists() else set()

    for name in names:
        sat, z, x, y, (h0, h1) = PLACES[name]
        ltag = "" if layer == "geocolor" else f"_{layer}"
        dname = f"{name}{'' if span == 1 else f'_x{span}'}{ltag}"
        lfull = layer_name(sat, layer)
        (out / dname).mkdir(exist_ok=True)
        times = []
        if hours:
            # still daylight-only: GeoColor switches to infrared at night, which is a different
            # picture entirely and reads as missing data to a black-pixel filter
            times = [t for t in (now - timedelta(minutes=stride * k) for k in range(hours * 60 // stride))
                     if allday or h0 <= t.hour < h1]
        else:
            for d in range(days):
                day = now - timedelta(days=d)
                for h in (range(24) if allday else range(h0, min(h1, 24))):   # GeoColor goes IR at night
                    for m in range(0, 60, stride):
                        t = day.replace(hour=h, minute=m)
                        if t < now:
                            times.append(t)
        times = sorted(set(times))
        rows, kept = [], 0

        def one(t):
            ts = t.strftime("%Y-%m-%dT%H:%M:00Z")
            fid = f"{dname}_{ts.replace(':', '')}"
            f = out / dname / f"{ts.replace(':', '')}.jpg"
            if fid in seen or f.exists():
                return None
            im = fetch(lfull, ts, z, x, y, span)
            if im is None:
                return None
            s = stats(np.asarray(im))
            # s["nodata"] counts pixels that are EXACTLY black -- an actual hole. s["black"]
            # counts merely dark ones, which over deep ocean is 2-5% of a good daylight frame.
            # In all-day mode there is NO pixel test for a hole, and inventing one would be
            # worse than having none. Measured over four night frames: open Atlantic ocean at
            # night renders exactly (0,0,0) across an entire tile -- one read 1.00, 0.98, 0.92
            # -- which is pixel-for-pixel identical to a missing tile (positive control: a
            # blacked-out tile also reads 1.00). GIBS PNGs are fully opaque, so alpha carries
            # nothing either. The only real signal is the HTTP response, and tile() already
            # drops the whole frame when any tile 404s. Daylight mode keeps the pixel test,
            # where it works and where a black region really is a swath gap.
            if (not allday and (s["nodata"] > max_black or s["mean"] < min_mean)) or \
               (allday and s["nodata"] > 0.98):        # an entirely black FRAME is still junk

                return None
            im.save(f, quality=90)
            return dict(id=fid, place=name, sat=sat, layer=layer, t=ts, z=z, x=x, y=y, span=span,
                        **{k: round(v, 3) for k, v in s.items()})

        with ThreadPoolExecutor(workers) as ex:
            for row in ex.map(one, times):
                if row:
                    rows.append(row)
        rows.sort(key=lambda r: r["t"])
        for i, r in enumerate(rows):                        # position within its own run
            prev = rows[i - 1]["t"] if i else None
            gap = None
            if prev:
                dt = (datetime.strptime(r["t"], "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(prev, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60
                gap = int(dt)
            r["prev"] = prev
            r["gap_min"] = gap                              # None or >stride means a new run starts here
            log.write(json.dumps(r) + "\n")
            kept += 1
        log.flush()
        on_disk = len(list((out / dname).glob("*.jpg")))
        click.echo(f"{name:14} +{kept:5} new, {on_disk:6} on disk, of {len(times)} slots  ({sat})")
    total = sum(1 for _ in open(out / "goes.jsonl")) if (out / "goes.jsonl").exists() else 0
    click.echo(f"-> {out}   {total} frames total")


if __name__ == "__main__":
    main()
