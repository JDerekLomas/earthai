"""Is a downsampled high-zoom fetch sharper than the native tile of the same ground?

The claim being tested: for a fixed ground footprint and a fixed 256 px output, fetching
NxN children at a deeper zoom and area-averaging them down beats fetching the one tile the
tile server renders at that footprint -- because the server's own coarse render is often an
upsample of something it already had, and because averaging suppresses the sensor noise and
JPEG ringing that a GAN would otherwise learn as texture.

    python scripts/supersample_check.py --source goes --z 6 --x 17 --y 26
    python scripts/supersample_check.py --source modis --z 6 --x 33 --y 30 --jpeg 85

The instrument is the radially averaged power spectrum, not a Laplacian variance: variance
rewards NOISE, and half the point here is that averaging removes noise, so a noise-loving
metric would score the blurry original higher for the wrong reason. `top_octave` is the
share of spectral energy above half Nyquist -- the band an upsampled image physically
cannot have.

Every run prints its own controls, because this metric is easy to fool:
  blur   -- a known gaussian over the best variant; the metric MUST fall
  up2x   -- that variant halved and re-enlarged; the metric MUST collapse
  sharp  -- unsharp mask over the native tile; if this scores like a real supersample,
            the metric is measuring acutance rather than resolved detail and the whole
            comparison is worthless.
"""
from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image, ImageFilter

UA = {"User-Agent": "earthai-supersample/0.1 (research; github.com/JDerekLomas/earthai)"}

SOURCES = {
    # name: (url template, deepest zoom that exists, a fixed time or None)
    "goes":  ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/"
              "{t}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png", 7, "2026-09-12T18:00:00Z"),
    "modis": ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/"
              "default/{t}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg", 9, "2026-09-10"),
    "viirs": ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/"
              "default/{t}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg", 9, "2026-09-10"),
    "s2":    ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/"
              "{z}/{y}/{x}.jpg", 14, None),
}


def fetch(tpl, t, z, x, y):
    try:
        r = requests.get(tpl.format(t=t, z=z, x=x, y=y), headers=UA, timeout=40)
    except requests.RequestException:
        return None
    if r.status_code != 200 or len(r.content) < 800:
        return None
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def mosaic(tpl, t, z, x0, y0, n):
    """The n x n block of tiles at zoom z covering the same ground as one tile at z-log2(n)."""
    grid = [(dx, dy) for dy in range(n) for dx in range(n)]
    with ThreadPoolExecutor(min(len(grid), 12)) as ex:
        ims = list(ex.map(lambda g: fetch(tpl, t, z, x0 + g[0], y0 + g[1]), grid))
    if any(i is None for i in ims):
        return None
    side = ims[0].width
    out = Image.new("RGB", (side * n, side * n))
    for (dx, dy), im in zip(grid, ims):
        out.paste(im, (side * dx, side * dy))
    return out


def radial_spectrum(a: np.ndarray) -> np.ndarray:
    """Radially averaged power spectrum of the luminance, indexed 0..N/2 in cycles/pixel."""
    g = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    g = g - g.mean()
    h, w = g.shape
    win = np.outer(np.hanning(h), np.hanning(w))       # or the tile edges ring across all bands
    P = np.abs(np.fft.fftshift(np.fft.fft2(g * win))) ** 2
    yy, xx = np.indices(P.shape)
    r = np.hypot(yy - h / 2, xx - w / 2).astype(int)
    n = min(h, w) // 2
    tot = np.bincount(r.ravel(), P.ravel())[:n]
    cnt = np.bincount(r.ravel())[:n]
    return tot / np.maximum(cnt, 1)


