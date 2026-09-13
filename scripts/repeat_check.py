"""Does an expanded image repeat itself at the block period?

The constant is tiled to enlarge the canvas, so the thing to fear is not only a seam at
the joins but the whole field copy-pasting every block. Autocorrelation at the block lag
answers it directly, against the same statistic at a lag that is not a multiple of the
block (which should be ~0 either way).

Measured on the 200 kimg clouds net, 3 seeds each: unsmoothed tiling shows an excess of
+0.182 at the 256 px period -- the sky genuinely repeats -- while any gaussian over the
constant drops it to about zero and RAISES overall detail (11.7 -> 13.7), because a
repeat is less varied than a field. So the smoothing is load-bearing, and its job is breaking the
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
    # the excess over neighbouring lags is the statistic: any image with real large-scale
    # structure (a --regions panorama above all) scores high at EVERY lag, so the raw
    # correlation at the block period is not by itself evidence of repetition.
    base = np.nanmean([corr(block - 29), corr(block + 31), corr(block + 61)])
    return corr(block) - base, corr(block), base
g = collections.defaultdict(list)
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.png")):
    g[f.stem.split("_s")[0]].append(periodicity(f))
print(f"{'kernel':10} {'EXCESS at the block period':>27} {'raw':>7} {'baseline':>9}")
for k in sorted(g):
    v = np.array(g[k])
    print(f"{k:10} {v[:,0].mean():27.3f} {v[:,1].mean():7.3f} {v[:,2].mean():9.3f}")
print("\nexcess = correlation at the block lag minus the mean at three nearby non-multiple lags.")
print("Only the excess is evidence: the raw number rides on whatever large-scale structure")
print("the image already has, which is largest in exactly the panoramas worth checking.")
