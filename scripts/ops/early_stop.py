"""Stop a training run when its FID stops improving, instead of when its kimg runs out.

This is the largest remaining waste on the project. Both finished runs kept training long
after they had produced their best network: clouds peaked at 200 kimg of 3000 and got
steadily worse for the other 2800, and land peaked at 1000 of 1236. That is about EUR17 of
GPU spent making worse networks, and it happened because --kimg was the only stopping rule.

    python scripts/ops/early_stop.py --run runs/sg2-clouds-z9 --patience 4
    python scripts/ops/early_stop.py --host root@1.2.3.4 --run runs/... --patience 4 --act

Watches metric-fid50k_full.jsonl, and when the best FID has not improved for `patience`
consecutive snapshots it reports (or with --act, kills the trainer). The best snapshot is
already on disk, so stopping costs nothing but the chance that it would have recovered --
which is why patience is a parameter and 4 snapshots (400 kimg) is a deliberately generous
default rather than the 1 or 2 that would be cheapest.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import click

SEC_PER_KIMG = 13.7      # measured, 256 px batch 32 on an L40S
EUR_PER_HOUR = 1.47


def sh(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


def read_fid(host, run):
    """Rows of (kimg, fid), oldest first. Works locally or over ssh."""
    pat = f"{run}/*/metric-fid50k_full.jsonl"
    cmd = f"cat {pat}" if not host else f"ssh -o ConnectTimeout=10 -o BatchMode=yes {host} 'cat {pat}'"
    out, rc = sh(cmd, 60)
    if rc != 0 or not out:
        return []
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
            kimg = int(d["snapshot_pkl"].split("-")[-1].split(".")[0])
            rows.append((kimg, float(d["results"]["fid50k_full"])))
        except Exception:
            continue
    return sorted(rows)


@click.command()
@click.option("--run", required=True, help="run directory holding the <id>/ subdir")
@click.option("--host", default=None, help="ssh target, e.g. root@1.2.3.4; omit for local")
@click.option("--patience", default=4, type=int, help="snapshots without improvement before stopping")
@click.option("--min-kimg", default=200, type=int, help="never stop before this, so a slow start is not cut off")
@click.option("--act", is_flag=True, help="actually kill the trainer (default: report only)")
@click.option("--watch", default=0, type=int, help="keep checking every N minutes")
def main(run, host, patience, min_kimg, act, watch):
    while True:
        rows = read_fid(host, run)
        if not rows:
            click.echo(f"no FID rows yet at {run}")
        else:
            best_i = min(range(len(rows)), key=lambda i: rows[i][1])
            best_kimg, best_fid = rows[best_i]
            since = len(rows) - 1 - best_i
            cur_kimg, cur_fid = rows[-1]
            click.echo(f"{len(rows):3} snapshots | now {cur_kimg:5} kimg FID {cur_fid:6.2f} | "
                       f"best {best_fid:6.2f} at {best_kimg:5} kimg | {since} since best")
            if since >= patience and cur_kimg >= min_kimg:
                wasted_h = (cur_kimg - best_kimg) * SEC_PER_KIMG / 3600
                click.echo(f"PLATEAU: no improvement for {since} snapshots ({cur_kimg - best_kimg} kimg, "
                           f"{wasted_h:.1f} h, EUR{wasted_h * EUR_PER_HOUR:.0f} already spent past the peak)")
                click.echo(f"   keep: {run}/*/network-snapshot-{best_kimg:06d}.pkl")
                if act:
                    # [t]rain.py matches "train.py" but not itself: a plain 'train.py.*name'
                    # pattern also matched the shell running this pkill, killed it, and
                    # reported "kill returned -15" on a kill that had in fact succeeded
                    # (first real firing, 14 Sep 2026, ab-transfer).
                    cmd = "pkill -f '[t]rain.py.*%s'" % Path(run).name
                    out, rc = sh(cmd if not host else f"ssh {host} \"{cmd}\"", 60)
                    click.echo("   trainer killed" if rc == 0 else f"   kill returned {rc}")
                return
        if not watch:
            return
        time.sleep(watch * 60)


if __name__ == "__main__":
    main()
