"""Generate ONE continuous image larger than the training resolution.

Tiling independent 256 px samples gives a patchwork -- every tile is its own sample, so
the seams are real discontinuities no blending hides. But a StyleGAN2 generator is fully
convolutional above its 4x4 learned constant: give it a bigger constant and bigger noise
maps and it paints a single coherent field at any size, because every pixel is produced by
the same convolutions with the same w. Clouds are close to a stationary texture, which is
exactly the case where this works.

  python scripts/expand.py --network runs/.../network-snapshot-000200.pkl --tiles 6x3 --seed 7
  python scripts/expand.py --network ... --tiles 8x4 --drift 0.35 --seeds 3,9 --out out/wide.png

One w over the whole canvas gives one weather everywhere -- seamless but monotonous. --seeds
with --regions places several skies at points on the canvas and blends their FEATURE MAPS at
every block through smooth masks, so each area follows its own w and the transitions between
them are painted by the network rather than cross-faded on top of it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import numpy as np

SG3 = os.environ.get("SG3", "/root/third_party/stylegan3")
sys.path.insert(0, SG3)
sys.path.insert(0, str(Path(__file__).parent))


def expand_generator(G, hw, smooth=(5, 1.6)):
    """Re-shape the synthesis network to paint hw = (h, w) blocks of 4x4 instead of one."""
    import torch
    h, w = hw
    b4 = G.synthesis.b4
    c = b4.const.data                                   # [C, 4, 4]
    # tile the learned constant, then soften the joins so the field starts continuous
    big = c.repeat(1, h, w)
    if h * w > 1 and smooth:
        # the joins between tiled copies are a real seam (measured 1.44x without this);
        # a gaussian over the constant removes it before anything upsamples it
        n, sig = smooth
        ax = torch.arange(n, device=c.device, dtype=torch.float32) - (n - 1) / 2
        g = torch.exp(-(ax ** 2) / (2 * sig ** 2)); g = g / g.sum()
        k = (g[:, None] * g[None, :]).expand(big.shape[0], 1, n, n)
        big = torch.nn.functional.conv2d(big[None], k, padding=n // 2, groups=big.shape[0])[0]
    b4.const = torch.nn.Parameter(big)
    return G


def patch_shape_checks():
    """The layers assume a square canvas of a known size: assert_shape checks it, and the
    noise map is built from self.resolution. Both need to follow the tensor instead."""
    import torch
    import torch_utils.misc as misc
    from training.networks_stylegan2 import SynthesisLayer
    from training.networks_stylegan2 import modulated_conv2d
    from torch_utils.ops import bias_act
    misc.assert_shape = lambda *a, **k: None

    def forward(self, x, w, noise_mode="random", fused_modconv=True, gain=1):
        styles = self.affine(w)
        noise = None
        if self.use_noise and noise_mode == "random":
            h, w_ = x.shape[2] * self.up, x.shape[3] * self.up      # from the tensor, not a constant
            noise = torch.randn([x.shape[0], 1, h, w_], device=x.device) * self.noise_strength
        if self.use_noise and noise_mode == "const":
            noise = self.noise_const * self.noise_strength
        x = modulated_conv2d(x=x, weight=self.weight, styles=styles, noise=noise, up=self.up,
                             padding=self.padding, resample_filter=self.resample_filter,
                             flip_weight=(self.up == 1), fused_modconv=fused_modconv)
        act_gain = self.act_gain * gain
        clamp = self.conv_clamp * gain if self.conv_clamp is not None else None
        return bias_act.bias_act(x, self.bias.to(x.dtype), act=self.activation, gain=act_gain, clamp=clamp)

    SynthesisLayer.forward = forward


def region_centres(k):
    """k points spread over the unit canvas, in a row for small k and a loose grid beyond."""
    if k <= 3:
        return [((i + 0.5) / k, 0.5) for i in range(k)]
    cols = int(np.ceil(np.sqrt(k)))
    rows = int(np.ceil(k / cols))
    return [(((i % cols) + 0.5) / cols, ((i // cols) + 0.5) / rows) for i in range(k)]


def patch_spatial_blend(G, centres, blend):
    """After every synthesis block, collapse the batch of k skies into one canvas using smooth
    masks. Each sample is computed normally (so demodulation stays correct); only the activations
    are combined, which lets the following blocks paint the transition instead of covering it."""
    import torch
    from training.networks_stylegan2 import SynthesisNetwork

    def masks_for(h, w, device, dtype):
        yy, xx = torch.meshgrid(torch.linspace(0, 1, h, device=device), torch.linspace(0, 1, w, device=device), indexing="ij")
        d = torch.stack([((xx - cx) ** 2 + ((yy - cy) * (h / max(w, 1))) ** 2) for cx, cy in centres])
        return torch.softmax(-d / max(blend * 0.05, 1e-6), dim=0)[:, None].to(dtype)

    orig = SynthesisNetwork.forward

    def forward(self, ws, **kw):
        block_ws = []
        with torch.autograd.profiler.record_function("split_ws"):
            ws = ws.to(torch.float32)
            w_idx = 0
            for res in self.block_resolutions:
                block = getattr(self, f"b{res}")
                block_ws.append(ws.narrow(1, w_idx, block.num_conv + block.num_torgb))
                w_idx += block.num_conv
        x = img = None
        k = ws.shape[0]
        for res, cur_ws in zip(self.block_resolutions, block_ws):
            block = getattr(self, f"b{res}")
            x, img = block(x, img, cur_ws, **kw)
            m = masks_for(x.shape[2], x.shape[3], x.device, x.dtype)
            x = (x * m).sum(0, keepdim=True).repeat(k, 1, 1, 1)
            if img is not None:
                mi = masks_for(img.shape[2], img.shape[3], img.device, img.dtype)
                img = (img * mi).sum(0, keepdim=True).repeat(k, 1, 1, 1)
        return img

    SynthesisNetwork.forward = forward


@click.command()
@click.option("--network", required=True)
@click.option("--tiles", default="4x2", help="how many 256 px blocks wide x high")
@click.option("--seed", default=0, type=int)
@click.option("--seeds", default=None, help="comma-separated; with --regions each one owns part of the canvas")
@click.option("--regions", is_flag=True, help="give each seed a region of the canvas instead of averaging them")
@click.option("--blend", default=0.6, type=float, help="how wide the transition between regions is; low is an abrupt edge, high is a slow gradient")
@click.option("--truncation", default=0.7, type=float)
@click.option("--noise", default="random", type=click.Choice(["random", "none"]))
@click.option("--smooth", default="5,1.6", help="gaussian over the tiled constant as size,sigma -- or 'none' to see the seam it removes")
@click.option("--out", default=None, type=click.Path(path_type=Path))
def main(network, tiles, seed, seeds, regions, blend, truncation, noise, smooth, out):
    import torch
    from PIL import Image
    from latent import load_G

    patch_shape_checks()
    w_n, h_n = (int(v) for v in tiles.lower().split("x"))
    dev = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    G = load_G(network, dev)
    base = G.img_resolution
    sm = None if smooth == "none" else (int(smooth.split(",")[0]), float(smooth.split(",")[1]))
    G = expand_generator(G, (h_n, w_n), sm)

    ids = [int(v) for v in (seeds.split(",") if seeds else [str(seed)])]
    ws = torch.cat([G.mapping(torch.from_numpy(np.random.RandomState(s).randn(1, G.z_dim)).to(dev), None, truncation_psi=truncation) for s in ids])
    if regions and len(ids) > 1:
        centres = region_centres(len(ids))
        patch_spatial_blend(G, centres, blend)
        w = ws
    else:
        w = ws.mean(0, keepdim=True)

    with torch.no_grad():
        img = G.synthesis(w, noise_mode=noise)
        if img.shape[0] > 1:
            img = img[:1]
    a = (img.permute(0, 2, 3, 1) * 127.5 + 128).clamp(0, 255).to(torch.uint8).cpu().numpy()[0]
    out = out or Path("out") / f"expand_{w_n}x{h_n}_s{'-'.join(map(str, ids))}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a).save(out)
    click.echo(f"{a.shape[1]}x{a.shape[0]} px ({w_n}x{h_n} blocks of {base}) -> {out}")


if __name__ == "__main__":
    main()
