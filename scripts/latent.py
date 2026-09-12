"""Latent-space tools for a trained StyleGAN2 pickle: sample grids, interpolation
videos, seamless loops. Runs in the stylegan3 environment (needs its dnnlib/legacy).

  python scripts/latent.py sample  --network runs/x/network-snapshot.pkl --seeds 0-63 --out out/grid.png
  python scripts/latent.py interp  --network ... --seeds 1,7,42,3 --frames 90 --out out/walk.mp4
  python scripts/latent.py loop    --network ... --seed 5 --frames 240 --out out/loop.mp4

Concepts: Z is the input Gaussian; W is the mapped, disentangled space where
linear paths look smooth. Interpolation happens in W (lerp) by default, or in Z
with slerp (great-circle), which stays on the Gaussian shell instead of cutting
through its low-density middle. `truncation` pulls W toward the mean: lower is
cleaner but less varied.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import numpy as np

SG3 = os.environ.get("SG3", "/root/third_party/stylegan3")
sys.path.insert(0, SG3)


def load_G(pkl: str, device):
    import legacy  # from stylegan3
    import dnnlib
    with dnnlib.util.open_url(pkl) as f:
        return legacy.load_network_pkl(f)["G_ema"].to(device).eval()


def to_img(x):
    return (x.permute(0, 2, 3, 1) * 127.5 + 128).clamp(0, 255).cpu().numpy().astype(np.uint8)


def parse_seeds(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def slerp(a, b, t):
    a_n, b_n = a / np.linalg.norm(a), b / np.linalg.norm(b)
    omega = np.arccos(np.clip(np.dot(a_n, b_n), -1, 1))
    if omega < 1e-6:
        return (1 - t) * a + t * b
    return (np.sin((1 - t) * omega) * a + np.sin(t * omega) * b) / np.sin(omega)


def ease(t):
    return t * t * (3 - 2 * t)  # smoothstep


@click.group()
def cli():
    pass


def common(f):
    f = click.option("--network", required=True)(f)
    f = click.option("--truncation", default=0.8, type=float)(f)
    f = click.option("--out", required=True, type=click.Path(path_type=Path))(f)
    return f


@cli.command()
@common
@click.option("--seeds", default="0-63")
@click.option("--cols", default=8, type=int)
def sample(network, truncation, out, seeds, cols):
    import torch
    from PIL import Image
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    G = load_G(network, dev)
    ids = parse_seeds(seeds)
    z = torch.from_numpy(np.stack([np.random.RandomState(s).randn(G.z_dim) for s in ids])).to(dev)
    w = G.mapping(z, None, truncation_psi=truncation)
    imgs = []
    with torch.no_grad():
        for i in range(0, len(ids), 16):
            imgs.append(to_img(G.synthesis(w[i:i + 16], noise_mode="const")))
    imgs = np.concatenate(imgs)
    h, wdt = imgs.shape[1:3]
    rows = (len(ids) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * wdt, 3), np.uint8)
    for i, im in enumerate(imgs):
        sheet[(i // cols) * h:(i // cols + 1) * h, (i % cols) * wdt:(i % cols + 1) * wdt] = im
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet).save(out)
    click.echo(f"{len(ids)} samples -> {out}")


def write_video(frames, out: Path, fps: int):
    import imageio
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".gif":
        imageio.mimsave(out, frames, duration=1000 / fps, loop=0)
    else:
        # faststart moves the moov atom to the front, so browsers can start playing
        # before the whole file arrives (without it a <video> tag just hangs on load).
        w = imageio.get_writer(out, fps=fps, codec="libx264", quality=8, macro_block_size=None,
                               output_params=["-movflags", "+faststart"])
        for f in frames:
            w.append_data(f)
        w.close()


@cli.command()
@common
@click.option("--seeds", default="0,1,2,3", help="keyframe seeds; the path visits them in order")
@click.option("--frames", default=60, type=int, help="frames per segment")
@click.option("--fps", default=30, type=int)
@click.option("--space", default="w", type=click.Choice(["w", "z"]), help="lerp in W or slerp in Z")
@click.option("--strip", type=click.Path(path_type=Path), default=None, help="also save a contact strip of N frames as an image")
def interp(network, truncation, out, seeds, frames, fps, space, strip):
    import torch
    from PIL import Image
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    G = load_G(network, dev)
    ids = parse_seeds(seeds)
    zs = [np.random.RandomState(s).randn(G.z_dim) for s in ids]
    ws = [G.mapping(torch.from_numpy(z[None]).to(dev), None, truncation_psi=truncation)[0].cpu().numpy() for z in zs]
    out_frames = []
    with torch.no_grad():
        for k in range(len(ids) - 1):
            for i in range(frames):
                t = ease(i / frames)
                if space == "w":
                    w = (1 - t) * ws[k] + t * ws[k + 1]
                    wt = torch.from_numpy(w[None]).to(dev)
                else:
                    z = slerp(zs[k], zs[k + 1], t)
                    wt = G.mapping(torch.from_numpy(z[None]).to(dev), None, truncation_psi=truncation)
                out_frames.append(to_img(G.synthesis(wt, noise_mode="const"))[0])
    write_video(out_frames, out, fps)
    click.echo(f"{len(out_frames)} frames -> {out}")
    if strip:
        n = 8
        pick = [out_frames[int(i * (len(out_frames) - 1) / (n - 1))] for i in range(n)]
        Image.fromarray(np.concatenate(pick, axis=1)).save(strip)
        click.echo(f"strip -> {strip}")


@cli.command()
@common
@click.option("--seed", default=0, type=int)
@click.option("--frames", default=240, type=int)
@click.option("--fps", default=30, type=int)
@click.option("--radius", default=1.0, type=float, help="circle radius in Z, in units of the Gaussian std")
def loop(network, truncation, out, seed, frames, fps, radius):
    """Seamless loop: a circle in Z through two orthogonal random directions."""
    import torch
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    G = load_G(network, dev)
    rs = np.random.RandomState(seed)
    c = rs.randn(G.z_dim) * 0.0
    u, v = rs.randn(G.z_dim), rs.randn(G.z_dim)
    v -= u * np.dot(u, v) / np.dot(u, u)
    u, v = u / np.linalg.norm(u) * np.sqrt(G.z_dim) * radius, v / np.linalg.norm(v) * np.sqrt(G.z_dim) * radius
    out_frames = []
    with torch.no_grad():
        for i in range(frames):
            th = 2 * np.pi * i / frames
            z = c + np.cos(th) * u + np.sin(th) * v
            w = G.mapping(torch.from_numpy(z[None]).to(dev), None, truncation_psi=truncation)
            out_frames.append(to_img(G.synthesis(w, noise_mode="const"))[0])
    write_video(out_frames, out, fps)
    click.echo(f"{len(out_frames)} frames -> {out}")


if __name__ == "__main__":
    cli()
