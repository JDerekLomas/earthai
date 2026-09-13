"""Audit what build_dataset.py's gates actually threw away, and whether they were right to.

Two gates are under suspicion, both of them absolute tests on a quantity that scales with
how bright the scene is:

  black    drops a tile when >1% of its pixels fall under 0.04 luminance, meaning to catch
           GIBS holes. The identical test in fetch_goes.py was discarding 68% of good
           daylight frames, because deep ocean is legitimately darker than that.
  uniform  drops a tile when luminance std < 0.04, meaning to catch featureless sky. A dark
           scene has a small std whether or not it has structure.

    python scripts/gate_audit.py --tiles data/tiles_z9 --sample 4000
    python scripts/gate_audit.py --tiles data/tiles_z9 --sample 4000 --contact out/gate

For each sampled tile it recomputes the gate inputs plus three things the gate never had:
nodata (pixels that are exactly black, which is what a hole actually looks like), relative
detail (high-pass energy over mean brightness), and relative std. Then it reports the
disputed populations -- tiles the gate dropped that the better test would keep.

The warning that shapes the design, from the landshapes audit: do NOT simply relativise.
A ratio divides by brightness, so a near-black UNIFORM crop posts a healthy relative score
off a tiny denominator. Structure and uniformity are two different questions and need two
gates: a relative measure for structure, and a low ABSOLUTE floor for emptiness. This
script reports both so the pair can be chosen together, and prints the near-black-uniform
population explicitly so that failure mode cannot hide.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import click
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import stats  # noqa: E402


def highpass(lum: np.ndarray) -> float:
    """Energy in the 3x3 laplacian -- structure at the finest scale the tile resolves."""
    k = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]], dtype=np.float32)
    a = lum[1:-1, 1:-1]
    lap = (4 * a - lum[:-2, 1:-1] - lum[2:, 1:-1] - lum[1:-1, :-2] - lum[1:-1, 2:])
    return float(np.abs(lap).mean())


def probe(p: Path) -> dict | None:
    try:
        im = Image.open(p).convert("RGB")
    except Exception:
        return None
    a = np.asarray(im)
    s = stats(a)
    f = a.astype(np.float32) / 255
    lum = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    mean = max(float(lum.mean()), 1e-6)
    hp = highpass(lum)
    return dict(path=str(p), black=s["black"], std=s["std"], mean=s["mean"],
                cloud=s["cloud"], land=s["land"],
                nodata=float((a.max(-1) <= 2).mean()),      # a hole is EXACTLY black
                hp=hp, rel_hp=hp / mean, rel_std=s["std"] / mean)


@click.command()
@click.option("--tiles", default="data/tiles_z9", type=click.Path(path_type=Path))
@click.option("--sample", default=4000, type=int, help="tiles to probe (they are ~60 KB each)")
@click.option("--max-black", default=0.01, type=float, help="the gate as built")
@click.option("--min-std", default=0.04, type=float, help="the gate as built")
@click.option("--nodata-max", default=0.005, type=float, help="the corrected hole test")
@click.option("--contact", default=None, type=click.Path(path_type=Path),
              help="write contact sheets of each disputed population, so the verdict is lookable-at")
@click.option("--seed", default=0, type=int)
def main(tiles, sample, max_black, min_std, nodata_max, contact, seed):
    files = sorted(tiles.rglob("*.jpg"))
    if not files:
        raise SystemExit(f"no tiles under {tiles}")
    rng = random.Random(seed)
    pick = rng.sample(files, min(sample, len(files)))
    rows = [r for r in (probe(p) for p in pick) if r]
    click.echo(f"probed {len(rows)} of {len(files)} tiles under {tiles}\n")

    n = len(rows)
    arr = lambda k: np.array([r[k] for r in rows])
    blk_drop = [r for r in rows if r["black"] > max_black]
    uni_drop = [r for r in rows if r["black"] <= max_black and r["std"] < min_std]

    # --- gate 1: was "black" catching holes, or catching dark water? ---
    false_black = [r for r in blk_drop if r["nodata"] <= nodata_max]
    click.echo(f"BLACK gate: dropped {len(blk_drop)}/{n} ({len(blk_drop)/n:.1%})")
    click.echo(f"  of those, {len(false_black)} ({len(false_black)/max(len(blk_drop),1):.1%}) have essentially NO "
               f"pure-black pixels -- they are dark, not missing")
    if false_black:
        b = np.array([r["black"] for r in false_black]); nd = np.array([r["nodata"] for r in false_black])
        click.echo(f"  their darkness share: median {np.median(b):.3f}   their true hole share: median {np.median(nd):.5f}")
    real_black = [r for r in blk_drop if r["nodata"] > nodata_max]
    click.echo(f"  genuinely holed: {len(real_black)}  (median hole share {np.median([r['nodata'] for r in real_black]):.3f})"
               if real_black else "  genuinely holed: 0")

    # --- gate 2: is min-std a brightness gate in disguise? ---
    click.echo(f"\nUNIFORM gate: dropped {len(uni_drop)}/{n} ({len(uni_drop)/n:.1%}) for std < {min_std}")
    if uni_drop:
        m = np.array([r["mean"] for r in uni_drop]); allm = arr("mean")
        click.echo(f"  their brightness: median {np.median(m):.3f}  vs {np.median(allm):.3f} for the whole sample")
        rs = np.array([r["rel_std"] for r in uni_drop]); rh = np.array([r["rel_hp"] for r in uni_drop])
        click.echo(f"  their relative std: median {np.median(rs):.4f}   relative high-pass: median {np.median(rh):.4f}")
    # correlation: if std is mostly telling us about brightness, the gate is a brightness gate
    c = float(np.corrcoef(arr("std"), arr("mean"))[0, 1])
    cr = float(np.corrcoef(arr("rel_std"), arr("mean"))[0, 1])
    click.echo(f"\n  corr(std, brightness)      = {c:+.3f}   <- high means the absolute gate IS a brightness gate")
    click.echo(f"  corr(rel_std, brightness)  = {cr:+.3f}   <- the relative measure should be closer to zero")

    # --- the trap: relativising alone. A near-black uniform tile scores well on a ratio. ---
    dark = arr("mean") < np.quantile(arr("mean"), 0.10)
    rel_ok = arr("rel_hp") > np.quantile(arr("rel_hp"), 0.40)
    trap = [r for r, d, o in zip(rows, dark, rel_ok) if d and o and r["hp"] < np.quantile(arr("hp"), 0.10)]
    click.echo(f"\nTHE TRAP: {len(trap)} tiles are in the darkest 10%, have LOW absolute structure "
               f"(bottom 10% high-pass), and still pass a relative-only gate.")
    if trap:
        click.echo(f"  e.g. mean {trap[0]['mean']:.3f}  hp {trap[0]['hp']:.5f}  rel_hp {trap[0]['rel_hp']:.4f}")
    click.echo("  This is why the fix is TWO gates -- relative for structure, a low absolute floor for"
               "\n  emptiness -- and not a swap of one absolute number for one relative one.")

    # --- the disputed population: dropped by the built gate, kept by the pair ---
    floor = float(np.quantile(arr("hp"), 0.05))         # the genuinely featureless bottom
    rel_cut = float(np.quantile(arr("rel_hp"), 0.10))
    rescued = [r for r in blk_drop + uni_drop
               if r["nodata"] <= nodata_max and r["hp"] > floor and r["rel_hp"] > rel_cut]
    click.echo(f"\nDISPUTED: {len(rescued)} of {len(blk_drop)+len(uni_drop)} dropped tiles "
               f"({len(rescued)/max(len(blk_drop)+len(uni_drop),1):.1%}) pass the corrected pair "
               f"(no holes, absolute hp > {floor:.5f}, relative hp > {rel_cut:.4f})")
    click.echo(f"  -> {len(rescued)/n:.1%} of the raw set, which at 223,524 raw tiles is "
               f"~{int(len(rescued)/n*223524):,} tiles")

    if contact:
        contact.mkdir(parents=True, exist_ok=True)
        for name, group in [("false_black", false_black), ("uniform_dropped", uni_drop),
                            ("trap_dark_uniform", trap), ("rescued", rescued)]:
            g = group[:36]
            if not g:
                continue
            cols = 6
            rowsn = (len(g) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * 132, rowsn * 132), "#101820")
            for i, r in enumerate(g):
                im = Image.open(r["path"]).convert("RGB").resize((128, 128))
                sheet.paste(im, ((i % cols) * 132 + 2, (i // cols) * 132 + 2))
            sheet.save(contact / f"{name}.png")
            click.echo(f"  {contact / (name + '.png')}  ({len(g)} of {len(group)})")


if __name__ == "__main__":
    main()
