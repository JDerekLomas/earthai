"""Smooth a frame sequence with learned frame interpolation (RIFE), then encode it.

PRIOR ART: scripts/month_sheet.py encodes real frames as-is; the "3 days, blended" sample on the
month page was ffmpeg's minterpolate (optical flow + blend). Neither morphs. At 1.9 km/px the deck
moves under a pixel between 10-minute frames (2-15 km/h, measured), so the visible jerk is texture
forming and dissolving, not motion -- which is exactly what a learned interpolator (RIFE v4.6, run
through rife-ncnn-vulkan on the Mac's own GPU, ~47 frames/s at 768 px) turns into a smooth morph.

    python scripts/interpolate.py --dir data/goes/california_x3 --start 2026-08-20 --days 3 --factor 8 \
        --out site/month/california_x3/smooth3.mp4
    python scripts/interpolate.py --dir data/goes/california_x3 --per-day --factor 4 --lon -122

--per-day writes days/<date>_x4.mp4 next to the month tool's clips and records each as `smooth` on
the matching row of month.json, so the page can toggle between real and smoothed.

Gaps in the archive are NOT special-cased: RIFE's directory mode treats frames as evenly spaced, so
a 30-minute gap becomes one slow morph instead of a jump. That is the behaviour we want for looking;
it is not for measuring anything. Real files only: rife-ncnn-vulkan skips symlinks silently.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))
from make_timelapse import stamp  # noqa: E402

RIFE = Path.home() / "tools/rife/rife-ncnn-vulkan-20221029-macos"


def solar(t: datetime, lon: float) -> datetime:
    return t + timedelta(hours=lon / 15)


CHUNK = 400   # real frames per RIFE run; a 5,400-frame run died at 12,074 outputs (GPU/memory), 400 never has


def rife_encode(frames: list[Path], dest: Path, factor: int, fps: int, width: int, crf: int) -> dict | None:
    """Long sequences are cut into CHUNK-frame runs that share their boundary frame, encoded
    separately, and joined with ffmpeg's concat demuxer (the duplicate boundary frame is dropped)."""
    if len(frames) < 2:
        return None
    if len(frames) <= CHUNK:
        return _rife_encode_one(frames, dest, factor, fps, width, crf)
    parts, made_total = [], 0
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        for k, a in enumerate(range(0, len(frames) - 1, CHUNK - 1)):
            sub = frames[a:a + CHUNK]
            if len(sub) < 2:
                break
            part = Path(td) / f"part{k:04d}.mp4"
            r = _rife_encode_one(sub, part, factor, fps, width, crf, drop_last=(a + CHUNK < len(frames)))
            if not r:
                return None
            parts.append(part); made_total += r["frames"]
            click.echo(f"   chunk {k}: {len(sub)} real -> {r['frames']} frames")
        lst = Path(td) / "parts.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        e = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                            "-movflags", "+faststart", str(dest)], capture_output=True, text=True)
        if e.returncode != 0:
            click.echo(f"   concat failed: {e.stderr.strip()[:200]}")
            return None
    t0, t1 = stamp(frames[0]), stamp(frames[-1])
    return dict(file=dest.name, frames=made_total, real_frames=len(frames), factor=factor, fps=fps,
                start=t0.strftime("%Y-%m-%dT%H:%M:00Z"), end=t1.strftime("%Y-%m-%dT%H:%M:00Z"),
                span_min=int((t1 - t0).total_seconds() / 60), seconds=round(made_total / fps, 1), kb=dest.stat().st_size // 1024)


def _rife_encode_one(frames: list[Path], dest: Path, factor: int, fps: int, width: int, crf: int, drop_last: bool = False) -> dict | None:
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        src, out = Path(td) / "in", Path(td) / "out"
        src.mkdir(); out.mkdir()
        for i, f in enumerate(frames):
            shutil.copyfile(f, src / f"{i:08d}.jpg")
        n = (len(frames) - 1) * factor + 1
        r = subprocess.run([str(RIFE / "rife-ncnn-vulkan"), "-i", str(src), "-o", str(out), "-n", str(n),
                            "-m", str(RIFE / "rife-v4.6"), "-f", "%08d.jpg", "-j", "2:4:4"], capture_output=True, text=True)
        made = sorted(out.glob("*.jpg"))
        if r.returncode != 0 or len(made) < n - 1:
            click.echo(f"   rife failed ({len(made)}/{n} frames): {r.stderr.strip()[:200]}")
            return None
        if drop_last and len(made) > 1:
            made[-1].unlink(); made = made[:-1]         # the next chunk starts with this same real frame
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(out / "%08d.jpg"),
               "-vf", f"scale={width}:-2:flags=lanczos", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
        e = subprocess.run(cmd, capture_output=True, text=True)
        if e.returncode != 0:
            click.echo(f"   ffmpeg failed: {e.stderr.strip()[:200]}")
            return None
    t0, t1 = stamp(frames[0]), stamp(frames[-1])
    return dict(file=dest.name, frames=len(made), real_frames=len(frames), factor=factor, fps=fps,
                start=t0.strftime("%Y-%m-%dT%H:%M:00Z"), end=t1.strftime("%Y-%m-%dT%H:%M:00Z"),
                span_min=int((t1 - t0).total_seconds() / 60), seconds=round(len(made) / fps, 1), kb=dest.stat().st_size // 1024)


@click.command()
@click.option("--dir", "src", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--start", default=None, help="first UTC day (YYYY-MM-DD) for a single clip")
@click.option("--days", default=3, type=int)
@click.option("--factor", default=4, type=int, help="frames out per real frame")
@click.option("--fps", default=24, type=int)
@click.option("--width", default=768, type=int)
@click.option("--crf", default=27, type=int)
@click.option("--out", default=None, type=click.Path(path_type=Path), help="single clip destination")
@click.option("--per-day", is_flag=True, help="one smoothed clip per local solar day, recorded in month.json")
@click.option("--lon", default=-122.0, type=float)
@click.option("--daylight", default="auto", type=click.Choice(["auto", "yes", "no"]), help="per-day: clip to 06-19 solar (auto: yes unless the dir ends in _ir)")
def main(src, start, days, factor, fps, width, crf, out, per_day, lon, daylight):
    frames = {stamp(p): p for p in src.glob("*.jpg")}
    month_dir = Path("site/month") / src.name
    if per_day:
        manifest = month_dir / "month.json"
        m = json.loads(manifest.read_text())
        dl = (not src.name.endswith("_ir")) if daylight == "auto" else daylight == "yes"
        by_day = defaultdict(list)
        for t in frames:
            if not dl or 6 <= solar(t, lon).hour < 19:
                by_day[solar(t, lon).strftime("%Y-%m-%d")].append(t)
        done = 0
        for row in m["clips"]:
            ts = sorted(by_day.get(row["date"], []))
            if len(ts) < 12:
                continue
            dest = month_dir / "days" / f"{row['date']}_x{factor}.mp4"
            r = rife_encode([frames[t] for t in ts], dest, factor, fps, width, crf)
            if r:
                row["smooth"] = f"days/{dest.name}"
                row["smooth_frames"] = r["frames"]; row["smooth_seconds"] = r["seconds"]
                done += 1
                click.echo(f"   {row['date']}  {len(ts)} -> {r['frames']} frames  {r['seconds']}s  {r['kb']} KB")
        m["smooth_factor"] = factor
        manifest.write_text(json.dumps(m, indent=1))
        click.echo(f"{src.name}: {done} smoothed day clips -> {manifest}")
        return
    t0 = datetime.strptime(start, "%Y-%m-%d")
    ts = sorted(t for t in frames if t0 <= t < t0 + timedelta(days=days))
    out = out or month_dir / f"smooth{days}.mp4"
    r = rife_encode([frames[t] for t in ts], out, factor, fps, width, crf)
    if not r:
        raise SystemExit("failed")
    click.echo(f"{out}: {len(ts)} real -> {r['frames']} frames, {r['seconds']}s, {r['kb'] // 1024} MB")
    manifest = month_dir / "month.json"
    if manifest.exists() and out.parent == month_dir:
        m = json.loads(manifest.read_text())
        m[out.stem] = dict(r, note=f"{start} + {days} days, RIFE v4.6 x{factor}, {fps} fps")
        manifest.write_text(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
