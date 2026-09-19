"""Upload the globe's day clips and tiles to the Cloudflare R2 bucket the page reads them from.

PRIOR ART: none — scripts/deploy_site.sh ships site/ to Vercel, which is the wrong host for ten days x five
satellites of tiles (10-15 GB); rclone/aws would need an R2 API token that does not exist on this laptop,
whereas `wrangler` is logged in by OAuth and can `r2 object put` one file at a time.

    python scripts/r2_sync.py data/clouds/days:globe data/clouds/tiles/tenday/goes19:globe/tiles

Each argument is SRC_DIR:KEY_PREFIX; every regular file under SRC_DIR goes to <prefix>/<path relative to SRC_DIR>
in the bucket, so the bucket mirrors the site's own paths under `globe/` and the page needs only a base URL
(`assets` in clouds.json, e.g. https://pub-....r2.dev/globe/). Resumable: a done-list (--done) records key, size
and mtime of every upload, and an unchanged file is skipped, so re-running after an interruption or a partial
rebuild uploads only what changed. Thousands of files take a while (~1-2 s each; --workers in parallel): run it
under tmux. Log files, numpy arrays, PNG frames and jsonl are never uploaded (--skip).

Bucket setup, done once on 2026-09-19 with the same wrangler: `r2 bucket dev-url enable earthai-clouds` (public
read at https://pub-9fd592e0ca484a2dbec64ef0858a9bc9.r2.dev) and a CORS rule allowing GET/HEAD from any origin with
the Range header and Content-Range exposed (the page fetches tile keyframe groups by byte range).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import click

TYPES = {".mp4": "video/mp4", ".webp": "image/webp", ".json": "application/json", ".h264": "application/octet-stream",
         ".jpg": "image/jpeg", ".png": "image/png", ".webm": "video/webm"}
SKIP = {".log", ".txt", ".npy", ".npz", ".jsonl", ".DS_Store", ".png"}


def wrangler_bin(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    if shutil.which("wrangler"):
        return ["wrangler"]
    return ["npx", "-y", "wrangler"]


@click.command()
@click.argument("pairs", nargs=-1, required=True)
@click.option("--bucket", default="earthai-clouds", show_default=True)
@click.option("--done", default="data/clouds/r2_done.jsonl", type=click.Path(path_type=Path), show_default=True,
              help="the done-list; delete it to re-upload everything")
@click.option("--workers", default=4, show_default=True)
@click.option("--wrangler", "wr", default=None, help="path to wrangler (default: on PATH, else npx wrangler)")
@click.option("--dry-run", is_flag=True)
@click.option("--skip", default=",".join(sorted(SKIP)), show_default=True, help="extensions never uploaded")
def main(pairs, bucket, done, workers, wr, dry_run, skip):
    skip = set(skip.split(","))
    done.parent.mkdir(parents=True, exist_ok=True)
    have = {}
    if done.exists():
        for line in done.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                have[r["key"]] = (r["size"], r["mtime"])
    todo = []
    for pair in pairs:
        src, _, prefix = pair.partition(":")
        src = Path(src)
        if not src.is_dir():
            raise click.ClickException(f"{src} is not a directory")
        prefix = prefix.strip("/")
        for p in sorted(src.rglob("*")):
            if not p.is_file() or p.name.startswith(".") or p.suffix in skip:
                continue
            key = f"{prefix}/{p.relative_to(src).as_posix()}" if prefix else p.relative_to(src).as_posix()
            stt = p.stat()
            if have.get(key) == (stt.st_size, int(stt.st_mtime)):
                continue
            todo.append((p, key, stt.st_size, int(stt.st_mtime)))
    total = sum(t[2] for t in todo)
    click.echo(f"{len(todo)} files, {total / 1e6:.0f} MB to upload to {bucket} ({len(have)} already done)")
    if dry_run:
        for p, key, size, _ in todo[:40]:
            click.echo(f"  {key}  {size / 1e6:.2f} MB")
        if len(todo) > 40:
            click.echo(f"  ... {len(todo) - 40} more")
        return
    cmd0 = wrangler_bin(wr)
    lock = Lock()
    sent = [0, 0]
    t0 = time.time()

    def one(item):
        p, key, size, mtime = item
        ct = TYPES.get(p.suffix, "application/octet-stream")
        for attempt in range(3):
            r = subprocess.run(cmd0 + ["r2", "object", "put", f"{bucket}/{key}", "--file", str(p), "--content-type", ct, "--remote"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                with lock:
                    sent[0] += 1; sent[1] += size
                    with done.open("a") as f:
                        f.write(json.dumps({"key": key, "size": size, "mtime": mtime}) + "\n")
                    if sent[0] % 20 == 0 or sent[0] == len(todo):
                        el = time.time() - t0
                        click.echo(f"  {sent[0]}/{len(todo)}  {sent[1] / 1e6:.0f} MB  {sent[1] / 1e6 / max(el, 1e-3):.1f} MB/s  {el / 60:.1f} min")
                return True
            time.sleep(2 * (attempt + 1))
        with lock:
            click.echo(f"FAILED {key}: {(r.stderr or r.stdout)[-300:]}")
        return False

    with ThreadPoolExecutor(workers) as pool:
        ok = sum(pool.map(one, todo))
    click.echo(f"{ok}/{len(todo)} uploaded, {sent[1] / 1e6:.0f} MB in {(time.time() - t0) / 60:.1f} min")
    if ok != len(todo):
        sys.exit(1)


if __name__ == "__main__":
    main()
