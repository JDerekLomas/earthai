"""Small side-by-side JPEG of a training run: real tiles, the start net, the latest fakes.

Crops the top-left corner of the big stylegan snapshot grids (they are ~7680x4320) so the
result is a few tens of KB and can be committed and read on a phone. Run it on the GPU box
and copy the JPEG down; never move the full-size PNGs.

    python scripts/compare_run.py --run runs/sg2-land256/00000-stylegan2-land256-gpus1-batch32-gamma1
"""
import json
import re
from pathlib import Path

import click
from PIL import Image, ImageDraw

BAR = 16  # label bar height, in output pixels


def crop_corner(path: Path, cols: int, rows: int, cell: int) -> Image.Image:
    im = Image.open(path)
    return im.crop((0, 0, min(cols * cell, im.width), min(rows * cell, im.height)))


@click.command()
@click.option("--run", required=True, type=click.Path(exists=True, path_type=Path), help="run dir holding reals.png and fakes*.png")
@click.option("--out", type=click.Path(path_type=Path), help="output JPEG (default docs/<run-name>_compare_<kimg>.jpg)")
@click.option("--cols", default=5, type=int)
@click.option("--rows", default=2, type=int)
@click.option("--cell", default=256, type=int, help="tile size in the source grids")
@click.option("--width", default=640, type=int, help="output width")
@click.option("--quality", default=70, type=int)
def main(run, out, cols, rows, cell, width, quality):
    fakes = sorted(p for p in run.glob("fakes*.png") if p.name != "fakes_init.png")
    if not fakes:
        raise SystemExit(f"no fakes*.png in {run} yet (only fakes_init.png is written at startup)")
    latest = fakes[-1]
    kimg = int(re.search(r"(\d+)", latest.stem).group(1))

    opts = json.loads((run / "training_options.json").read_text())
    resume = opts.get("resume_pkl")
    start = f"start: {Path(resume).stem.split('-')[0]} net 0 kimg" if resume else "start: random init 0 kimg"

    panels = [(run / "reals.png", "real tiles"), (run / "fakes_init.png", start), (latest, f"after {kimg} kimg")]
    panels = [(crop_corner(p, cols, rows, cell), label) for p, label in panels if p.exists()]

    scale = width / panels[0][0].width
    ph = round(panels[0][0].height * scale)
    sheet = Image.new("RGB", (width, len(panels) * ph))
    for i, (im, _) in enumerate(panels):
        sheet.paste(im.resize((width, ph), Image.LANCZOS), (0, i * ph))

    draw = ImageDraw.Draw(sheet)
    for i, (_, label) in enumerate(panels):
        w = draw.textlength(label) + 8
        draw.rectangle([0, i * ph, w, i * ph + BAR], fill="black")
        draw.text((4, i * ph + 3), label, fill="white")

    out = out or Path("docs") / f"{run.parent.name}_compare_{kimg}kimg.jpg"
    out.parent.mkdir(exist_ok=True)
    sheet.save(out, quality=quality)
    click.echo(f"{out}  {out.stat().st_size // 1024} KB  ({', '.join(l for _, l in panels)})")


if __name__ == "__main__":
    main()
