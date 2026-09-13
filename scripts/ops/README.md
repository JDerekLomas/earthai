# Keeping the meter honest

An instance bills by the minute whether or not the card is doing anything, so the
expensive mistake is never a big job — it is a small one that finished at 3am. Two things
live here.

**`idle_watch.py`** — lists every instance, samples each one's GPU and process table over
a minute, and says WORKING / IDLE / UNREACHABLE with what it costs.

```
python scripts/ops/idle_watch.py            # report only
python scripts/ops/idle_watch.py --watch 15 # keep checking every 15 min
python scripts/ops/idle_watch.py --stop     # also stop boxes listed in ops.json autostop
```

A box counts as WORKING if the GPU is busy **or** a known job process is alive. Both,
deliberately: a training run between ticks reads 0% for a moment, and a hung process can
hold the card at 100% while producing nothing.

**`ops.json`** — `autostop` is an explicit allow-list. Nothing is ever stopped unless its
name is in it; another project's box is reported, never touched. `earthai-gpu` is listed
by Derek's instruction (2026-09-13).

**Running on its own** — a launchd agent checks every 30 minutes and stops anything
allow-listed that has been idle 45 minutes:

```
launchctl list | grep earthai            # is it loaded
tail -20 scratch/idlewatch.log           # what it has seen
launchctl unload ~/Library/LaunchAgents/com.earthai.idlewatch.plist   # turn it off
```

45 minutes rather than 30 because a box legitimately sits at GPU 0% between a data upload
finishing and a run starting. For the same reason `rsync`, `scp`, `sftp-server`, `wget`
and `curl` count as work: without them an autostop would kill a box mid-upload, which is
the obvious way for a cost-saving tool to destroy more value than it saves.

## What this was built from

- `earthai-gpu` billed ~€10 sitting at 0% while a CPU fetch ran, because of a relative
  path in a chain script. Nothing was watching.
- `sl-mitra-1` billed €27 over 36 hours with **no GPU accounting records at all** — its
  card was never used.
- Two training runs were left to finish long after their FID stopped improving: clouds
  best at 200 kimg of 3000, land best at 1000 of 1236. About €15 of that was measurement
  we already had.

## The rule that would have caught all three

**CPU work does not belong on a GPU box.** Fetching, filtering and packing are network-
and CPU-bound; they run fine on a laptop and cost nothing there. Start the GPU instance
when a run is ready to start, stop it when the run ends.

## First false positive, kept as a warning

The first version matched the string `worker` against the process table and reported the
idle box as busy — `kworker` kernel threads. A monitor that says everything is fine is
worse than no monitor, so patterns are now matched against real command lines with kernel
threads filtered out.
