"""Encode the eclipse page's media from the reprojected frames.

PRIOR ART: scripts/make_timelapse.py (encode) and scripts/interpolate.py (rife_encode) do the
encoding; this only chooses the frames, the crops and the pairings for site/eclipse/.

    python scripts/eclipse_media.py --dir data/eclipse_v2 --out site/eclipse
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from interpolate import rife_encode  # noqa: E402
from make_timelapse import encode  # noqa: E402


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


@click.command()
@click.option("--dir", "src", default="data/eclipse_v2", type=click.Path(exists=True, path_type=Path))
@click.option("--out", default="site/eclipse", type=click.Path(path_type=Path))
@click.option("--width", default=1600, type=int)
@click.option("--peak", default="2026-08-12T173021Z", help="frame of deepest shadow for the stills")
@click.option("--before", default="2026-08-12T163021Z")
@click.option("--crop", default="0.22,0.05,0.68,0.42", help="fractional left,top,right,bottom of the frame for the Greenland crop")
@click.option("--skip-smooth", is_flag=True)
@click.option("--native-dir", default=None, type=click.Path(path_type=Path), help="frames in the satellite's own projection for eclipse_native.mp4")
def main(src, out, width, peak, before, crop, skip_smooth, native_dir):
    out.mkdir(parents=True, exist_ok=True)
    norm = sorted((src / "truecolor_norm").glob("*.jpg"))
    raw = sorted((src / "truecolor").glob("*.jpg"))
    click.echo(f"{len(norm)} frames")
    click.echo(encode(norm, out / "eclipse.mp4", fps=6, scale=None, crf=22, width=width))
    click.echo(encode(raw, out / "eclipse_raw.mp4", fps=6, scale=None, crf=22, width=width))
    if not skip_smooth:
        click.echo(rife_encode(norm, out / "eclipse_smooth.mp4", factor=4, fps=24, width=width, crf=22))

    p = Image.open(src / "truecolor_norm" / f"{peak}.jpg")
    p.resize((width, int(p.height * width / p.width)), Image.LANCZOS).save(out / "poster.jpg", quality=85)

    ir = Image.open(src / "ir" / f"{peak}.jpg").convert("RGB")
    hw = width // 2
    pair = Image.new("RGB", (hw * 2 + 8, int(p.height * hw / p.width)), (6, 17, 28))
    pair.paste(label(p.resize((hw, pair.height), Image.LANCZOS), f"{peak[11:13]}:{peak[13:15]} UTC  true colour, normalised"), (0, 0))
    pair.paste(label(ir.resize((hw, pair.height), Image.LANCZOS), f"{peak[11:13]}:{peak[13:15]} UTC  band 13, cloud-top temperature"), (hw + 8, 0))
    pair.save(out / f"pair_{peak[11:15]}.jpg", quality=88)

    l, t, r, b = (float(v) for v in crop.split(","))
    for name in (before, peak):
        im = Image.open(src / "truecolor_norm" / f"{name}.jpg")
        box = (int(im.width * l), int(im.height * t), int(im.width * r), int(im.height * b))
        c = im.crop(box)
        c = c.resize((width // 2 * 2, int(c.height * width / c.width)), Image.LANCZOS)
        label(c, f"{name[11:13]}:{name[13:15]} UTC").save(out / f"greenland_{name[11:15]}.jpg", quality=88)
    if native_dir:
        nat = sorted((native_dir / "truecolor_norm").glob("*.jpg"))
        click.echo(rife_encode(nat, out / "eclipse_native.mp4", factor=4, fps=24, width=width, crf=22))
        n0 = Image.open(nat[len(nat) // 2])
        n0.resize((width, int(n0.height * width / n0.width)), Image.LANCZOS).save(out / "native_poster.jpg", quality=85)
    click.echo("done")


if __name__ == "__main__":
    main()
