"""Add the Landshapes crops (the /Users/dereklomas/earth project) to the tile-triage gallery.

Reads that project's manifest, samples the z13 crops two ways -- stratified per family,
and from the border bands just inside its build's hard exclusions, where a verdict
actually moves a threshold -- grades each 512 px original with its own auto_tone so the
review sees what training sees, and writes sprite sheets + rows in the gallery's format.
Run under that project's venv so the grader imports:

    /Users/dereklomas/earth/.venv/bin/python scripts/curate_landshapes.py --out /tmp/ls_bundle --sheet-offset 12

Read-only on the Landshapes tree; the only writes back are done later by export_rejects.py.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import click
from PIL import Image

CELL, PER_SHEET, COLS = 256, 100, 10
BANDS = [("white 0.30-0.70", "white", 0.30, 0.70), ("mean 32-48", "mean", 32, 48),
         ("deep_water 0.55-0.75", "deep_water", 0.55, 0.75), ("detail 3.0-4.5", "detail", 3.0, 4.5)]


@click.command()
@click.option("--root", default="/Users/dereklomas/earth", type=click.Path(path_type=Path))
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option("--zoom", default=13, type=int)
@click.option("--per-family", default=35, type=int)
@click.option("--per-band", default=50, type=int)
@click.option("--seed", default=13, type=int)
@click.option("--sheet-offset", default=0, type=int, help="first sheet number, to sit after the existing sheets")
@click.option("--grade/--raw", default=True)
@click.option("--previous", default=None, type=click.Path(path_type=Path), help="last build's tiles.landshapes.json: a crop keeps its id across rebuilds")
def main(root, out, zoom, per_family, per_band, seed, sheet_offset, grade, previous):
    rows = {}
    for line in open(root / "data/tiles/manifest.jsonl"):
        r = json.loads(line); rows[r["file"]] = r           # duplicate file -> last row wins
    z = [r for r in rows.values() if r.get("z") == zoom]
    rng = random.Random(seed)

    picked, why = [], {}
    fams = sorted({r["family"] for r in z} | {"coast"})
    for fam in fams:
        pool = [r for r in z if r["family"] == fam or (fam == "coast" and "coast" in r.get("tags", []))]
        pool = [r for r in pool if r["file"] not in why]
        for r in rng.sample(pool, min(per_family, len(pool))):
            why[r["file"]] = "stratified"; picked.append(r)
    for label, k, lo, hi in BANDS:
        pool = [r for r in z if r["file"] not in why and lo <= float(r.get(k, -1)) <= hi]
        for r in rng.sample(pool, min(per_band, len(pool))):
            why[r["file"]] = label; picked.append(r)
    click.echo(f"{len(z)} z{zoom} crops; sampled {len(picked)}: " + ", ".join(f"{b}={sum(1 for v in why.values() if v == b)}" for b in ["stratified"] + [b[0] for b in BANDS]))

    tone = None
    if grade:
        sys.path.insert(0, str(root))
        from landshapes.grade import auto_tone  # the build's own grader, default Grade
        tone = auto_tone
    click.echo("grading with auto_tone" if tone else "raw, ungraded")

    prior = {t["file"]: t["id"] for t in json.load(open(previous))} if previous else {}
    next_id = max([int(i[1:]) for i in prior.values()] + [-1]) + 1
    ids = {}
    for r in picked:
        if r["file"] in prior:
            ids[r["file"]] = prior[r["file"]]
        else:
            ids[r["file"]] = f"L{next_id:04d}"; next_id += 1
    if prior:
        kept_ids = sum(1 for r in picked if r["file"] in prior)
        click.echo(f"ids: {kept_ids} carried over from the previous build, {len(picked) - kept_ids} new, {len(prior) - kept_ids} previous crops no longer sampled")
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    (out / "full").mkdir(exist_ok=True)
    rng.shuffle(picked)
    tiles = []
    for si in range(0, len(picked), PER_SHEET):
        chunk = picked[si:si + PER_SHEET]
        sheet = Image.new("RGB", (COLS * CELL, math.ceil(len(chunk) / COLS) * CELL), (8, 12, 18))
        for i, r in enumerate(chunk):
            tid = ids[r["file"]]
            im = Image.open(root / "data/tiles" / r["file"]).convert("RGB")
            if tone:
                im = tone(im)
            im.save(out / "full" / f"{tid}.jpg", quality=82)
            sheet.paste(im.resize((CELL, CELL), Image.LANCZOS), ((i % COLS) * CELL, (i // COLS) * CELL))
            n = sheet_offset + si // PER_SHEET
            tiles.append(dict(id=tid, s=n, ix=i % COLS, iy=i // COLS, km=round(r["m_per_px"] * 512 / 1000), set="landshapes",
                              src="s2", z=zoom, reg=r["region"], rgm=r["family"], b=why[r["file"]], date="composite",
                              cf=round(float(r.get("white", 0)), 3), sd=round(float(r.get("std", 0)) / 255, 3),
                              lm=round(float(r.get("mean", 0)) / 255, 3), bk=round(float(r.get("nodata", 0)), 3),
                              f=f"full/{tid}.jpg", file=r["file"],
                              x={k: round(float(r[k]), 3) for k in ("white", "deep_water", "detail", "sat") if k in r}))
        n = sheet_offset + si // PER_SHEET
        sheet.save(out / "sheets" / f"sheet_{n:02d}.jpg", quality=80)
        sheet.resize((sheet.width // 4, sheet.height // 4), Image.LANCZOS).save(out / "sheets" / f"low_{n:02d}.jpg", quality=72)
    (out / "tiles.landshapes.json").write_text(json.dumps(tiles, separators=(",", ":")))
    click.echo(f"{len(tiles)} tiles, sheets {sheet_offset}..{sheet_offset + (len(picked) - 1) // PER_SHEET} -> {out}")


if __name__ == "__main__":
    main()
