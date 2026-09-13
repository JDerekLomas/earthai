"""Turn the GOES frame archive into watchable clips: the same sky, ten minutes apart.

A directory of JPEGs sorted by name is already in time order, but it is not one sequence.
GeoColor goes infrared after dark and those frames are dropped, so each place's archive is
a string of daylight ARCS separated by ~15 hour night gaps. Concatenating the lot gives a
video that jumps from dusk to the next dawn every four seconds, which reads as a glitch
rather than as night. So runs are found first and encoded separately.

    python scripts/make_timelapse.py --place gulf_x3
    python scripts/make_timelapse.py --all --out site/goes/clips

Writes one MP4 per day-arc that is long enough to be worth watching, one "month" MP4 of
every arc in order for the places that have one, and clips.json describing them -- which is
what the viewer page reads. Frames are never re-encoded in place; the archive is the master.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import click

STRIDE_MIN = 10          # the native GOES cadence
GAP_TOL = 25             # a bigger jump than this starts a new run (one dropped frame is fine)


def stamp(p: Path) -> datetime:
    return datetime.strptime(p.stem, "%Y-%m-%dT%H%M%SZ")


def runs(frames: list[Path], gap_tol=GAP_TOL) -> list[list[Path]]:
    """Split a place's frames into continuous sequences."""
    out, cur = [], []
    for f in frames:
        if cur and (stamp(f) - stamp(cur[-1])).total_seconds() / 60 > gap_tol:
            out.append(cur); cur = []
        cur.append(f)
    if cur:
        out.append(cur)
    return out


def encode(frames: list[Path], dest: Path, fps: int, scale: int | None, crf: int, width: int | None = None) -> dict | None:
    """ffmpeg over an explicit concat list, so the frames keep their exact order and no
    globbing surprise reorders them. Returns None if ffmpeg fails rather than leaving a
    truncated file that the page would try to play."""
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{f.resolve()}'\n" for f in frames))
    if width:                                   # an explicit output width wins over the upscale
        vf = ["-vf", f"scale={width}:-2:flags=lanczos"]
    elif scale and scale > 1:
        vf = ["-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos"]
    else:
        vf = []
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-r", str(fps), "-f", "concat", "-safe", "0",
           "-i", str(lst), *vf, "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0 or not dest.exists():
        click.echo(f"   ffmpeg failed for {dest.name}: {r.stderr.strip()[:200]}")
        return None
    t0, t1 = stamp(frames[0]), stamp(frames[-1])
    return dict(file=dest.name, frames=len(frames),
                start=t0.strftime("%Y-%m-%dT%H:%M:00Z"), end=t1.strftime("%Y-%m-%dT%H:%M:00Z"),
                span_min=int((t1 - t0).total_seconds() / 60),
                seconds=round(len(frames) / fps, 1), kb=dest.stat().st_size // 1024)


@click.command()
@click.option("--place", default=None, help="a directory name under --src, e.g. gulf_x3")
@click.option("--all", "all_places", is_flag=True)
@click.option("--src", default="data/goes", type=click.Path(path_type=Path))
@click.option("--out", default="site/goes/clips", type=click.Path(path_type=Path))
@click.option("--fps", default=10, type=int, help="10 fps = one hour of weather per second")
@click.option("--min-frames", default=24, type=int, help="skip arcs shorter than this (4 hours)")
@click.option("--crf", default=26, type=int, help="x264 quality for a day arc; lower is better and bigger")
@click.option("--month-crf", default=28, type=int, help="the all-arcs overview can take more compression")
@click.option("--month-width", default=512, type=int, help="and less resolution: it is a survey, not the picture")
@click.option("--max-days", default=6, type=int, help="how many day-arcs to keep per place, newest first")
@click.option("--month/--no-month", default=True, help="also encode every arc of a place end to end")
def main(place, all_places, src, out, fps, min_frames, crf, month_crf, month_width, max_days, month):
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not on PATH (brew install ffmpeg)")
    if not all_places and not place:
        raise SystemExit("give --place or --all")
    dirs = sorted(d for d in src.iterdir() if d.is_dir()) if all_places else [src / place]
    if not dirs:
        raise SystemExit(f"nothing to encode under {src}")
    out.mkdir(parents=True, exist_ok=True)
    # merge into whatever is already described there: encoding one place at a time is the
    # normal way to work (a fetch finishes per place), and an overwrite would silently drop
    # every other place from the page while leaving its MP4s on disk.
    mf = out / "clips.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}

    for d in dirs:
        frames = sorted(d.glob("*.jpg"), key=stamp)
        if not frames:
            continue
        # 256 px tiles are too small to watch; the x3 stitches are already 768
        from PIL import Image
        w = Image.open(frames[0]).width
        scale = 3 if w <= 256 else 1
        arcs = [a for a in runs(frames) if len(a) >= min_frames]
        click.echo(f"{d.name:18} {len(frames):5} frames, {len(arcs)} arcs >= {min_frames} "
                   f"({w} px, x{scale})")

        days = []
        for a in arcs[-max_days:]:
            dest = out / f"{d.name}_{stamp(a[0]):%Y%m%d}.mp4"
            row = encode(a, dest, fps, scale, crf)
            if row:
                days.append(row)
                click.echo(f"   {row['file']:34} {row['frames']:4} fr  {row['seconds']:5.1f}s  {row['kb']:5} KB")

        row_all = None
        if month and len(arcs) > 1:
            flat = [f for a in arcs for f in a]
            dest = out / f"{d.name}_all.mp4"
            row_all = encode(flat, dest, fps * 2, scale, month_crf, month_width)
            if row_all:
                click.echo(f"   {row_all['file']:34} {row_all['frames']:4} fr  {row_all['seconds']:5.1f}s  {row_all['kb']:5} KB  (all arcs)")

        manifest[d.name] = dict(frames=len(frames), px=w * scale, arcs=len(arcs),
                                days=days, all=row_all)

    mf.write_text(json.dumps(manifest, indent=1))
    total = sum(r["kb"] for p in manifest.values() for r in p["days"] + ([p["all"]] if p["all"] else []))
    click.echo(f"\n-> {out}  {len(manifest)} places, {total // 1024} MB")


if __name__ == "__main__":
    main()
