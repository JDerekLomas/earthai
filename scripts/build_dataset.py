"""Filter raw GIBS tiles into a training set.

Drops: swath gaps / nodata (black pixels), nearly cloud-free ocean, near-uniform
tiles, and near-duplicates (perceptual hash). Writes 256 px PNGs to
data/dataset/ plus manifest.csv with an estimated cloud fraction per tile, which
later becomes a slider direction.

Usage:  python scripts/build_dataset.py --min-cloud 0.12 --max-black 0.01
"""
from __future__ import annotations

import csv
from pathlib import Path

import click
import numpy as np
from PIL import Image
from tqdm import tqdm


def stats(img: np.ndarray) -> dict:
    """img: HxWx3 uint8. Clouds over ocean are bright and unsaturated; ocean is
    dark and blue; nodata is near-black."""
    f = img.astype(np.float32) / 255
    lum = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    mx, mn = f.max(-1), f.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    black = float((lum < 0.04).mean())
    cloud = float(((lum > 0.42) & (sat < 0.35)).mean())
    # land: warm-hued (red >= blue), saturated, mid-brightness pixels. Ocean is
    # blue-dominant, cloud is unsaturated, so this isolates desert/vegetation.
    land = float(((f[..., 0] >= f[..., 2]) & (sat > 0.25) & (lum > 0.12) & (lum < 0.75)).mean())
    return {"black": black, "cloud": cloud, "land": land, "std": float(lum.std()), "mean": float(lum.mean())}


def dhash(img: Image.Image, size: int = 8) -> int:
    g = np.asarray(img.convert("L").resize((size + 1, size), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


@click.command()
@click.option("--tiles", default="data/tiles", type=click.Path(path_type=Path))
@click.option("--raw-manifest", default="data/manifest_raw.csv", type=click.Path(path_type=Path))
@click.option("--out", default="data/dataset", type=click.Path(path_type=Path))
@click.option("--manifest-out", default=None, type=click.Path(path_type=Path), help="default: <out parent>/manifest.csv")
@click.option("--size", default=256, type=int)
@click.option("--mode", default="clouds", type=click.Choice(["clouds", "land"]), help="land: keep cloud-free land instead (inverts the cloud and land filters)")
@click.option("--min-cloud", default=0.12, type=float, help="drop tiles with less cloud than this")
@click.option("--max-black", default=0.01, type=float, help="drop tiles with more nodata than this")
@click.option("--min-std", default=0.04, type=float, help="drop near-uniform tiles")
@click.option("--max-land", default=0.03, type=float, help="drop tiles with more land-coloured pixels than this")
@click.option("--hash-bits", default=4, type=int, help="max differing dhash bits to call a duplicate")
def main(tiles, raw_manifest, out, manifest_out, size, mode, min_cloud, max_black, min_std, max_land, hash_bits):
    if mode == "land":
        # keep tiles that are mostly land and mostly cloud-free; snow reads as cloud, so
        # allow some "cloud" fraction rather than losing every winter mountain tile
        max_cloud, min_cloud, min_land, max_land = 0.15, 0.0, 0.0, 1.0
    else:
        max_cloud, min_land = 1.0, 0.0
    rows = list(csv.DictReader(open(raw_manifest)))
    out.mkdir(parents=True, exist_ok=True)
    seen: list[int] = []
    kept, dropped = [], {"black": 0, "land": 0, "cloud": 0, "uniform": 0, "dup": 0, "missing": 0}
    for r in tqdm(rows, unit="tile"):
        p = tiles.parent / r["path"]
        if not p.exists():
            dropped["missing"] += 1
            continue
        im = Image.open(p).convert("RGB")
        if im.size != (size, size):
            im = im.resize((size, size), Image.LANCZOS)
        s = stats(np.asarray(im))
        if s["black"] > max_black:
            dropped["black"] += 1; continue
        if s["land"] > max_land or s["land"] < min_land:
            dropped["land"] += 1; continue
        if s["cloud"] < min_cloud or s["cloud"] > max_cloud:
            dropped["cloud"] += 1; continue
        if s["std"] < min_std:
            dropped["uniform"] += 1; continue
        h = dhash(im)
        if any(bin(h ^ o).count("1") <= hash_bits for o in seen[-5000:]):
            dropped["dup"] += 1; continue
        seen.append(h)
        name = Path(r["path"]).stem + ".png"
        im.save(out / name, optimize=True)
        kept.append({**r, "path": f"{out.name}/{name}", "cloud_frac": f"{s['cloud']:.3f}", "lum_mean": f"{s['mean']:.3f}", "lum_std": f"{s['std']:.3f}"})

    with open(manifest_out or out.parent / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(kept[0].keys()) if kept else ["path"])
        w.writeheader(); w.writerows(kept)
    click.echo(f"kept {len(kept)} / {len(rows)}   dropped: {dropped}")
    by = {}
    for k in kept:
        by[k["regime"]] = by.get(k["regime"], 0) + 1
    for k, v in sorted(by.items()):
        click.echo(f"  {k:20} {v}")


if __name__ == "__main__":
    main()