def sharpness(im: Image.Image) -> dict:
    a = np.asarray(im).astype(np.float32) / 255
    s = radial_spectrum(a)
    n = len(s)
    e = s / max(s.sum(), 1e-12)
    # share of energy in the top octave: an upsampled image physically cannot have this
    top = float(e[n // 2:].sum())
    mid = float(e[n // 4:n // 2].sum())
    # where the spectrum has fallen to 1% of its low-frequency level -- the effective cutoff
    low = float(s[1:max(2, n // 16)].mean())
    idx = np.where(s < low * 0.01)[0]
    cutoff = float(idx[0] / n) if len(idx) else 1.0
    return dict(top_octave=round(top, 5), mid_band=round(mid, 5), cutoff=round(cutoff, 3))


def area_down(im: Image.Image, to: int) -> Image.Image:
    """Box/area average, which is the operation that actually removes noise. LANCZOS would
    re-introduce ringing at the very frequencies being measured."""
    return im.resize((to, to), Image.BOX)


def jpeg_roundtrip(im: Image.Image, q: int):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    kb = len(buf.getvalue()) / 1024
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), kb


@click.command()
@click.option("--source", default="goes", type=click.Choice(list(SOURCES)))
@click.option("--z", "z0", default=6, type=int, help="the zoom whose single tile is the baseline")
@click.option("--x", "x0", required=True, type=int)
@click.option("--y", "y0", required=True, type=int)
@click.option("--out", default=256, type=int, help="output tile size every variant is reduced to")
@click.option("--time", "t", default=None, help="override the source's default timestamp")
@click.option("--jpeg", default=0, type=int, help="also report survival through JPEG at this quality")
@click.option("--save", default=None, type=click.Path(path_type=Path), help="write a side-by-side PNG")
def main(source, z0, x0, y0, out, t, jpeg, save):
    tpl, zmax, tdef = SOURCES[source]
    t = t or tdef
    rows = []
    variants = {}

    for k in range(0, zmax - z0 + 1):
        n, z = 2 ** k, z0 + k
        im = mosaic(tpl, t, z, x0 * n, y0 * n, n)
        if im is None:
            click.echo(f"  z{z} ({n}x{n}) unavailable, stopping the ladder here")
            break
        native = im.width
        small = im if native == out else area_down(im, out)
        name = "native" if k == 0 else f"x{n} from z{z}"
        variants[name] = small
        r = dict(variant=name, zoom=z, tiles=n * n, fetched_px=native, **sharpness(small))
        if jpeg:
            j, kb = jpeg_roundtrip(small, jpeg)
            r["jpeg_kb"] = round(kb, 1)
            r["jpeg_top_octave"] = sharpness(j)["top_octave"]
            r["jpeg_kept"] = round(r["jpeg_top_octave"] / max(r["top_octave"], 1e-9), 3)
        rows.append(r)

    if not rows:
        raise SystemExit("nothing fetched")

    best_name = rows[-1]["variant"]
    best, nat = variants[best_name], variants["native"]

    # ---- the honest instrument: the deepest fetch, area-averaged down, IS the reference.
    # Two blind sharpness numbers cannot tell resolved detail from an unsharp mask (see the
    # `sharp` control below, which scores 5x anything real while adding no information).
    # Measured against a reference, the question becomes answerable: how much of the true
    # signal at each frequency does this variant retain? That ratio is an MTF.
    ref = np.asarray(best).astype(np.float32) / 255
    sref = radial_spectrum(ref)
    for r, name in zip(rows, list(variants)):
        a = np.asarray(variants[name]).astype(np.float32) / 255
        n = len(sref)
        sv = radial_spectrum(a)
        band = lambda s_, lo, hi: float(s_[int(lo*n):int(hi*n)].sum())
        r["mtf_mid"] = round(np.sqrt(band(sv,.25,.5) / max(band(sref,.25,.5),1e-12)), 3)
        r["mtf_top"] = round(np.sqrt(band(sv,.5,1.) / max(band(sref,.5,1.),1e-12)), 3)
        d = a - ref
        r["psnr_vs_ref"] = round(float(10*np.log10(1.0/max((d**2).mean(),1e-12))), 2)

    # ---- controls. Without these the numbers above mean nothing. ----
    ctl = []
    ctl.append(("blur  (best, gaussian r=1.2)", sharpness(best.filter(ImageFilter.GaussianBlur(1.2)))))
    ctl.append(("up2x  (best, halved then enlarged)",
                sharpness(best.resize((out // 2, out // 2), Image.BOX).resize((out, out), Image.BICUBIC))))
    ctl.append(("sharp (native, unsharp mask)",
                sharpness(nat.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=0)))))

    w = max(len(r["variant"]) for r in rows) + 2
    click.echo(f"\n{source} z{z0}/{x0}/{y0}  t={t}  -> {out} px\n")
    hdr = (f"{'variant':{w}} {'zoom':>4} {'tiles':>6} {'fetched':>8} {'top_oct':>9} "
           f"{'MTF mid':>8} {'MTF top':>8} {'PSNR':>7}")
    if jpeg:
        hdr += f" {'jpeg KB':>8} {'kept':>6}"
    click.echo(hdr)
    base = rows[0]["top_octave"]
    for r in rows:
        line = (f"{r['variant']:{w}} {r['zoom']:>4} {r['tiles']:>6} {r['fetched_px']:>8} "
                f"{r['top_octave']:>9.5f} {r['mtf_mid']:>8.3f} {r['mtf_top']:>8.3f} "
                f"{r['psnr_vs_ref']:>7.2f}")
        if jpeg:
            line += f" {r['jpeg_kb']:>8.1f} {r['jpeg_kept']:>6.3f}"
        if r["variant"] == best_name:
            line += "   <- reference"
        click.echo(line)

    click.echo(f"\ncontrols (the metric must move the right way, or it is not measuring resolution)")
    for label, s in ctl:
        click.echo(f"  {label:36} top_oct {s['top_octave']:.5f}  cutoff {s['cutoff']:.3f}")
    click.echo(f"  {'best variant itself':36} top_oct {sharpness(best)['top_octave']:.5f}")
    click.echo(f"  {'native itself':36} top_oct {sharpness(nat)['top_octave']:.5f}")

    if save:
        sheet = Image.new("RGB", (out * len(variants), out))
        for i, (k, v) in enumerate(variants.items()):
            sheet.paste(v, (i * out, 0))
        save.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(save)
        click.echo(f"\n{' | '.join(variants)}  ->  {save}")
    click.echo("\n" + json.dumps(rows))


if __name__ == "__main__":
    main()
