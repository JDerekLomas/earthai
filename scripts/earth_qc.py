"""Find the artifacts in the whole-Earth mosaics by measurement, then quarantine what cannot be fixed.

PRIOR ART: scripts/goes_qc.py judges ONE satellite's crop (white wedge, frozen frame, flash).
A mosaic is five satellites blended, so a defect is per ZONE and per SEAM: a zone going dark
for one frame reads as a black rectangle while the frame's mean barely moves. This script
reuses goes_qc's flash rule per zone and adds the coverage, seam and white-wedge rules.

    python scripts/earth_qc.py --dir data/earth              # report, write qc_flags.json
    python scripts/earth_qc.py --dir data/earth --apply      # move flagged frames to _qc_rejected/

Rules (every one is a measured threshold, no hand-picked frames):
  drop   a satellite's zone carried less of its usual data than its neighbours in time: cover
         (from earth.jsonl) below both neighbours by more than DROP_SHARE. A dropped GIBS tile
         block or a partly transparent WMS answer -- the blend falls back to the dimmed basemap
         there, so it reads as a dark rectangle or a bright/dark streak for one or two frames.
  flash  per zone: mean brightness jumps > FLASH_JUMP grey levels away from BOTH neighbours in
         the same direction while the neighbours agree within FLASH_AGREE (goes_qc's rule).
  white  a 64-px tile of the 1024x512 thumbnail, inside the satellite-covered area, more than
         WHITE_TILE_MAX pure white (min channel >= 253): a GIBS render wedge that slipped past
         the per-tile test in fetch_earth.
  seam   the brightness step across a seam line (mean of the band on one side minus the other)
         jumps > SEAM_JUMP levels away from both neighbours while they agree within FLASH_AGREE:
         one side of the seam changed and the other did not.
  block  a 32-px block of the thumbnail, inside the covered area, whose mean brightness sits
         more than BLOCK_JUMP levels from the median of the same block in the 6 surrounding
         frames while those 6 agree within BLOCK_AGREE: a rectangle that is there for one or
         two frames and not before or after (a dropped tile, a render wedge a quarter of a
         tile wide). A frame is flagged when at least BLOCK_MIN blocks fire. Added after the
         first pass: the 2026-09-12 13:50Z wedge (25% of one Band 13 tile) moved neither the
         zone mean nor the seam step enough to trip the rules above.
  hold   informational, from earth.jsonl: a satellite's previous picture was reused.

Zones and seams come from fetch_earth's weight maps, so they are exactly the blend's own.
Per-frame measurements are cached in <dir>/qc_stats.json (keyed by file name + size).
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_earth import SATS, grid, weight_map  # noqa: E402

TW, TH = 1024, 512
DROP_SHARE = 0.03
FLASH_JUMP, FLASH_AGREE = 6.0, 4.0
SEAM_JUMP = 6.0
WHITE_TILE_MAX = 0.05
BLOCK, BLOCK_JUMP, BLOCK_AGREE, BLOCK_MIN = 32, 25.0, 12.0, 2
ORDER = ["goes_west", "goes_east", "mtg", "iodc", "himawari"]     # west to east; each pair is a seam


class Zones:
    def __init__(self):
        lat, lon = grid(TW, TH)
        self.w = {k: weight_map(lat, lon, s["lon"]) for k, s in SATS.items()}
        stack = np.stack([self.w[k] for k in ORDER])
        self.covered = stack.sum(0) > 0
        dom = stack.argmax(0)
        self.zone = {k: (dom == i) & self.covered for i, k in enumerate(ORDER)}
        # seam bands: where a and b are the two strongest and within 0.35 of each other
        self.seams = {}
        for i, a in enumerate(ORDER):
            b = ORDER[(i + 1) % len(ORDER)]
            wa, wb = self.w[a], self.w[b]
            others = np.max(np.stack([self.w[k] for k in ORDER if k not in (a, b)]), 0)
            both = (wa > 0.05) & (wb > 0.05) & (np.maximum(wa, wb) > others)
            d = wa - wb
            self.seams[f"{a}|{b}"] = (both & (d > 0.05) & (d < 0.4), both & (d < -0.05) & (d > -0.4))


def stamp(p: Path) -> datetime:
    return datetime.strptime(p.stem, "%Y-%m-%dT%H%MZ")


def measure(p: Path, z: Zones) -> dict:
    im = Image.open(p).convert("RGB").resize((TW, TH), Image.BOX)
    a = np.asarray(im)
    g = a.astype(np.float32).mean(-1)
    pure = (a.min(-1) >= 253) & z.covered
    side = 64
    tiles = [float(pure[y:y + side, x:x + side].mean()) for y in range(0, TH, side) for x in range(0, TW, side)]
    bl = g.reshape(TH // BLOCK, BLOCK, TW // BLOCK, BLOCK).mean((1, 3))
    return dict(zone={k: float(g[m].mean()) for k, m in z.zone.items()},
                seam={k: float(g[l].mean() - g[r].mean()) for k, (l, r) in z.seams.items()},
                white_tile_max=max(tiles), blocks=np.round(bl, 1).tolist())


def flash_rule(series: list[float], jump: float) -> list[int]:
    """Indices whose value jumps away from both neighbours in the same direction by > jump
    while the neighbours agree within FLASH_AGREE."""
    out = []
    for i in range(1, len(series) - 1):
        d1, d2 = series[i] - series[i - 1], series[i] - series[i + 1]
        if min(abs(d1), abs(d2)) > jump and (d1 > 0) == (d2 > 0) and abs(series[i + 1] - series[i - 1]) < FLASH_AGREE:
            out.append(i)
    return out


def run(src: Path) -> tuple[list[Path], dict[Path, list[str]], dict]:
    frames = sorted(src.glob("????-??-??T????Z.jpg"), key=stamp)
    cache_p = src / "qc_stats.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    z = Zones()
    todo = [p for p in frames if cache.get(p.name, {}).get("bytes") != p.stat().st_size]
    if todo:
        with click.progressbar(todo, label="measuring") as it:
            for p in it:
                cache[p.name] = dict(measure(p, z), bytes=p.stat().st_size)
        cache_p.write_text(json.dumps(cache))
    rows = {}
    if (src / "earth.jsonl").exists():
        for line in (src / "earth.jsonl").read_text().splitlines():
            r = json.loads(line); rows[r["id"]] = r
    st = [cache[p.name] for p in frames]
    why: dict[Path, list[str]] = {p: [] for p in frames}
    # neighbours must be 10 minutes apart for the series rules
    contiguous = [i for i in range(1, len(frames) - 1)
                  if stamp(frames[i]) - stamp(frames[i - 1]) <= timedelta(minutes=10)
                  and stamp(frames[i + 1]) - stamp(frames[i]) <= timedelta(minutes=10)]
    cont = set(contiguous)
    # drop
    for i, p in enumerate(frames):
        r = rows.get(p.stem)
        if not r:
            continue
        for k, v in r["cover"].items():
            nb = [rows[q.stem]["cover"].get(k, 0) for q in (frames[i - 1:i] + frames[i + 1:i + 2]) if q.stem in rows]
            usual = max(nb) if nb else 1.0
            if v < usual - DROP_SHARE:
                why[p].append(f"drop: {k} cover {v:.3f} vs {usual:.3f}")
    # flash per zone
    for k in ORDER:
        s = [x["zone"][k] for x in st]
        for i in flash_rule(s, FLASH_JUMP):
            if i in cont:
                why[frames[i]].append(f"flash: {k} {s[i] - s[i - 1]:+.1f} vs previous, {s[i] - s[i + 1]:+.1f} vs next")
    # white wedge
    for p, x in zip(frames, st):
        if x["white_tile_max"] > WHITE_TILE_MAX:
            why[p].append(f"white: worst tile {x['white_tile_max']:.3f}")
    # seam jump
    for k in z.seams:
        s = [x["seam"][k] for x in st]
        for i in flash_rule(s, SEAM_JUMP):
            if i in cont:
                why[frames[i]].append(f"seam: {k} step {s[i]:+.1f} vs {s[i - 1]:+.1f}/{s[i + 1]:+.1f}")
    # block: against the median of the 6 surrounding frames, inside the covered area
    cov = z.covered.reshape(TH // BLOCK, BLOCK, TW // BLOCK, BLOCK).mean((1, 3)) > 0.5
    B = np.array([x["blocks"] for x in st], np.float32)
    for i in range(len(frames)):
        lo, hi = max(0, i - 3), min(len(frames), i + 4)
        others = np.concatenate([B[lo:i], B[i + 1:hi]])
        if len(others) < 4 or stamp(frames[hi - 1]) - stamp(frames[lo]) > timedelta(minutes=10 * (hi - lo)):
            continue
        med = np.median(others, 0); spread = others.max(0) - others.min(0)
        fired = (np.abs(B[i] - med) > BLOCK_JUMP) & (spread < BLOCK_AGREE) & cov
        if fired.sum() >= BLOCK_MIN:
            ys, xs = np.nonzero(fired)
            why[frames[i]].append(f"block: {int(fired.sum())} blocks, worst {float(np.abs(B[i] - med)[fired].max()):+.0f} levels near lon {xs.mean() * BLOCK * 360 / TW - 180:.0f} lat {90 - ys.mean() * BLOCK * 180 / TH:.0f}")
    holds = {p.stem: rows[p.stem]["held"] for p in frames if p.stem in rows and rows[p.stem].get("held")}
    return frames, {p: w for p, w in why.items() if w}, holds


@click.command()
@click.option("--dir", "src", default="data/earth", type=click.Path(exists=True, path_type=Path))
@click.option("--apply", is_flag=True, help="move flagged frames into _qc_rejected/ (default: report only)")
def main(src, apply):
    frames, flagged, holds = run(src)
    kinds: dict[str, int] = {}
    for ws in flagged.values():
        for w in ws:
            kinds[w.split(":")[0]] = kinds.get(w.split(":")[0], 0) + 1
    click.echo(f"{src}: {len(frames)} frames, {len(holds)} with a held source (informational)")
    click.echo(f"flagged {len(flagged)} of {len(frames)} ({len(flagged) / max(len(frames), 1):.1%}); reasons: {kinds}")
    for p, ws in sorted(flagged.items()):
        click.echo(f"  {p.name}  " + " | ".join(ws))
    (src / "qc_flags.json").write_text(json.dumps(
        dict(frames=len(frames), flagged={p.name: ws for p, ws in sorted(flagged.items())}, reasons=kinds, holds=holds), indent=1))
    if apply and flagged:
        q = src / "_qc_rejected"; q.mkdir(exist_ok=True)
        with open(q / "qc.jsonl", "a") as log:
            for p, ws in sorted(flagged.items()):
                shutil.move(str(p), q / p.name)
                log.write(json.dumps(dict(file=p.name, reason=" | ".join(ws), moved=datetime.utcnow().isoformat() + "Z")) + "\n")
        click.echo(f"moved {len(flagged)} into {q}/ (restore with mv; nothing deleted)")
    elif flagged:
        click.echo("report only -- rerun with --apply to quarantine")


if __name__ == "__main__":
    main()
