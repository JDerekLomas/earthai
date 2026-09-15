"""The 12 August 2026 eclipse again, from Meteosat-12 (MTG-I1) at 0 degrees, through EUMETSAT's open WMS.

PRIOR ART: scripts/fetch_goes_aws.py does the same afternoon from GOES-East out of NOAA's netCDF
archive, where the radiances come as numbers; scripts/fetch_earth.py::wms_image already reads
EUMETView, but one layer at one instant, into a globe mosaic. This walks the afternoon at the
ten-minute cadence for four MTG layers, normalises the 0.6 um band by the sun's height the way the
GOES script does, and writes the eclipse page's Meteosat media (clips, stills, curves).

    python scripts/fetch_eclipse_meteosat.py fetch                      # 14:00-20:30 UTC into data/eclipse_mtg
    python scripts/fetch_eclipse_meteosat.py fetch --stills --layers truecolour,vis06 --start 2026-08-12T16:40 --end 2026-08-12T17:40
    python scripts/fetch_eclipse_meteosat.py encode                     # site/eclipse/mtg_*

What EUMETView serves, keyless, for MTG-I1 (measured 15 Sep 2026; GetCapabilities lists years of
archive at PT10M for each):
    mtg_fd:rgb_truecolour   EUMETSAT's true-colour RGB. Already divided by the sun's height and cut
                            to transparent at night, and its haze/blue correction assumes full sun,
                            so the eclipse shadow comes out as a red-orange bruise, not black.
    mtg_fd:vis06_hrfi       the 0.6 um band as served, grey, 0.5 km native. NOT sun-normalised: the
                            Sahara dims all afternoon in step with cos(solar zenith). The honest
                            quantity for a brightness curve; the shadow is plain darkness.
    mtg_fd:ir105_hrfi       10.5 um thermal, grey style; cloud-top temperature, the "clouds did not
                            change" control. Whiter is colder.
    mtg_fd:rgb_geocolour    GeoColour blends a night rendering in where it thinks it is dusk, and
                            reads the shadow as dusk: city lights come on under the umbra. A curiosity.

The WMS renders a styled 8-bit raster, not the reflectance, so the grey-to-reflectance mapping of
vis06 is calibrated here against cos(solar zenith) over a cloud-free desert box before it is used
(encode prints the fitted exponent; ~1 means linear).

Frames are `<out>/<layer>/<UTC>.png` (RGBA as served; alpha 0 = no data: night or off-disk),
named like every other frame set here so make_timelapse.stamp() reads them.
"""
from __future__ import annotations

import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from fetch_goes_aws import cos_solar_zenith, normalise, to_u8  # noqa: E402

UA = {"User-Agent": "earthai-eclipse-mtg/0.1 (research; github.com/JDerekLomas/earthai)"}
WMS = ("https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
       "&layers={layer}&styles={style}&crs=EPSG:4326&bbox={s},{w},{n},{e}&width={W}&height={H}"
       "&format=image/png&transparent=true&time={t}")
LAYERS = {
    "truecolour": ("mtg_fd:rgb_truecolour", ""),
    "vis06": ("mtg_fd:vis06_hrfi", ""),
    "ir105": ("mtg_fd:ir105_hrfi", "mtg_fd:mtg_fd_ir105_hrfi_grayscale"),
    "geocolour": ("mtg_fd:rgb_geocolour", ""),
}
BBOX = (-60.0, 25.0, 40.0, 80.0)          # lon0, lat0, lon1, lat1 -- the same window as the GOES page
RES = 0.05                                # degrees per pixel: 2000 x 1100
ICELAND = (-45.0, 55.0, 5.0, 72.0)        # the stills: the umbra oval and its surroundings, 0.02 deg: 2500 x 850
STILL_RES = 0.02
# Boxes whose mean brightness is logged per frame (lon0, lat0, lon1, lat1). Greenland, Iceland and
# Iberia are the GOES page's boxes, so the two curves compare; Britain lay under a ~90% partial;
# the control is cloud-free Sahara, a flat Lambertian floor that only the sun's height should move.
BOXES = {
    "greenland": (-50, 64, -30, 76),
    "iceland": (-25, 63, -13, 67),
    "britain": (-8, 50, 2, 59),
    "iberia": (-10, 37, 3, 44),
    "control_sahara": (-2, 24.5, 10, 30),
}


