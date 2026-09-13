"""Is an expanded image actually seamless? Measure, with a control.

Enlarging the generator tiles its learned 4x4 constant, which could print a grid at the
block period. The test is the mean gradient across the block boundaries against the median
gradient of the image -- but that number alone is misleading: an image with strong vertical
structure (a --regions panorama) reads high at ANY column. So the same statistic is also
taken at columns offset half a block, where no seam can exist. Only the boundary/control
RATIO is evidence: 1.0 means the boundaries are indistinguishable from anywhere else.

    python scripts/seam_check.py out/gallery
"""
import numpy as np, pathlib, sys
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
def ratios(path, block=256):
    a = np.asarray(Image.open(path).convert("L")).astype(float)
    H, W = a.shape
    gx = np.abs(np.diff(a, axis=1)).mean(axis=0)
    gy = np.abs(np.diff(a, axis=0)).mean(axis=1)
    cols = [c-1 for c in range(block, W, block)]
    rows = [r-1 for r in range(block, H, block)]
    # control: the same count of boundaries offset by half a block (should be ~1.0)
    ccols = [c-1+block//2 for c in range(block, W-block//2, block)]
    v = np.mean(gx[cols])/np.median(gx) if cols else float("nan")
    vc = np.mean(gx[ccols])/np.median(gx) if ccols else float("nan")
    h = np.mean(gy[rows])/np.median(gy) if rows else float("nan")
    return v, vc, h, W, H
print(f"{'image':22} {'v-seam':>7} {'control':>8} {'h-seam':>7}  size")
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.png")):
    v, vc, h, W, H = ratios(f)
    print(f"{f.stem:22} {v:7.3f} {vc:8.3f} {h:7.3f}  {W}x{H}")
