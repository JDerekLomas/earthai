"""Turn gallery verdicts on the Landshapes set into that project's rejects.txt hook.

Reads the tile-triage document from the Vercel API (or a saved JSON), keeps only
rejects on set == "landshapes", and writes one manifest `file` path per line to the
Landshapes tree, plus every comment (any verdict) as a sidecar JSON. These two paths
are the only writes this repo makes under /Users/dereklomas/earth, by agreement.

    TRIAGE_KEY=... python scripts/export_rejects.py
    python scripts/export_rejects.py --from verdicts.json --dry-run
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import click

API = "https://earthai-scales.vercel.app/api/verdicts"


@click.command()
@click.option("--tiles", default="site/triage/index.html", type=click.Path(path_type=Path), help="the deployed page; its TILES literal maps ids to manifest paths")
@click.option("--from", "src", default=None, type=click.Path(path_type=Path), help="verdict JSON instead of the API")
@click.option("--root", default="/Users/dereklomas/earth/data/tiles", type=click.Path(path_type=Path))
@click.option("--dry-run", is_flag=True)
def main(tiles, src, root, dry_run):
    html = tiles.read_text()
    lit = html[html.index("const TILES = ") + len("const TILES = "):]
    rows = json.loads(lit[:lit.index(";\n")])
    by_id = {r["id"]: r for r in rows if r.get("set") == "landshapes"}

    if src:
        doc = json.loads(src.read_text())
    else:
        key = os.environ.get("TRIAGE_KEY")
        if not key:
            sys.exit("TRIAGE_KEY not set (or pass --from)")
        req = urllib.request.Request(API, headers={"x-triage-key": key})
        doc = json.load(urllib.request.urlopen(req, timeout=30))

    rejects = sorted(by_id[i]["file"] for i, v in doc.get("v", {}).items() if v == "reject" and i in by_id)
    keeps = sum(1 for i, v in doc.get("v", {}).items() if v == "keep" and i in by_id)
    comments = {by_id[i]["file"]: {"comment": c, "verdict": doc.get("v", {}).get(i), "band": by_id[i]["b"], "family": by_id[i]["rgm"]}
                for i, c in doc.get("c", {}).items() if i in by_id}
    by_band = {}
    for i, v in doc.get("v", {}).items():
        if i in by_id:
            b = by_id[i]["b"]; d = by_band.setdefault(b, {"keep": 0, "reject": 0}); d[v] = d.get(v, 0) + 1

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = [f"# rejects from the earthai tile-triage gallery, {stamp}",
            f"# {len(rejects)} rejected, {keeps} kept, of {len(by_id)} landshapes crops shown",
            "# per band: " + "; ".join(f"{b}: {d['keep']} keep / {d['reject']} reject" for b, d in sorted(by_band.items()))]
    txt = "\n".join(head + rejects) + "\n"
    click.echo("\n".join(head))
    if dry_run:
        click.echo(f"(dry run) would write {len(rejects)} lines to {root / 'rejects.txt'} and {len(comments)} comments")
        return
    (root / "rejects.txt").write_text(txt)
    (root / "rejects.comments.json").write_text(json.dumps({"exported": stamp, "notes": doc.get("n", ""), "tiles": comments}, indent=1))
    click.echo(f"wrote {root / 'rejects.txt'} ({len(rejects)} paths) and rejects.comments.json ({len(comments)} comments)")


if __name__ == "__main__":
    main()