def get(url: str, timeout=240, tries=3) -> bytes | None:
    for k in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200 and r.content[:2] == b"\x89P":
                return r.content
            click.echo(f"   {r.status_code} {r.content[:120]!r}")
        except requests.RequestException as e:
            click.echo(f"   {type(e).__name__}")
        time.sleep(2 + 4 * k)
    return None


def wms_png(layer: str, t: datetime, bbox, res: float) -> Image.Image | None:
    key, style = LAYERS[layer]
    lon0, lat0, lon1, lat1 = bbox
    W, H = int(round((lon1 - lon0) / res)), int(round((lat1 - lat0) / res))
    url = WMS.format(layer=key, style=style, s=lat0, w=lon0, n=lat1, e=lon1, W=W, H=H, t=t.strftime("%Y-%m-%dT%H:%M:00Z"))
    raw = get(url)
    if raw is None:
        return None
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    if (np.asarray(im)[..., 3] > 0).mean() < 0.05:            # an empty slot is a fully transparent image
        return None
    return im


def instants(start: str, end: str, step_min: int = 10) -> list[datetime]:
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    out = []
    while t0 <= t1:
        out.append(t0); t0 += timedelta(minutes=step_min)
    return out


def name(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H%M%SZ")


@click.group()
def cli():
    pass


@cli.command()
@click.option("--start", default="2026-08-12T14:00", show_default=True)
@click.option("--end", default="2026-08-12T20:30", show_default=True)
@click.option("--layers", default="truecolour,vis06,ir105,geocolour", show_default=True)
@click.option("--out", default="data/eclipse_mtg", type=click.Path(path_type=Path), show_default=True)
@click.option("--workers", default=6, show_default=True)
@click.option("--stills", is_flag=True, help="the Iceland window at 0.01 deg into <out>/stills/<layer>/ instead of the wide frames")
def fetch(start, end, layers, out, workers, stills):
    """Every ten-minute slot of the afternoon for each layer; slots already on disk are skipped."""
    bbox, res = (ICELAND, STILL_RES) if stills else (BBOX, RES)
    if stills:
        out = out / "stills"
    jobs = [(layer, t) for layer in layers.split(",") for t in instants(start, end)]
    for layer in layers.split(","):
        (out / layer).mkdir(parents=True, exist_ok=True)
    todo = [(l, t) for l, t in jobs if not (out / l / f"{name(t)}.png").exists()]
    click.echo(f"{len(jobs)} slots, {len(todo)} to fetch, {bbox} at {res} deg")

    def one(job):
        layer, t = job
        im = wms_png(layer, t, bbox, res)
        if im is None:
            return layer, t, None
        im.save(out / layer / f"{name(t)}.png", optimize=False)
        return layer, t, (np.asarray(im)[..., 3] > 0).mean()

    missing = []
    with ThreadPoolExecutor(workers) as pool:
        for layer, t, cov in pool.map(one, todo):
            if cov is None:
                missing.append((layer, name(t)))
                click.echo(f"   {layer:10s} {name(t)}  EMPTY")
            else:
                click.echo(f"   {layer:10s} {name(t)}  data {cov:.0%}")
    if missing:
        click.echo(f"{len(missing)} empty slots: " + ", ".join(f"{l}/{n}" for l, n in missing))
    click.echo("done")


# ---------------------------------------------------------------- encode

def grid(bbox, res):
    lon0, lat0, lon1, lat1 = bbox
    W, H = int(round((lon1 - lon0) / res)), int(round((lat1 - lat0) / res))
    lon = lon0 + (np.arange(W) + 0.5) * res
    lat = lat1 - (np.arange(H) + 0.5) * res
    return np.meshgrid(lon, lat)


def box_mask(lon, lat, box):
    lon0, lat0, lon1, lat1 = box
    return (lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1)


def on_black(im: Image.Image) -> Image.Image:
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    rgb = a[..., :3] * (a[..., 3:4] / 255.0)
    return Image.fromarray(rgb.round().astype(np.uint8))


def label(im: Image.Image, text: str) -> Image.Image:
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", max(18, im.width // 70))
    except OSError:
        f = ImageFont.load_default()
    x, y = 18, im.height - 18 - f.size
    d.rectangle([x - 8, y - 6, x + d.textlength(text, font=f) + 8, y + f.size + 6], fill=(6, 17, 28, 200))
    d.text((x, y), text, font=f, fill=(228, 236, 244))
    return im


def fit_gamma(greys, mus) -> float:
    """grey/255 = k * mu^g over the control box, by least squares in log space, using only frames
    with the sun well up (mu > 0.25). g ~ 1 means the style is linear in reflectance."""
    g = np.array(greys) / 255.0; m = np.array(mus)
    sel = (m > 0.25) & (g > 0.02)
    if sel.sum() < 3:
        return 1.0
    A = np.vstack([np.log(m[sel]), np.ones(sel.sum())]).T
    (slope, _), *_ = np.linalg.lstsq(A, np.log(g[sel]), rcond=None)
    return float(slope)


@cli.command()
@click.option("--src", default="data/eclipse_mtg", type=click.Path(exists=True, path_type=Path), show_default=True)
@click.option("--out", default="site/eclipse", type=click.Path(path_type=Path), show_default=True)
@click.option("--width", default=1600, show_default=True)
@click.option("--factor", default=8, show_default=True, help="RIFE in-between frames per real pair for the smooth clip")
@click.option("--fps-smooth", default=10, show_default=True, help="10 fps x 8 makes the 40-frame afternoon last ~30 s")
@click.option("--skip-smooth", is_flag=True)
@click.option("--peak", default=None, help="UTC HHMM of the Iceland still; default: the darkest frame of the Iceland box")
def encode(src, out, width, factor, fps_smooth, skip_smooth, peak):
    """Derived frame sets, the clips, the stills, the pair and mtg_curves.json."""
    from interpolate import rife_encode
    from make_timelapse import encode as ff_encode, stamp

    out.mkdir(parents=True, exist_ok=True)
    tc = sorted((src / "truecolour").glob("*.png"))
    vis = sorted((src / "vis06").glob("*.png"))
    ir = sorted((src / "ir105").glob("*.png"))
    ts = [stamp(p) for p in vis]
    click.echo(f"{len(tc)} true colour, {len(vis)} vis06, {len(ir)} ir105 frames")
    lon, lat = grid(BBOX, RES)
    masks = {k: box_mask(lon, lat, b) for k, b in BOXES.items()}

    # 1. the control box against the sun, to learn the vis06 style's tone curve
    raw_grey, raw_a, mus = [], [], []
    for p, t in zip(vis, ts):
        a = np.asarray(Image.open(p).convert("LA"))
        raw_grey.append(a[..., 0].astype(np.float32)); raw_a.append(a[..., 1] > 0)
        mus.append(cos_solar_zenith(t.replace(tzinfo=timezone.utc), lon, lat).astype(np.float32))
    ctrl = masks["control_sahara"]
    g_ctrl = [float(g[ctrl & a].mean()) if (ctrl & a).any() else 0.0 for g, a in zip(raw_grey, raw_a)]
    mu_ctrl = [float(m[ctrl].mean()) for m in mus]
    gamma = fit_gamma(g_ctrl, mu_ctrl)
    click.echo(f"vis06 tone curve over the Sahara box: grey ~ mu^{gamma:.2f}  (1 = linear)")

    # 2. normalised 0.6 um frames and the curves. Undo the tone curve, divide by cos(sza), gamma for display.
    nd = src / "vis06_norm"; nd.mkdir(exist_ok=True)
    td = src / "truecolour_black"; td.mkdir(exist_ok=True)
    rows = []
    for p, t, g, a, mu in zip(vis, ts, raw_grey, raw_a, mus):
        refl = np.power(g / 255.0, 1.0 / gamma) if abs(gamma - 1) > 0.05 else g / 255.0
        n = normalise(refl, mu)
        n[~a] = 0.0
        Image.fromarray(to_u8(np.power(np.clip(n, 0, 1), 1 / 2.2))).convert("RGB").save(nd / f"{name(t)}.jpg", quality=90)
        row = {"t": t.strftime("%H:%M")}
        for k, m in masks.items():
            sel = m & a
            row[k] = {"norm": round(float(n[sel].mean()), 4) if sel.any() else None,
                      "raw": round(float(g[sel].mean()) / 255, 4) if sel.any() else None,
                      "cos_sza": round(float(mu[m].mean()), 4)}
        rows.append(row)
    for p in tc:
        on_black(Image.open(p)).save(td / f"{p.stem}.jpg", quality=92)
    ir_rows = {}
    for p in ir:
        a = np.asarray(Image.open(p).convert("LA"))
        ir_rows[stamp(p).strftime("%H:%M")] = {k: round(float(a[..., 0][m & (a[..., 1] > 0)].mean()), 2) for k, m in masks.items()}
    for row in rows:
        for k in BOXES:
            row[k]["ir_grey"] = ir_rows.get(row["t"], {}).get(k)
    # The served vis06 is switched off progressively once the sun is under ~13 degrees (measured:
    # the Sahara control, flat to 17:00, falls from cos(sza) 0.23 down while the sun is still well
    # up). That fade is a function of the sun's height only, so the control gives it directly:
    # fade(mu) = control(mu) / control's plateau, and every box is divided by fade(its own mu).
    ctrl_n = np.array([r["control_sahara"]["norm"] or 0.0 for r in rows]); ctrl_mu = np.array([r["control_sahara"]["cos_sza"] for r in rows])
    plateau = float(ctrl_n[ctrl_mu > 0.3].mean())
    order = np.argsort(ctrl_mu)
    fade = lambda mu: float(np.clip(np.interp(mu, ctrl_mu[order], ctrl_n[order] / plateau), 0.02, 1.0)) if mu > 0.05 else None
    for r in rows:
        for k in BOXES:
            f = fade(r[k]["cos_sza"])
            r[k]["corrected"] = round(r[k]["norm"] / f, 4) if (f is not None and r[k]["norm"] is not None) else None
    (src / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    curves = {"times": [r["t"] for r in rows], "gamma": round(gamma, 3), "control_plateau": round(plateau, 4),
              "boxes": {k: {"norm": [r[k]["norm"] for r in rows], "corrected": [r[k]["corrected"] for r in rows],
                            "raw": [r[k]["raw"] for r in rows], "cos_sza": [r[k]["cos_sza"] for r in rows],
                            "ir_grey": [r[k]["ir_grey"] for r in rows]} for k in BOXES}}
    (out / "mtg_curves.json").write_text(json.dumps(curves))
    click.echo(f"control plateau {plateau:.3f}; served vis06 fade starts near cos(sza) {ctrl_mu[ctrl_n / plateau < 0.95].max() if (ctrl_n / plateau < 0.95).any() else 0:.2f}")
    for k in BOXES:
        n = curves["boxes"][k]["corrected"]; mu = curves["boxes"][k]["cos_sza"]
        base = max(v for v in n[:12] if v is not None)
        lit = [j for j in range(len(n)) if n[j] is not None and mu[j] > 0.12]
        i = min(lit, key=lambda j: n[j])
        click.echo(f"   {k:16s} morning {base:.3f}  darkest {n[i]:.3f} at {curves['times'][i]}  ({n[i] / base:.0%} left)")

    # 3. clips
    tcb = sorted(td.glob("*.jpg")); nb = sorted(nd.glob("*.jpg"))
    click.echo(ff_encode(tcb, out / "mtg.mp4", fps=6, scale=None, crf=22, width=width))
    click.echo(ff_encode(nb, out / "mtg_vis06.mp4", fps=6, scale=None, crf=22, width=width))
    if not skip_smooth:
        click.echo(rife_encode(tcb, out / "mtg_smooth.mp4", factor=factor, fps=fps_smooth, width=width, crf=22))
    p0 = Image.open(tcb[0])
    p0.resize((width, int(p0.height * width / p0.width)), Image.LANCZOS).save(out / "mtg_poster.jpg", quality=85)

    # 4. the pair at 17:30 (deepest over Greenland, matching the GOES pair) and the geocolour curiosity
    pk = "2026-08-12T173000Z"
    p = Image.open(td / f"{pk}.jpg"); i = on_black(Image.open(src / "ir105" / f"{pk}.png"))
    hw = width // 2
    pair = Image.new("RGB", (hw * 2 + 8, int(p.height * hw / p.width)), (6, 17, 28))
    pair.paste(label(p.resize((hw, pair.height), Image.LANCZOS), "17:30 UTC  true colour (EUMETSAT)"), (0, 0))
    pair.paste(label(i.resize((hw, pair.height), Image.LANCZOS), "17:30 UTC  10.5 um, cloud-top temperature"), (hw + 8, 0))
    pair.save(out / "mtg_pair_1730.jpg", quality=88)
    gp = src / "geocolour" / f"{pk}.png"
    if gp.exists():
        g = on_black(Image.open(gp)); g.resize((width, int(g.height * width / g.width)), Image.LANCZOS).save(out / "mtg_geocolour_1730.jpg", quality=86)

    # 5. Iceland stills from the 0.02-degree window, if fetched: an hour before, and at the darkest.
    # The 0.6 um still gets the same normalisation as the clip (own grid, same fitted tone curve).
    n_ice = curves["boxes"]["iceland"]["corrected"]
    i_dark = min((j for j in range(len(n_ice)) if n_ice[j] is not None), key=lambda j: n_ice[j])
    peak = peak or curves["times"][i_dark].replace(":", "")
    before = f"{int(peak[:2]) - 1:02d}{peak[2:]}"
    st = src / "stills"
    if st.exists():
        slon, slat = grid(ICELAND, STILL_RES)
        for hhmm in (before, peak):
            for layer in ("truecolour", "vis06"):
                q = st / layer / f"2026-08-12T{hhmm}00Z.png"
                if not q.exists():
                    click.echo(f"   no still {layer} {hhmm}"); continue
                if layer == "vis06":
                    a = np.asarray(Image.open(q).convert("LA"))
                    t = datetime(2026, 8, 12, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
                    refl = np.power(a[..., 0] / 255.0, 1.0 / gamma)
                    n = normalise(refl, cos_solar_zenith(t, slon, slat)); n[a[..., 1] == 0] = 0
                    im = Image.fromarray(to_u8(np.power(np.clip(n, 0, 1), 1 / 2.2))).convert("RGB")
                else:
                    im = on_black(Image.open(q))
                im = im.resize((width // 2 * 2, int(im.height * width / im.width)), Image.LANCZOS)
                label(im, f"{hhmm[:2]}:{hhmm[2:]} UTC  {'true colour (EUMETSAT)' if layer == 'truecolour' else '0.6 um, sun height normalised'}").save(out / f"mtg_iceland_{layer}_{hhmm}.jpg", quality=88)
    click.echo(f"Iceland darkest at {peak[:2]}:{peak[2:]}; stills at {before} and {peak}")
    click.echo("done")


if __name__ == "__main__":
    cli()
