"""Find straight lines in an image, and say how straight is too straight.

Built for two jobs. It does ONE of them; read this before quoting a number from it.

  1. Straight-line contamination in tiles -- WORKS. Run over 600 land tiles it ranks swath
     edges to the top, which is correct: the hard diagonal where an orbital swath ends is
     by far the straightest real feature in the archive. It found NO roads, and that is a
     finding rather than a failure: at z8 the tiles are ~600 m/px, so a road is well under
     one pixel. Road contamination is real at 10 m/px and absent here. It is also NOT a
     better nodata detector than counting black pixels -- corr(nodata, segment) = -0.017
     over 1,200 tiles, and only 5 of 1,157 gate-passing tiles score in the top 1%.
  2. Blend seams in a generated canvas -- DOES NOT WORK. It reports ~1.15 (no line) on a
     canvas whose region boundaries are plainly visible to the eye. The seam there is a
     boundary between two texture STATISTICS, gradual over ~50-100 px and running maybe a
     third of the canvas, inside an image already full of cloud edges at every angle. A
     Hough ridge, in luminance or in local high-frequency energy, does not represent it.
     Do not use this to claim a generated image is seam-free; that claim currently rests
     on a controlled visual comparison, and honestly saying so is better than citing a
     number from an instrument that cannot see the thing.

    python scripts/straight_check.py out/warp_straight.png out/warp_warp15.png
    python scripts/straight_check.py --glob 'data/tiles_land/**/*.jpg' --top 40

Plain numpy Hough over a gradient map; no cv2 on this box. `straightness` is the Hough
peak divided by the accumulator's own 99.5th percentile -- how much of an OUTLIER the best
line is among all candidate lines. Dimensionless, so a 2048 px canvas and a 256 px tile are
directly comparable. Roughly: under 1.5 no line, 2-3 a soft boundary, above 4 a hard one.

The controls passed at every stage while the instrument was still blind to case 2, which
is the lesson worth keeping: a synthetic control that spans the whole canvas does not test
a boundary that spans a third of it. A control has to match the SHAPE of the real input,
not just its kind.

--selftest prints the controls, and they are not optional. The first version of this scored
peak over line length, which measures how much EDGE an image has rather than whether it has
a line -- a smooth left-right ramp scored 1.12 against 0.43 for a real seam, and every
image reported 45 degrees, because a saturated edge map piles up on its own diagonals. The
controls caught that in one run. A drawn line and a soft seam must both outscore a ramp and
a noise field, and --selftest prints PASS or FAIL on exactly that.
"""
from __future__ import annotations

import glob as globmod
from pathlib import Path

import click
import numpy as np
from PIL import Image, ImageDraw


