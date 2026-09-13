"""Does an expanded image repeat itself at the block period?

The constant is tiled to enlarge the canvas, so the thing to fear is not only a seam at
the joins but the whole field copy-pasting every block. Autocorrelation at the block lag
answers it directly, against the same statistic at a lag that is not a multiple of the
block (which should be ~0 either way).

Measured on the 200 kimg clouds net, 3 seeds each: unsmoothed tiling gives 0.349 at the
256 px period -- the sky genuinely repeats -- while any gaussian over the constant drops
it to 0.017-0.035 and RAISES overall detail (11.7 -> 13.7), because a repeat is less
varied than a field. So the smoothing is load-bearing, and its job is breaking the
periodicity, not hiding a seam.

    python scripts/repeat_check.py out/sweep
"""
import numpy as np, pathlib, sys, collections
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
def periodicity(path, block=256):
    """How much does the image repeat itself at the block period? 1.0 = identical copies."""
    a = np.asarray(Image.open(path).convert("L")).astype(float)
    a = (a - a.mean()) / (a.std() + 1e-6)
    H, W = a.shape
    def corr(lag):
        if lag >= W: return float("nan")
        x, y = a[:, :W-lag], a[:, lag:]
        return float((x * y).mean())
    return corr(block), corr(block + 37), corr(block // 2)   # at the period, off it, half it
g = collections.defaultdict(list)
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.png")):
    g[f.stem.split("_s")[0]].append(periodicity(f))
print(f"{'kernel':10} {'corr @256 (the block period)':>29} {'@293 (off it)':>14} {'@128':>7}")
for k in sorted(g):
    v = np.array(g[k])
    print(f"{k:10} {v[:,0].mean():29.3f} {v[:,1].mean():14.3f} {v[:,2].mean():7.3f}")
