"""Before/after for the official-products pipeline: the legacy tree (data/clouds) against the official tree
(data/clouds/official), frame by frame, plus the seam tables and crop plates.

PRIOR ART: scripts/seam_check.py measures one tree's seams from frames.jsonl; scripts/compare_run.py compares
GAN runs. This compares two pipeline trees of the same day: mean |delta opacity|, the fraction of cloud pixels
whose opacity moved by more than 0.2, the height field's change, and the seam table side by side.

    python scripts/official_compare.py --legacy data/clouds --official data/clouds/official --date 2026-09-12 \
        --plates docs/globe-2026-09-19-official
"""
from __future__ import annotations

import json
from pathlib import Path

import click
import numpy as np
from PIL import Image

W, H = 4096, 1504
# crops named as in the tiles report: (lon_min, lon_max, lat_min, lat_max), and the UTC hour they are looked at
CROPS = {"chile": (-100, -60, -45, -5, 17), "caribbean": (-95, -55, 5, 30, 17), "mid_atlantic": (-50, -10, -10, 30, 17),
         "dateline": (160, 200, -30, 20, 3), "africa": (-10, 50, -35, 35, 12)}


def frame(root: Path, slot: str) -> np.ndarray | None:
    p = root / "frames" / f"{slot}.png"
    return np.asarray(Image.open(p)).astype(np.float32) / 255.0 if p.exists() else None


def height(root: Path, slot: str) -> np.ndarray | None:
    p = root / "height" / f"{slot}.png"
    return np.asarray(Image.open(p)).astype(np.float32) / 16.0 if p.exists() else None


def seams(root: Path, date: str) -> dict:
    rows = [json.loads(l) for l in (root / "frames.jsonl").read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r["t"].startswith(date)]
    out = {}
    for r in rows:
        for k, v in r.get("seam", {}).items():
            out.setdefault(k, []).append((v["bias"], v["mad"]))
    return {k: {"bias": round(float(np.mean([a for a, _ in v])), 4), "mad": round(float(np.mean([b for _, b in v])), 4), "n": len(v)} for k, v in out.items()}


def crop(a: np.ndarray, box) -> np.ndarray:
    lon0, lon1, lat0, lat1, _ = box
    lat_max = H / W * 180.0
    c0, c1 = int((lon0 + 180) / 360 * W), int((lon1 + 180) / 360 * W)
    r0, r1 = int((lat_max - lat1) / (2 * lat_max) * H), int((lat_max - lat0) / (2 * lat_max) * H)
    cols = np.arange(c0, c1) % W
    return a[r0:r1][:, cols]


@click.command()
@click.option("--legacy", default="data/clouds", type=click.Path(path_type=Path))
@click.option("--official", default="data/clouds/official", type=click.Path(path_type=Path))
@click.option("--date", default="2026-09-12")
@click.option("--plates", default=None, type=click.Path(path_type=Path))
def main(legacy, official, date, plates):
    slots = sorted(p.stem for p in (official / "frames").glob(f"{date}*.png"))
    slots = [s for s in slots if (legacy / "frames" / f"{s}.png").exists()]
    click.echo(f"{len(slots)} common frames")
    dsum, n, moved, cloudy, hsum, hn, hmoved = 0.0, 0, 0, 0, 0.0, 0, 0
    per_hour = {}
    for s in slots:
        a, b = frame(legacy, s), frame(official, s)
        d = b - a
        dsum += float(np.abs(d).sum()); n += d.size
        cl = (a >= 0.1) | (b >= 0.1)
        cloudy += int(cl.sum()); moved += int((np.abs(d) > 0.2)[cl].sum())
        ha, hb = height(legacy, s), height(official, s)
        if ha is not None and hb is not None:
            hc = (ha > 0) & (hb > 0)
            hsum += float(np.abs(hb - ha)[hc].sum()); hn += int(hc.sum()); hmoved += int((np.abs(hb - ha) > 2)[hc].sum())
        per_hour.setdefault(s[11:13], []).append(float(np.abs(d).mean()))
    click.echo(f"opacity: mean |delta| {dsum / n:.4f} over every pixel; cloud pixels (op >= 0.1 in either) {cloudy / n / len(slots):.3f} of the grid, "
               f"of which {moved / cloudy:.3f} moved by more than 0.2")
    if hn:
        click.echo(f"height (where both have a top): mean |delta| {hsum / hn:.2f} km, {hmoved / hn:.3f} moved by more than 2 km")
    click.echo("mean |delta opacity| by UTC hour: " + " ".join(f"{h}:{np.mean(v):.3f}" for h, v in sorted(per_hour.items())))
    sl, so = seams(legacy, date), seams(official, date)
    click.echo("seams (mean |delta opacity| in the overlap, bias = first minus second):")
    click.echo(f"  {'pair':22s} {'legacy mad':>11s} {'bias':>8s}   {'official mad':>12s} {'bias':>8s}")
    for k in sorted(set(sl) | set(so)):
        a, b = sl.get(k), so.get(k)
        click.echo(f"  {k:22s} {a['mad'] if a else float('nan'):11.4f} {a['bias'] if a else float('nan'):+8.4f}   {b['mad'] if b else float('nan'):12.4f} {b['bias'] if b else float('nan'):+8.4f}")
    if plates:
        plates.mkdir(parents=True, exist_ok=True)
        for name, box in CROPS.items():
            s = f"{date}T{box[4]:02d}00Z"
            a, b = frame(legacy, s), frame(official, s)
            if a is None or b is None:
                continue
            ha, hb = height(legacy, s), height(official, s)
            ca, cb = crop(a, box), crop(b, box)
            row1 = np.concatenate([ca, cb, np.clip(0.5 + (cb - ca), 0, 1)], 1)
            rows = [row1]
            if ha is not None and hb is not None:
                rows.append(np.concatenate([crop(ha, box) / 16, crop(hb, box) / 16, np.clip(0.5 + (crop(hb, box) - crop(ha, box)) / 16, 0, 1)], 1))
            im = Image.fromarray((np.concatenate(rows, 0) * 255).astype(np.uint8))
            sc = max(1, 1800 // im.width)
            im = im.resize((im.width * sc, im.height * sc), Image.NEAREST) if sc > 1 else im
            im.save(plates / f"frames_{name}_{s[11:15]}z.png")
            click.echo(f"  plate {plates / f'frames_{name}_{s[11:15]}z.png'}  (legacy | official | difference; top opacity, bottom height)")
        # the whole disc at 17Z and 03Z
        for hh in (17, 3):
            s = f"{date}T{hh:02d}00Z"
            a, b = frame(legacy, s), frame(official, s)
            if a is None or b is None:
                continue
            im = Image.fromarray((np.concatenate([a, b], 0) * 255).astype(np.uint8)).resize((2048, 1504), Image.LANCZOS)
            im.save(plates / f"frames_world_{hh:02d}00z.jpg", quality=85)
            click.echo(f"  plate {plates / f'frames_world_{hh:02d}00z.jpg'} (legacy above, official below)")


if __name__ == "__main__":
    main()
