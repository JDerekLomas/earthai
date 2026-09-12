"""Sample grid of dataset tiles per regime, for eyeballing. Writes docs/grid_<regime>.jpg"""
import csv, random
from pathlib import Path
import click
from PIL import Image


@click.command()
@click.option("--manifest", default="data/manifest.csv", type=click.Path(path_type=Path))
@click.option("--n", default=64, type=int)
@click.option("--cell", default=128, type=int)
@click.option("--out", default="docs", type=click.Path(path_type=Path))
def main(manifest, n, cell, out):
    rows = list(csv.DictReader(open(manifest)))
    out.mkdir(exist_ok=True)
    cols = int(n**0.5)
    for regime in sorted({r["regime"] for r in rows}):
        sub = random.Random(0).sample([r for r in rows if r["regime"] == regime], min(n, sum(r["regime"] == regime for r in rows)))
        sheet = Image.new("RGB", (cols * cell, ((len(sub) + cols - 1) // cols) * cell))
        for i, r in enumerate(sub):
            im = Image.open(manifest.parent / r["path"]).resize((cell, cell), Image.LANCZOS)
            sheet.paste(im, ((i % cols) * cell, (i // cols) * cell))
        sheet.save(out / f"grid_{regime}.jpg", quality=88)
        click.echo(f"{regime}: {len(sub)} tiles -> {out}/grid_{regime}.jpg")


if __name__ == "__main__":
    main()