def texture_field(a: np.ndarray, win: int = 8) -> np.ndarray:
    """Local high-frequency energy, box-averaged over `win` pixels.

    The seam between two blended latents is usually NOT a brightness step -- both regions
    are cloud at the same exposure -- it is a change of TEXTURE: dense cells one side,
    smooth deck the other. A luminance-gradient detector is blind to that by construction,
    and reported "no line" on a canvas with an obvious vertical break. Running the same
    Hough over this map instead finds boundaries in texture."""
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    hp = np.abs(4 * lum[1:-1, 1:-1] - lum[:-2, 1:-1] - lum[2:, 1:-1] - lum[1:-1, :-2] - lum[1:-1, 2:])
    # box blur via a cumulative sum, so the window is cheap at 2048 px
    c = np.cumsum(np.cumsum(np.pad(hp, 1), 0), 1)
    h, w = hp.shape
    ys = np.clip(np.arange(h)[:, None] + np.array([-win // 2, win // 2])[None, :], 0, h)
    xs = np.clip(np.arange(w)[:, None] + np.array([-win // 2, win // 2])[None, :], 0, w)
    y0, y1 = ys[:, 0][:, None], ys[:, 1][:, None]
    x0, x1 = xs[:, 0][None, :], xs[:, 1][None, :]
    area = np.maximum((y1 - y0) * (x1 - x0), 1)
    box = (c[y1, x1] - c[y0, x1] - c[y1, x0] + c[y0, x0]) / area
    out = np.zeros_like(lum)
    out[1:-1, 1:-1] = box
    return out


def edges(a: np.ndarray, keep: float = 0.02) -> np.ndarray:
    """Gradient magnitude, reduced to the strongest `keep` fraction of pixels.

    Keeping everything above a fixed threshold was the bug that made the first version of
    this useless: a smooth left-to-right ramp has a strong gradient at EVERY pixel, so the
    edge map saturates, and a saturated square accumulates most along its own diagonals --
    every image then reported a 45-degree "line". Taking a fixed small FRACTION makes the
    number of voting pixels the same whether the image is a ramp or a cloud field, so the
    accumulator is comparing shape rather than density."""
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    gy = np.zeros_like(lum); gx = np.zeros_like(lum)
    gy[1:-1, :] = lum[2:, :] - lum[:-2, :]
    gx[:, 1:-1] = lum[:, 2:] - lum[:, :-2]
    m = np.hypot(gx, gy)
    # Non-maximum suppression across the gradient direction. Without it a smooth ramp --
    # which has a strong gradient at every pixel, over a wide band rather than on a ridge --
    # supplies a long contiguous run of "edge" and reads as a line. A real edge is THIN:
    # it is a local maximum along the direction it points. This is the difference between
    # "the image changes here" and "there is a line here".
    with np.errstate(invalid="ignore", divide="ignore"):
        ux = np.nan_to_num(gx / np.maximum(m, 1e-6))
        uy = np.nan_to_num(gy / np.maximum(m, 1e-6))
    h, w = m.shape
    yy, xx = np.indices(m.shape)
    def sample(dy, dx):
        y2 = np.clip((yy + np.rint(uy * dy)).astype(int), 0, h - 1)
        x2 = np.clip((xx + np.rint(ux * dx)).astype(int), 0, w - 1)
        return m[y2, x2]
    ridge = (m >= sample(1, 1)) & (m >= sample(-1, -1))
    m = np.where(ridge, m, 0.0)
    cut = np.percentile(m[m > 0], 100 * (1 - keep)) if (m > 0).any() else 0.0
    out = np.where(m >= cut, m, 0.0)
    return out / max(out.max(), 1e-6)


def hough(e: np.ndarray, n_theta: int = 180, rho_step: float = 1.0):
    """Standard linear Hough over the edge magnitude. Returns (accumulator, thetas, rhos)."""
    h, w = e.shape
    th = np.linspace(-np.pi / 2, np.pi / 2, n_theta, endpoint=False)
    cos, sin = np.cos(th), np.sin(th)
    diag = int(np.ceil(np.hypot(h, w)))
    rhos = np.arange(-diag, diag + 1, rho_step)
    ys, xs = np.nonzero(e > 0)                         # `edges` already kept only the top fraction
    if len(xs) == 0:
        return np.zeros((len(rhos), n_theta)), th, rhos
    wts = e[ys, xs]
    acc = np.zeros((len(rhos), n_theta), dtype=np.float32)
    # accumulate per angle: vectorised over points, looped over 180 angles
    for i in range(n_theta):
        r = xs * cos[i] + ys * sin[i]
        idx = np.clip(((r + diag) / rho_step).astype(np.int64), 0, len(rhos) - 1)
        np.add.at(acc[:, i], idx, wts)
    return acc, th, rhos


def score(im: Image.Image, keep: float = 0.02, mode: str = "lum") -> dict:
    """`straightness` = the Hough peak over the accumulator's own 99.5th percentile.

    Not peak over line-length: that measures how much edge the image has, which is why the
    ramp beat the seam. A LINE is an outlier in the accumulator -- one cell far above every
    other cell. A diffuse edge field produces a broad accumulator whose maximum is close to
    its own upper tail. The ratio asks the right question, and it is dimensionless, so a
    2048 px canvas and a 256 px tile are directly comparable."""
    a = np.asarray(im.convert("RGB")).astype(np.float32) / 255
    h, w = a.shape[:2]
    if mode == "texture":
        t = texture_field(a)
        t = (t - t.min()) / max(t.max() - t.min(), 1e-6)
        a = np.stack([t] * 3, -1)          # run the same edge+Hough over the texture map
    # Crop a margin before the Hough. The texture map has an invalid border (the window
    # runs off the image), and an invalid border is a perfect rectangle -- four ideal
    # straight lines that the detector duly found, at 0 degrees, in every single control
    # including the pure noise field. Any window-based field needs this.
    m = max(8, int(0.02 * min(h, w)))
    a = a[m:-m, m:-m]
    h, w = a.shape[:2]
    e = edges(a, keep)
    acc, th, rhos = hough(e)
    # a soft seam is spread over many rho bins; blur along rho by ~1% of the diagonal so it
    # accumulates as one line instead of twenty faint ones
    b = max(1, int(0.01 * np.hypot(h, w)))
    k = np.ones(2 * b + 1) / (2 * b + 1)
    acc = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, acc)

    j, i = np.unravel_index(int(np.argmax(acc)), acc.shape)
    peak = float(acc[j, i])
    bg = float(np.percentile(acc[acc > 0], 99.5)) if (acc > 0).any() else 0.0

    # A global peak-over-background cannot see a line that crosses only part of the canvas:
    # a region boundary in a blended generation runs maybe a third of the way before meeting
    # another region, and a third of a line is not an outlier among the thousands of candidate
    # full-length lines. The synthetic control missed this because it spanned the whole image
    # -- the control shared the instrument's blind spot. So also walk the best few candidate
    # lines and measure the longest CONTIGUOUS run of edge support along each.
    best_seg, best_ang = 0.0, 0.0
    flat = np.argsort(acc, axis=None)[::-1][:24]
    diag = float(np.hypot(h, w))
    for f in flat:
        jj, ii = np.unravel_index(int(f), acc.shape)
        theta, rho = float(th[ii]), float(rhos[jj])
        ct, st_ = np.cos(theta), np.sin(theta)
        n = int(diag)
        t = np.linspace(-diag, diag, 2 * n)
        xx = (rho * ct - t * st_).astype(int)
        yy = (rho * st_ + t * ct).astype(int)
        ok = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        if ok.sum() < 20:
            continue
        # support: the edge map within 1 px of the line, sampled along it
        v = e[yy[ok], xx[ok]]
        sup = v > 0
        # longest contiguous run, tolerating single-pixel gaps
        runs, cur = [], 0
        gap = 0
        for b in sup:
            if b:
                cur += 1; gap = 0
            else:
                gap += 1
                if gap > 2:
                    runs.append(cur); cur = 0
        runs.append(cur)
        seg = max(runs) / diag
        if seg > best_seg:
            best_seg, best_ang = seg, float(np.degrees(theta))

    return dict(straightness=round(peak / max(bg, 1e-9), 3),
                segment=round(best_seg, 3),
                angle_deg=round(float(np.degrees(th[i])), 1),
                seg_angle=round(best_ang, 1),
                rho=float(rhos[j]),
                mode=mode,
                px=f"{w}x{h}")


def selftest():
    """Controls. Without these a low score means nothing."""
    rng = np.random.RandomState(0)
    n = 512
    cases = []

    base = (rng.rand(n, n, 3) * 0.25 + 0.35)
    # a soft cloud-like field: low-frequency noise, no lines anywhere
    low = rng.rand(16, 16)
    cloud = np.asarray(Image.fromarray((low * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)).astype(np.float32) / 255
    cloudy = np.stack([cloud] * 3, -1) * 0.6 + base * 0.4
    cases.append(("noise + smooth field (must NOT fire)", Image.fromarray((cloudy * 255).astype(np.uint8))))

    # a pure left-right gradient: a strong EDGE everywhere but no line
    g = np.tile(np.linspace(0, 1, n), (n, 1))
    cases.append(("smooth gradient, no line (must NOT fire)", Image.fromarray((np.stack([g] * 3, -1) * 255).astype(np.uint8))))
    # the same ramp with a whisper of noise. A MATHEMATICALLY EXACT ramp has a gradient that
    # is equal at every pixel to the last bit, so non-maximum suppression cannot thin it --
    # every pixel ties with its neighbour and survives. That degenerate case is kept above
    # as a known limitation rather than deleted; this one is what a real image looks like,
    # and it is the one the verdict is computed on.
    gn = np.clip(g + rng.randn(n, n) * 0.004, 0, 1)
    cases.append(("noisy gradient, no line (must NOT fire)", Image.fromarray((np.stack([gn] * 3, -1) * 255).astype(np.uint8))))

    # the thing being detected: a soft straight boundary, as a blend seam would look
    half = (np.arange(n)[None, :] > n / 2).astype(np.float32)
    soft = np.asarray(Image.fromarray((half * 255).astype(np.uint8)).filter(
        __import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(12))).astype(np.float32) / 255
    seam = np.stack([soft] * 3, -1) * 0.5 + cloudy * 0.5
    cases.append(("SOFT straight seam in cloud (must fire)", Image.fromarray((seam * 255).astype(np.uint8))))

    hard = Image.fromarray((cloudy * 255).astype(np.uint8))
    d = ImageDraw.Draw(hard); d.line([(30, 20), (480, 470)], fill=(255, 255, 255), width=2)
    cases.append(("drawn hard line (must fire)", hard))

    # The control that matters, because it is the shape of the real input: two textures
    # meeting on a straight line with the SAME mean brightness. A luminance detector cannot
    # see this, which is exactly the blind spot that made the first pass report "no seam" on
    # a canvas with an obvious break.
    fine = rng.rand(n, n)
    coarse = np.asarray(Image.fromarray((rng.rand(32, 32) * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)).astype(np.float32) / 255
    fine = (fine - fine.mean()) * 0.25 + 0.5
    coarse = (coarse - coarse.mean()) * 0.25 + 0.5
    mask = (np.arange(n)[None, :] > n / 2).astype(np.float32)
    tex = fine * (1 - mask) + coarse * mask
    cases.append(("TEXTURE seam, equal brightness (must fire)", Image.fromarray((np.stack([tex] * 3, -1) * 255).astype(np.uint8))))

    # each mode is judged only on what it is for: `lum` finds brightness lines (roads),
    # `texture` finds texture boundaries (blend seams). Holding lum responsible for a
    # texture seam would be judging an instrument on a case it is not built for.
    responsible = {"lum": ("SOFT straight seam", "drawn hard line"),
                   "texture": ("TEXTURE seam", "drawn hard line")}
    for mode in ("lum", "texture"):
        click.echo(f"controls, mode={mode}:")
        out = {}
        for label, im in cases:
            s = score(im, mode=mode)
            out[label] = max(s["straightness"], 1.0 + 4 * s["segment"])
            duty = "" if any(label.startswith(x) for x in responsible[mode]) or "NOT" in label else "   (not this mode's job)"
            if label.startswith("smooth gradient"):
                duty = "   (degenerate: exactly-equal gradients defeat NMS)"
            click.echo(f"  {label:44} straight {s['straightness']:6.3f}  segment {s['segment']:5.3f}  "
                       f"angle {s['seg_angle']:+6.1f}deg{duty}")
        neg = max(v for k, v in out.items() if "NOT" in k and not k.startswith("smooth gradient"))
        pos = min(v for k, v in out.items() if any(k.startswith(x) for x in responsible[mode]))
        verdict = "PASS" if pos > neg * 1.3 else "FAIL"
        click.echo(f"  worst non-line {neg:.3f} vs weakest line it must catch {pos:.3f}  ->  {verdict}\n")


@click.command()
@click.argument("paths", nargs=-1)
@click.option("--glob", "pattern", default=None, help="shell glob instead of explicit paths")
@click.option("--top", default=0, type=int, help="with --glob, list only the N straightest")
@click.option("--selftest", "st", is_flag=True, help="print the controls and exit")
@click.option("--mode", default="texture", type=click.Choice(["lum", "texture"]),
              help="texture: find boundaries in local high-frequency energy (blend seams). "
                   "lum: find boundaries in brightness (roads, coastlines).")
def main(paths, pattern, top, st, mode):
    if st or (not paths and not pattern):
        selftest()
        if not paths and not pattern:
            return
    files = list(paths) + (sorted(globmod.glob(pattern, recursive=True)) if pattern else [])
    if not files:
        return
    rows = []
    with click.progressbar(files, label="scoring", show_pos=True) if len(files) > 20 else _null(files) as it:
        for f in it:
            try:
                rows.append((f, score(Image.open(f), mode=mode)))
            except Exception as e:
                click.echo(f"  {f}: {e}")
    rows.sort(key=lambda r: -max(r[1]["straightness"], 1.0 + 4 * r[1]["segment"]))
    shown = rows[:top] if top else rows
    click.echo(f"\n{'file':46} {'straight':>9} {'segment':>8} {'angle':>7} {'size':>10}")
    for f, s in shown:
        click.echo(f"{Path(f).name[:46]:46} {s['straightness']:>9.3f} {s['segment']:>8.3f} "
                   f"{s['seg_angle']:>6.1f}d {s['px']:>10}")
    if len(rows) > 1:
        v = np.array([s["segment"] for _, s in rows])
        click.echo(f"\n{len(rows)} images   segment: median {np.median(v):.3f}   p90 {np.percentile(v,90):.3f}   max {v.max():.3f}")


class _null:
    def __init__(self, x): self.x = x
    def __enter__(self): return self.x
    def __exit__(self, *a): return False


if __name__ == "__main__":
    main()
