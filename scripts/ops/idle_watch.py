"""Find GPU boxes that are running and not working, and optionally stop them.

The failure this exists to prevent: an instance bills by the minute whether or not the
card is doing anything, so the expensive mistake is never a big job -- it is a small one
that finished at 3am and left the meter running. earthai-gpu cost EUR10 idling through a
CPU fetch because of a path bug, and sl-mitra-1 has billed EUR27 without its GPU ever
being used.

    python scripts/ops/idle_watch.py                 # look, report, change nothing
    python scripts/ops/idle_watch.py --stop          # also stop boxes listed in ops.json
    python scripts/ops/idle_watch.py --watch 15      # keep checking every 15 minutes

A box is WORKING if its GPU is busy OR a job process is alive -- both, because a training
run between ticks can read 0% for a moment, and a hung process can hold the card at 100%
while producing nothing. IDLE means neither, sustained across --samples checks a minute
apart, which is what makes it safe to act on.

Nothing is stopped unless the box's name is in `autostop` in ops.json AND it has been idle
for `idle_minutes`. Someone else's box is reported, never touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

CFG = Path(__file__).with_name("ops.json")
PRICE = {"L40S-1-48G": 1.47, "L4-1-24G": 0.75, "H100-1-80G": 2.73, "L40S-2-96G": 2.94,
         "RENDER-S": 1.00, "GPU-3070-S": 1.00}
# A process whose presence means "working" even if the GPU reads 0 this second. These are
# matched against the COMMAND LINE of non-kernel processes only: "worker" alone matched
# kworker kernel threads and reported an idle box as busy, which is the one failure a
# monitor must not have.
JOB_PATTERNS = ["train.py", "vllm", "VLLM", "dataset_tool.py", "fetch_tiles.py",
                "fetch_goes.py", "fetch_scenes.py", "build_dataset.py", "curate_",
                "celery", "gunicorn", "uvicorn", "ocr_", "-m vllm"]


def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


def instances(zone):
    out, rc = sh(f"scw instance server list zone={zone} -o json", 90)
    if rc != 0 or not out:
        return []
    return json.loads(out)


def probe(ip, samples, gap):
    """Sample the GPU a few times and look for live job processes. Returns None if unreachable."""
    utils, mem, jobs = [], [], []
    for i in range(samples):
        out, rc = sh(f"ssh -o ConnectTimeout=10 -o BatchMode=yes root@{ip} "
                     f"'nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader; "
                     f"echo ---; ps -eo comm,args --no-headers'", 45)
        if rc != 0:
            return None
        head, _, rest = out.partition("---")
        # drop kernel threads: their args are empty or bracketed
        rest = "\n".join(l for l in rest.splitlines()
                          if len(l.split(None, 1)) > 1 and not l.split(None, 1)[1].startswith("["))
        try:
            u, m = head.strip().split(",")
            utils.append(int(u.strip().rstrip(" %")))
            mem.append(int(m.strip().rstrip(" MiB")))
        except ValueError:
            utils.append(0); mem.append(0)
        jobs = sorted({p for p in JOB_PATTERNS if p in rest})
        if i < samples - 1:
            time.sleep(gap)
    return dict(util=max(utils), util_mean=sum(utils) / len(utils), mem=max(mem), jobs=jobs)


def classify(p, cfg):
    if p is None:
        return "UNREACHABLE", "no ssh"
    if p["util"] >= cfg["busy_util"]:
        return "WORKING", f"gpu {p['util']}%"
    if p["mem"] >= cfg["busy_mem_mib"]:
        return "WORKING", f"{p['mem']} MiB resident"
    if p["jobs"]:
        return "WORKING", "job: " + ", ".join(p["jobs"][:3])
    return "IDLE", "gpu 0%, no job"


def load_cfg():
    if CFG.exists():
        return json.loads(CFG.read_text())
    d = {"zone": "fr-par-2", "busy_util": 5, "busy_mem_mib": 500, "idle_minutes": 30,
         "samples": 3, "sample_gap_s": 20, "autostop": [], "note": "put a box NAME in autostop to let --stop touch it"}
    CFG.write_text(json.dumps(d, indent=2))
    return d


@click.command()
@click.option("--stop", is_flag=True, help="stop boxes that are idle AND listed in ops.json autostop")
@click.option("--watch", default=0, type=int, help="keep checking every N minutes instead of once")
@click.option("--samples", default=None, type=int)
def main(stop, watch, samples):
    cfg = load_cfg()
    n = samples or cfg["samples"]
    seen_idle = {}                      # name -> first time seen idle in this process

    while True:
        rows = instances(cfg["zone"])
        if not rows:
            click.echo("no instances (is `scw` configured?)"); return
        now = datetime.now(timezone.utc)
        click.echo(f"\n{now:%Y-%m-%d %H:%M} UTC")
        click.echo(f"{'box':16} {'type':12} {'state':8} {'':10} {'EUR/h':>6} {'up':>7}  verdict")
        waste = 0.0
        for s in rows:
            name, typ, st = s["name"], s["commercial_type"], s["state"]
            price = PRICE.get(typ, 0)
            up = (now - datetime.fromisoformat(s["creation_date"].replace("Z", "+00:00"))).total_seconds() / 3600
            if st != "running":
                click.echo(f"{name:16} {typ:12} {st:8} {'':10} {price:6.2f} {up:6.1f}h  stopped, only storage bills")
                continue
            ip = s["public_ip"]["address"] if s.get("public_ip") else None
            p = probe(ip, n, cfg["sample_gap_s"]) if ip else None
            verdict, why = classify(p, cfg)
            mark = ""
            if verdict == "IDLE":
                first = seen_idle.setdefault(name, now)
                mins = (now - first).total_seconds() / 60
                waste += price
                mark = f"  <- idle {mins:.0f} min, costing EUR{price:.2f}/h"
                if stop and name in cfg["autostop"] and mins >= cfg["idle_minutes"]:
                    out, rc = sh(f"scw instance server stop {s['id']} zone={cfg['zone']}", 120)
                    mark += "  STOPPED" if rc == 0 else f"  stop failed ({rc})"
                elif stop and name not in cfg["autostop"]:
                    mark += "  (not in autostop, left alone)"
            else:
                seen_idle.pop(name, None)
            click.echo(f"{name:16} {typ:12} {st:8} {'':10} {price:6.2f} {up:6.1f}h  {verdict:12} {why}{mark}")
        if waste:
            click.echo(f"\nidle burn right now: EUR{waste:.2f}/h = EUR{waste*24:.0f}/day")
        if not watch:
            return
        time.sleep(watch * 60)


if __name__ == "__main__":
    main()
