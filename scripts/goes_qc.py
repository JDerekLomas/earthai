"""Quarantine GOES frames that are broken renders rather than weather.

PRIOR ART: scripts/fetch_goes.py filters at fetch time (HTTP 404, and the pure-black hole test
in daylight mode) and scripts/gate_audit.py audits build_dataset's gates over tiles. Neither
catches this: a frame the server returned 200 for, with a straight-edged wedge of PURE WHITE
across it. The black-hole test cannot see white, and it is not a dataset gate problem.

    python scripts/goes_qc.py --dir data/goes/california_x3
    python scripts/goes_qc.py --dir data/goes/california_x3 --apply     # actually move them

Two defects, each with its own instrument:

  white wedge  share of pixels that are exactly white (min channel >= 253, allowing JPEG +-2),
               judged per z6 tile. Measured on California: the one known artifact frame
               (2026-09-11 23:00 UTC) reads 15% pure white with its worst tile at 49%. The
               six brightest real overcast frames in the whole set read exactly 0.0000 --
               real cloud in GeoColor never saturates to pure white; a broken render does.
  frozen frame a frame byte-identical in content to the one ten minutes before it. A stale
               repeat is not weather standing still, and it would quietly inflate every
               frame-to-frame persistence number computed from this archive.
  flash        a frame whose mean brightness jumps away from BOTH neighbours by more than 6
               grey levels in the same direction while the neighbours agree within 4: a single
               frame rendered in the wrong mode (GeoColor's day/night switch misfiring). Found
               16 in the California month (jumps up to 72 levels), none in infrared. They read
               as a strobe in any clip, and a learned interpolator dutifully morphs into them.

Without --apply it only reports. With --apply it MOVES flagged frames into <dir>/_qc_rejected/
and appends the reason to <dir>/_qc_rejected/qc.jsonl. Nothing is deleted: a false positive is
one `mv` away from being restored. Every tool that reads frames globs <dir>/*.jpg, so a
subdirectory is invisible to them.

--selftest runs the positive and negative controls on the known frames before trusting it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import click
import numpy as np
from PIL import Image

WHITE_TILE_MAX = 0.05     # a tile more than 5% pure white is a render artifact
FLASH_JUMP = 6.0          # mean grey levels a flash frame differs from BOTH neighbours
FLASH_AGREE = 4.0         # ... while the neighbours agree with each other within this
KNOWN_BAD = "data/goes/california_x3/2026-09-11T230000Z.jpg"


def stamp(p: Path) -> datetime:
    return datetime.strptime(p.stem, "%Y-%m-%dT%H%M%SZ")


def inspect(p: Path, side: int = 256) -> dict:
    """Judged per 256-px GIBS tile, whatever the frame's shape (California is 3x3 tiles, the
    CONUS frame 7x4); a broken render is a broken TILE, so that is the unit."""
    a = np.asarray(Image.open(p).convert("RGB"))
    pure = a.min(-1) >= 253
    tiles = [float(pure[y * side:(y + 1) * side, x * side:(x + 1) * side].mean())
             for y in range(a.shape[0] // side) for x in range(a.shape[1] // side)]
    # a content hash on a coarse, quantised thumbnail: JPEG re-encodes can differ in bytes for
    # the same picture, so hash what the picture IS rather than the file
    grey = np.asarray(Image.fromarray(a).convert("L").resize((64, 64), Image.BOX))
    thumb = grey // 4
    return dict(white=float(pure.mean()), white_tile_max=max(tiles), lum=float(grey.mean()),
                digest=hashlib.sha1(thumb.tobytes()).hexdigest())


def selftest() -> bool:
    bad = Path(KNOWN_BAD)
    if not bad.exists():
        click.echo(f"selftest skipped: {KNOWN_BAD} not present"); return True
    r = inspect(bad)
    fired = r["white_tile_max"] > WHITE_TILE_MAX
    click.echo(f"positive control  {bad.name}: worst tile {r['white_tile_max']:.3f} -> "
               f"{'FLAGGED' if fired else 'missed'} (must be FLAGGED)")
    # negative control: the brightest daylight frames in the same directory
    d = bad.parent
    day = [f for f in d.glob("*.jpg") if f.stem[11:13] in ("19", "20", "21")]
    lum = lambda f: float(np.asarray(Image.open(f).convert("L").resize((48, 48))).mean())
    worst = 0.0
    for f in sorted(day, key=lum, reverse=True)[:8]:
        worst = max(worst, inspect(f)["white_tile_max"])
    quiet = worst <= WHITE_TILE_MAX
    click.echo(f"negative control  8 brightest real overcast frames: worst tile {worst:.4f} -> "
               f"{'clean' if quiet else 'FLAGGED'} (must be clean)")
    ok = fired and quiet
    click.echo(f"selftest {'PASS' if ok else 'FAIL -- do not --apply'}")
    return ok


@click.command()
@click.option("--dir", "src", required=False, type=click.Path(path_type=Path))
@click.option("--apply", is_flag=True, help="move flagged frames into _qc_rejected/ (default: report only)")
@click.option("--span", default=3, type=int)
@click.option("--selftest", "st", is_flag=True)
def main(src, apply, span, st):
    if st or not src:
        ok = selftest()
        if not src:
            return
        if not ok and apply:
            raise SystemExit("controls failed; refusing to move anything")
    frames = sorted(src.glob("*.jpg"), key=stamp)
    click.echo(f"{src}: {len(frames)} frames")
    flagged, prev = [], None
    with click.progressbar(frames, label="inspecting") as it:
        lum = {}
        for p in it:
            r = inspect(p)
            why = None
            if r["white_tile_max"] > WHITE_TILE_MAX:
                why = f"white wedge: worst tile {r['white_tile_max']:.3f}"
            elif prev and prev[1] == r["digest"] and stamp(p) - stamp(prev[0]) <= timedelta(minutes=10):
                why = f"frozen: identical to {prev[0].name}"
            if why:
                flagged.append((p, why))
            prev = (p, r["digest"])
            lum[p] = r["lum"]
    # flash: needs the whole series. Only frames with both neighbours within 20 minutes qualify.
    ordered = [p for p in frames if p in lum]
    already = {p for p, _ in flagged}
    for i in range(1, len(ordered) - 1):
        a, b, c = ordered[i - 1], ordered[i], ordered[i + 1]
        if b in already or stamp(b) - stamp(a) > timedelta(minutes=20) or stamp(c) - stamp(b) > timedelta(minutes=20):
            continue
        d1, d2 = lum[b] - lum[a], lum[b] - lum[c]
        if min(abs(d1), abs(d2)) > FLASH_JUMP and (d1 > 0) == (d2 > 0) and abs(lum[c] - lum[a]) < FLASH_AGREE:
            flagged.append((b, f"flash: {d1:+.1f} vs previous, {d2:+.1f} vs next"))
    flagged.sort(key=lambda x: x[0].name)
    kinds = {}
    for _, why in flagged:
        kinds[why.split(":")[0]] = kinds.get(why.split(":")[0], 0) + 1
    click.echo(f"\nflagged {len(flagged)} of {len(frames)} ({len(flagged) / max(len(frames), 1):.2%}): {kinds}")
    for p, why in flagged[:15]:
        click.echo(f"  {p.name}  {why}")
    if len(flagged) > 15:
        click.echo(f"  ... and {len(flagged) - 15} more")
    if apply and flagged:
        q = src / "_qc_rejected"
        q.mkdir(exist_ok=True)
        with open(q / "qc.jsonl", "a") as log:
            for p, why in flagged:
                shutil.move(str(p), q / p.name)
                log.write(json.dumps(dict(file=p.name, reason=why, moved=datetime.utcnow().isoformat() + "Z")) + "\n")
        click.echo(f"moved {len(flagged)} into {q}/ (restore with mv; nothing deleted)")
    elif flagged:
        click.echo("report only -- rerun with --apply to quarantine")


if __name__ == "__main__":
    main()
