"""Stack several renderings of the SAME sky at the same instants into one clip.

PRIOR ART: scripts/make_timelapse.py -- it encodes one directory of frames into per-arc
clips, and this imports its arc-splitting. It does not do this job: a comparison needs the
intersection of timestamps across several source directories, composited panel-by-panel, so
that what changes between panels is the rendering and nothing else.

Why it exists: GeoColor is true colour by day and an infrared rendering over city lights by
night, so a GeoColor timelapse that crosses dusk changes picture halfway. Band 13 infrared
measures cloud-top temperature and looks the same at noon and at 2am. Air Mass is a
water-vapour composite showing the air the clouds form in. Run side by side, the same
weather is visible in three different physics, and the question "what does night cost us?"
becomes something to look at rather than argue about.

    python scripts/compare_timelapse.py --place gulf --layers geocolor,ir,airmass
    python scripts/compare_timelapse.py --all --width 1536 --out site/goes/compare

Writes one MP4 per place plus compare.json describing them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import click
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from make_timelapse import runs, stamp  # noqa: E402

TITLES = {
    "geocolor": ("GeoColor", "true colour by day; infrared + city lights by night"),
    "ir": ("Band 13 infrared", "cloud-top temperature; identical day and night"),
    "airmass": ("Air Mass", "water vapour and ozone; the air the clouds form in"),
}
BAR = 30           # label bar height in output pixels, before scaling


def dir_for(src: Path, place: str, layer: str, span: int) -> Path:
    tag = "" if span == 1 else f"_x{span}"
    return src / f"{place}{tag}{'' if layer == 'geocolor' else '_' + layer}"


def frames_by_time(d: Path) -> dict[datetime, Path]:
    return {stamp(p): p for p in d.glob("*.jpg")}


def composite(paths: list[Path], labels: list[str], t: datetime, panel: int, night: bool) -> Image.Image:
    n = len(paths)
    sheet = Image.new("RGB", (panel * n, panel + BAR), "#0b1420")
    d = ImageDraw.Draw(sheet)
    for i, (p, lab) in enumerate(zip(paths, labels)):
        im = Image.open(p).convert("RGB")
        if im.width != panel:
            im = im.resize((panel, panel), Image.LANCZOS)
        sheet.paste(im, (i * panel, BAR))
        d.text((i * panel + 8, 9), lab, fill="#e4ecf4")
    stampstr = t.strftime("%Y-%m-%d  %H:%M UTC")
    w = d.textlength(stampstr)
    d.text((sheet.width - w - 10, 9), stampstr, fill="#93a4b6")
    # a night marker, because the whole point is watching what survives dusk
    if night:
        d.ellipse([sheet.width - w - 30, 11, sheet.width - w - 18, 23], fill="#37718f")
    return sheet


@click.command()
@click.option("--place", default=None)
@click.option("--all", "all_places", is_flag=True, help="every place that has all the layers")
@click.option("--layers", default="geocolor,ir,airmass")
@click.option("--span", default=3, type=int)
@click.option("--src", default="data/goes", type=click.Path(path_type=Path))
@click.option("--out", default="site/goes/compare", type=click.Path(path_type=Path))
@click.option("--width", default=1536, type=int, help="total output width across all panels")
@click.option("--fps", default=12, type=int)
@click.option("--crf", default=26, type=int)
@click.option("--min-frames", default=40, type=int)
def main(place, all_places, layers, span, src, out, width, fps, crf, min_frames):
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not on PATH (brew install ffmpeg)")
    lay = layers.split(",")
    if all_places:
        # a place qualifies only if every requested layer exists for it
        base = sorted({d.name.split("_x")[0] for d in src.iterdir() if d.is_dir()})
        places = [p for p in base if all(dir_for(src, p, l, span).is_dir() for l in lay)]
    else:
        if not place:
            raise SystemExit("give --place or --all")
        places = [place]
    out.mkdir(parents=True, exist_ok=True)
    mf = out / "compare.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}

    for p in places:
        dirs = [dir_for(src, p, l, span) for l in lay]
        missing = [d for d in dirs if not d.is_dir()]
        if missing:
            click.echo(f"{p}: missing {[d.name for d in missing]}, skipping"); continue
        maps = [frames_by_time(d) for d in dirs]
        common = sorted(set.intersection(*[set(m) for m in maps]))
        if len(common) < min_frames:
            click.echo(f"{p}: only {len(common)} instants common to all {len(lay)} layers, skipping"); continue
        # the longest continuous run, so the clip is one unbroken stretch of time
        arcs = runs([maps[0][t] for t in common])
        arc = max(arcs, key=len)
        times = [stamp(f) for f in arc]
        panel = width // len(lay)
        labels = [TITLES.get(l, (l, ""))[0] for l in lay]

        nights = 0
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for i, t in enumerate(times):
                # "night" here is local solar night at the tile's longitude, which is what the
                # viewer is actually looking for; the frames themselves carry no such flag
                lon = {"gulf": -80, "california": -122, "amazon": -60, "andes": -70,
                       "peru": -85, "atlantic_itcz": -35, "pacific_itcz": -140}.get(p, 0)
                solar = (t.hour + t.minute / 60 + lon / 15) % 24
                night = solar < 6 or solar > 18.5
                nights += night
                composite([m[t] for m in maps], labels, t, panel, night).save(tmp / f"{i:05d}.jpg", quality=92)
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{tmp / f'{i:05d}.jpg'}'\n" for i in range(len(times))))
            dest = out / f"{p}_compare.mp4"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-r", str(fps), "-f", "concat", "-safe", "0",
                   "-i", str(lst), "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                click.echo(f"{p}: ffmpeg failed: {r.stderr.strip()[:200]}"); continue

        span_h = (times[-1] - times[0]).total_seconds() / 3600
        manifest[p] = dict(file=dest.name, layers=lay, frames=len(times),
                           start=times[0].strftime("%Y-%m-%dT%H:%M:00Z"),
                           end=times[-1].strftime("%Y-%m-%dT%H:%M:00Z"),
                           hours=round(span_h, 1), nights=nights,
                           seconds=round(len(times) / fps, 1),
                           kb=dest.stat().st_size // 1024, px=f"{panel * len(lay)}x{panel + BAR}")
        click.echo(f"{p:12} {len(times):4} instants  {span_h:5.1f} h  {nights:4} night frames  "
                   f"{dest.stat().st_size // 1024:6} KB  -> {dest.name}")

    mf.write_text(json.dumps(manifest, indent=1))
    click.echo(f"\n-> {out}  {len(manifest)} places")


if __name__ == "__main__":
    main()
