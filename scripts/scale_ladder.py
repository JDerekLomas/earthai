"""Same place, same 256 px tile, different ground footprints — the scale ladder.

A 256 px training tile is a window of 256 * (metres per pixel) on the ground, so the
footprint is a choice, not a property of the source: pick a zoom, or stitch tiles and
crop. This fetches one centred 256 px crop per (source, zoom) so the choice can be
looked at instead of argued about. The tiles feed site/index.html (deployed with vercel).

    python scripts/scale_ladder.py            # -> site/ladder/<place>/<source>_z<zoom>_<m per px>_<km>.jpg
"""
import io, math, sys
from pathlib import Path

import requests
from PIL import Image

UA = {"User-Agent": "earthai-scale-ladder/0.1 (research; github.com/JDerekLomas/earthai)"}
GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{day}/GoogleMapsCompatible_Level{lvl}/{z}/{y}/{x}.jpg"
EOX = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"

SOURCES = {
    "modis":   dict(label="MODIS Terra true colour (NASA GIBS)", native=250, zooms=[6, 7, 8, 9],
                    url=lambda z, x, y, day: GIBS.format(layer="MODIS_Terra_CorrectedReflectance_TrueColor", day=day, lvl=9, z=z, y=y, x=x)),
    "viirs":   dict(label="VIIRS SNPP true colour (NASA GIBS)", native=375, zooms=[8, 9],
                    url=lambda z, x, y, day: GIBS.format(layer="VIIRS_SNPP_CorrectedReflectance_TrueColor", day=day, lvl=9, z=z, y=y, x=x)),
    "s2":      dict(label="Sentinel-2 cloudless 2020 (EOX)", native=10, zooms=[10, 11, 12, 13, 14],
                    url=lambda z, x, y, day: EOX.format(z=z, y=y, x=x)),
}

def mpp(z, lat):  # metres per pixel at this zoom and latitude
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)

def global_px(lon, lat, z):
    n = 2 ** z * 256
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y

def centred_crop(src, z, lon, lat, day, size=256):
    """Stitch whatever tiles the window spans, then crop 256 px centred on the point."""
    px, py = global_px(lon, lat, z)
    x0, y0 = px - size / 2, py - size / 2
    tx0, ty0 = int(x0 // 256), int(y0 // 256)
    tx1, ty1 = int((x0 + size) // 256), int((y0 + size) // 256)
    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            r = requests.get(SOURCES[src]["url"](z, tx, ty, day), headers=UA, timeout=30)
            if r.status_code != 200 or len(r.content) < 500:
                return None
            canvas.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), ((tx - tx0) * 256, (ty - ty0) * 256))
    ox, oy = x0 - tx0 * 256, y0 - ty0 * 256
    return canvas.crop((int(ox), int(oy), int(ox) + size, int(oy) + size))

PLACES = [
    ("stratocumulus_namibia", -10.0, -22.0, "2020-09-12", "closed-cell stratocumulus off Namibia"),
    ("dunes_erg_chebbi",      6.5,  31.1,  "2020-06-15", "linear dunes, eastern Grand Erg"),
    ("delta_okavango",        22.9, -19.3, "2020-07-01", "Okavango inland delta, drainage"),
]

out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/ladder")
for name, lon, lat, day, _ in PLACES:
    for src, cfg in SOURCES.items():
        for z in cfg["zooms"]:
            m = mpp(z, lat)
            d = out / name
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{src}_z{z}_{m:.0f}mpp_{m*256/1000:.0f}km.jpg"
            if f.exists():
                continue
            try:
                im = centred_crop(src, z, lon, lat, day)
            except Exception as e:
                print(f"  {f.name}: {type(e).__name__}"); continue
            if im is None:
                print(f"  {f.name}: no tile"); continue
            im.save(f, quality=86)
            print(f"  {f.name}")
print("done")
