"""Break kept scenes into training tiles -- the other half of fetch_scenes.py.

The window is the lever: --window 256 cuts native tiles, --window 512 cuts twice the
ground and shrinks it to 256 (same footprint as one zoom coarser, but from a scene a
human has approved), and several windows can be cut from the same scenes in one pass.
Crops go through the same black / cloud / uniform filters as build_dataset.py, so a
kept scene can still lose its swath corner.

    python scripts/cut_scenes.py --verdicts verdicts.json --window 256 --window 512 --stride 128

--verdicts is the JSON the triage page saves (its "v" map, scene id -> keep|reject);
without it every scene in scenes.jsonl is cut, which is the "no curation" baseline.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402


@click.command()
@click.option("--scenes", default="data/scenes", type=click.Path(path_type=Path))
@click.option("--verdicts", default=None, type=click.Path(path_type=Path), help="triage JSON; keep only scenes marked keep")
@click.option("--out", default="data/dataset_scenes", type=click.Path(path_type=Path))
@click.option("--window", "windows", multiple=True, default=[256], type=int, help="crop size in source px; repeatable")
@click.option("--stride", default=None, type=int, help="step between crops (default: half the window)")
@click.option("--size", default=256, type=int, help="output tile size")
@click.option("--mode", default="clouds", type=click.Choice(["clouds", "land"]))
@click.option("--min-cloud", default=0.12, type=float)
@click.option("--max-cloud", default=None, type=float)
@click.option("--max-black", default=0.01, type=float)
@click.option("--min-std", default=0.04, type=float)
def main(scenes, verdicts, out, windows, stride, size, mode, min_cloud, max_cloud, max_black, min_std):
    rows = [json.loads(l) for l in open(scenes / "scenes.jsonl")]
    if verdicts:
        v = json.load(open(verdicts)).get("v", {})
        rows = [r for r in rows if v.get(r["id"]) == "keep"]
        click.echo(f"{len(rows)} kept scenes")
    if mode == "land":
        lo, hi = 0.0, 0.15 if max_cloud is None else max_cloud
    else:
        lo, hi = min_cloud, 1.0 if max_cloud is None else max_cloud
    out.mkdir(parents=True, exist_ok=True)
    kept, dropped = [], {"black": 0, "cloud": 0, "uniform": 0}
    for r in rows:
        im = Image.open(scenes / f"{r['id']}.jpg").convert("RGB")
        for w in windows:
            st = stride or w // 2
            for y in range(0, im.height - w + 1, st):
                for x in range(0, im.width - w + 1, st):
                    crop = im.crop((x, y, x + w, y + w))
                    if w != size:
                        crop = crop.resize((size, size), Image.LANCZOS)
                    s = stats(np.asarray(crop))
                    if s["black"] > max_black:
                        dropped["black"] += 1; continue
                    if s["cloud"] < lo or s["cloud"] > hi:
                        dropped["cloud"] += 1; continue
                    if s["std"] < min_std:
                        dropped["uniform"] += 1; continue
                    name = f"{r['id']}_w{w}_{x}_{y}.png"
                    crop.save(out / name, optimize=True)
                    kept.append(dict(path=f"{out.name}/{name}", scene=r["id"], region=r["region"], regime=r["regime"], source=r["source"],
                                     zoom=r["zoom"], window=w, km=round(r["mpp"] * w / 1000), x=x, y=y,
                                     cloud_frac=f"{s['cloud']:.3f}", lum_mean=f"{s['mean']:.3f}", lum_std=f"{s['std']:.3f}"))
    with open(out.parent / f"{out.name}.manifest.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(kept[0].keys()) if kept else ["path"])
        wr.writeheader(); wr.writerows(kept)
    click.echo(f"{len(kept)} tiles from {len(rows)} scenes   dropped: {dropped}")
    by = {}
    for k in kept:
        by[(k["window"], k["regime"])] = by.get((k["window"], k["regime"]), 0) + 1
    for (w, g), n in sorted(by.items()):
        click.echo(f"  w{w:<5} {g:20} {n}")


if __name__ == "__main__":
    main()
