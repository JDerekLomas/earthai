"""Review a month of one place: a weather calendar, a noon flipbook, and one clip per day.

PRIOR ART: scripts/make_timelapse.py encodes continuous arcs of frames and this reuses its
encode() and stamp(). It does not do this job: a month of all-day frames is ONE continuous
arc, so make_timelapse would emit a single 40-day clip that nobody can review. Review needs
the month cut by LOCAL day, and a single still that shows all of it at once.

    python scripts/month_sheet.py --dir data/goes/california_x3 --lon -122
    python scripts/month_sheet.py --dir data/goes/california_x3_ir --lon -122 --daylight-clips no

Writes to --out (default site/month/<dir name>/):
  calendar.jpg   rows = days, columns = local solar hours. The month at a glance: persistence,
                 burn-off, the days the deck never came in.
  month.mp4      EVERY frame in order as one continuous clip, no cut at midnight: the month as
                 one motion, 24 fps = four hours a second, about four minutes long.
  hours/HH.mp4   one frame per day at solar hour HH, for all 24 hours, 3 fps. The noon flipbook
                 generalised: day-to-day change with the daily cycle held still, at any hour.
  days/*.mp4     one clip per local day
  month.json     what the page reads

Local time is SOLAR time from longitude, not the civil time zone: what the clouds respond to
is the sun, and daylight saving would shift every column by an hour halfway through a month.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import click
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from make_timelapse import encode, stamp  # noqa: E402


def solar(t: datetime, lon: float) -> datetime:
    return t + timedelta(hours=lon / 15)


def nearest(frames: dict[datetime, Path], target: datetime, tol_min: int = 10):
    best, bd = None, None
    for k in (0, 10, -10, 20, -20):
        c = target + timedelta(minutes=k)
        if c in frames:
            d = abs(k)
            if d <= tol_min and (bd is None or d < bd):
                best, bd = frames[c], d
    return best


@click.command()
@click.option("--dir", "src", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--lon", default=-122.0, type=float, help="longitude of the scene centre, for solar time")
@click.option("--out", default=None, type=click.Path(path_type=Path))
@click.option("--hours", default="7,9,11,13,15,17,1", help="local solar hours for calendar columns; include a night hour to show continuity")
@click.option("--thumb", default=150, type=int, help="calendar cell size in px")
@click.option("--min-frames", default=60, type=int, help="skip days with fewer frames than this (a day is 144)")
@click.option("--clip-width", default=512, type=int)
@click.option("--crf", default=28, type=int)
@click.option("--daylight-clips", default="yes", type=click.Choice(["yes", "no"]),
              help="yes: day clips run 06-19 solar, which is what GeoColor is for; no: full 24 h (infrared)")
def main(src, lon, out, hours, thumb, min_frames, clip_width, crf, daylight_clips):
    out = out or Path("site/month") / src.name
    (out / "days").mkdir(parents=True, exist_ok=True)
    frames = {stamp(p): p for p in src.glob("*.jpg")}
    if not frames:
        raise SystemExit(f"no frames in {src}")

    by_day: dict[str, list[datetime]] = defaultdict(list)
    for t in frames:
        by_day[solar(t, lon).strftime("%Y-%m-%d")].append(t)
    days = sorted(d for d, ts in by_day.items() if len(ts) >= min_frames)
    skipped = sorted(d for d, ts in by_day.items() if len(ts) < min_frames)
    click.echo(f"{src.name}: {len(frames)} frames, {len(days)} days with >= {min_frames} frames"
               f"{f', skipped partial days {skipped}' if skipped else ''}")

    # ---- calendar
    cols = [int(h) for h in hours.split(",")]
    HEAD, LEFT, GAP = 26, 118, 3
    W = LEFT + len(cols) * (thumb + GAP)
    H = HEAD + len(days) * (thumb + GAP)
    sheet = Image.new("RGB", (W, H), "#0b1420")
    d = ImageDraw.Draw(sheet)
    for j, h in enumerate(cols):
        d.text((LEFT + j * (thumb + GAP) + 4, 8), f"{h:02d}:00 solar" + ("  night" if h < 5 or h > 19 else ""), fill="#93a4b6")
    cells = missing = 0
    noon = []
    for i, day in enumerate(days):
        y = HEAD + i * (thumb + GAP)
        base = datetime.strptime(day, "%Y-%m-%d")
        d.text((8, y + 6), base.strftime("%a %d %b"), fill="#e4ecf4")
        d.text((8, y + 22), f"{len(by_day[day])} frames", fill="#6c7f92")
        for j, h in enumerate(cols):
            # solar hour on this solar date -> UTC instant
            target = (base + timedelta(hours=h) - timedelta(hours=lon / 15)).replace(second=0, microsecond=0)
            target = target.replace(minute=(target.minute // 10) * 10)
            f = nearest(frames, target)
            x = LEFT + j * (thumb + GAP)
            if f is None:
                missing += 1
                d.rectangle([x, y, x + thumb, y + thumb], outline="#27394b")
                # plain ASCII: PIL's default bitmap font has no em dash and draws a tofu box
                d.text((x + thumb // 2 - 12, y + thumb // 2 - 6), "none", fill="#6c7f92")
                continue
            cells += 1
            sheet.paste(Image.open(f).convert("RGB").resize((thumb, thumb), Image.BOX), (x, y))
        nf = nearest(frames, (base + timedelta(hours=12) - timedelta(hours=lon / 15)).replace(second=0, microsecond=0)
                     .replace(minute=((base + timedelta(hours=12) - timedelta(hours=lon / 15)).minute // 10) * 10), tol_min=20)
        if nf:
            noon.append((day, nf))
    sheet.save(out / "calendar.jpg", quality=86, optimize=True)
    click.echo(f"calendar.jpg  {W}x{H}  {cells} cells filled, {missing} empty")

    # ---- noon flipbook: day-to-day change with the diurnal cycle held constant
    noon_row = encode([f for _, f in noon], out / "noon.mp4", 2, None, 22, 768) if len(noon) > 2 else None
    if noon_row:
        click.echo(f"noon.mp4  {len(noon)} days  {noon_row['kb']} KB")

    # ---- the whole month as one continuous clip (the archive's own order; gaps simply jump)
    allts = sorted(frames)
    month_row = encode([frames[t] for t in allts], out / "month.mp4", 24, None, 30, 640)   # 5,600 frames: 768/crf24 made a 180 MB file
    if month_row:
        month_row["file"] = "month.mp4"
        click.echo(f"month.mp4  {len(allts)} frames  {month_row['seconds']}s  {month_row['kb'] // 1024} MB")

    # ---- same-hour flipbooks, one per solar hour
    (out / "hours").mkdir(exist_ok=True)
    hour_rows = []
    for h in range(24):
        picks = []
        for day in days:
            base = datetime.strptime(day, "%Y-%m-%d")
            tgt = (base + timedelta(hours=h) - timedelta(hours=lon / 15)).replace(second=0, microsecond=0)
            tgt = tgt.replace(minute=(tgt.minute // 10) * 10)
            f = nearest(frames, tgt, tol_min=20)
            if f:
                picks.append(f)
        if len(picks) > 2:
            r = encode(picks, out / "hours" / f"{h:02d}.mp4", 3, None, 22, 768)
            if r:
                r.update(hour=h, file=f"hours/{h:02d}.mp4", days=len(picks))
                hour_rows.append(r)
    click.echo(f"hours/  {len(hour_rows)} flipbooks")

    # ---- one clip per day
    rows = []
    for day in days:
        ts = sorted(by_day[day])
        if daylight_clips == "yes":
            ts = [t for t in ts if 6 <= solar(t, lon).hour < 19]
        if len(ts) < 12:
            continue
        r = encode([frames[t] for t in ts], out / "days" / f"{day}.mp4", 12, None, crf, clip_width)
        if r:
            r["date"] = day
            r["file"] = f"days/{day}.mp4"
            rows.append(r)
    click.echo(f"days/  {len(rows)} clips  {sum(r['kb'] for r in rows) // 1024} MB")

    manifest = dict(source=src.name, lon=lon, frames=len(frames), days=len(days),
                    first=min(frames).strftime("%Y-%m-%dT%H:%M:00Z"), last=max(frames).strftime("%Y-%m-%dT%H:%M:00Z"),
                    calendar="calendar.jpg", calendar_hours=cols, noon="noon.mp4" if noon_row else None,
                    noon_days=[dd for dd, _ in noon], month=month_row, hours=hour_rows,
                    clips=rows, skipped_partial_days=skipped)
    (out / "month.json").write_text(json.dumps(manifest, indent=1))
    click.echo(f"-> {out}/month.json")


if __name__ == "__main__":
    main()
