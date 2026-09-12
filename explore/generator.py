"""Two interchangeable generators for the explorer: a real StyleGAN pickle, and a stub.

The explorer is a UI problem, not a GAN problem, so it is built against a narrow
interface that both can satisfy:

    g.z_dim, g.w_dim, g.num_ws, g.res
    g.map(z)   -> W  ndarray [B, num_ws, w_dim]
    g.synth(w) -> uint8 ndarray [B, res, res, 3]

`StubGenerator` is procedural noise, needs nothing but numpy, and exists so the UI can
be built and driven before a checkpoint exists (and on a laptop, on a plane). Its skies
are not the model's skies -- they only respond to W the way a real generator does, which
is all the sliders need in order to be exercised.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

SG3 = os.environ.get("SG3", "/root/third_party/stylegan3")


class StubGenerator:
    """Fake clouds from summed sinusoids, steered by W. No torch, no checkpoint."""

    kind = "stub"

    def __init__(self, z_dim: int = 512, w_dim: int = 512, num_ws: int = 14, res: int = 256):
        self.z_dim, self.w_dim, self.num_ws, self.res = z_dim, w_dim, num_ws, res
        rs = np.random.RandomState(0)
        self.M = rs.randn(z_dim, w_dim) / np.sqrt(z_dim)  # stand-in mapping network
        self.w_avg = rs.randn(w_dim) * 0.1
        # a fixed bank of 2-D frequencies; W picks how much of each to mix in
        self.freqs = rs.randn(24, 2) * 3.0
        self.phase = rs.rand(24) * 2 * np.pi

    def map(self, z: np.ndarray, truncation: float = 1.0) -> np.ndarray:
        w = np.tanh(z @ self.M)
        w = self.w_avg + truncation * (w - self.w_avg)
        return np.repeat(w[:, None, :], self.num_ws, axis=1)

    def synth(self, w: np.ndarray) -> np.ndarray:
        y, x = np.mgrid[0 : self.res, 0 : self.res] / self.res
        n = len(self.freqs)
        out = []
        for wi in w:
            c = wi.mean(axis=0)  # one style vector per image
            amp = c[:n]
            field = np.zeros((self.res, self.res))
            for a, (fy, fx), ph in zip(amp, self.freqs, self.phase):
                field += a * np.sin(2 * np.pi * (fy * y + fx * x) + ph)
            field /= np.sqrt((amp**2).sum() / 2) + 1e-6  # unit std, so structure always shows
            cover = 1.2 * float(c[n])          # where the cloud/no-cloud threshold sits
            slope = 2.5 + 2.0 * float(c[n + 1])  # how hard the cloud edges are
            cloud = 1 / (1 + np.exp(-max(slope, 0.5) * (field - cover)))
            sea = np.array([0.04, 0.10, 0.24]) + 0.05 * field[..., None]
            sky = np.array([0.93, 0.94, 0.96]) - 0.12 * (1 - cloud[..., None])
            img = sea * (1 - cloud[..., None]) + sky * cloud[..., None]
            out.append(np.clip(img, 0, 1))
        return (np.stack(out) * 255).astype(np.uint8)


class StyleGANGenerator:
    """A trained network-snapshot pkl, through the stylegan3 repo's loader."""

    kind = "stylegan"

    def __init__(self, pkl: str, device: str | None = None):
        import torch

        sys.path.insert(0, SG3)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from latent import load_G  # reuse the CLI's loader

        self.torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"))
        self.G = load_G(pkl, self.device)
        self.z_dim, self.w_dim = self.G.z_dim, self.G.w_dim
        self.num_ws, self.res = self.G.num_ws, self.G.img_resolution

    def map(self, z: np.ndarray, truncation: float = 1.0) -> np.ndarray:
        t = self.torch.from_numpy(z).float().to(self.device)
        with self.torch.no_grad():
            return self.G.mapping(t, None, truncation_psi=truncation).cpu().numpy()

    def synth(self, w: np.ndarray) -> np.ndarray:
        t = self.torch.from_numpy(w).float().to(self.device)
        with self.torch.no_grad():
            img = self.G.synthesis(t, noise_mode="const")
        return (img.permute(0, 2, 3, 1) * 127.5 + 128).clamp(0, 255).to(self.torch.uint8).cpu().numpy()


def load(network: str | None, device: str | None = None):
    """A real generator if a pkl is given, else the stub."""
    return StyleGANGenerator(network, device) if network else StubGenerator()


def z_of(seed: int, z_dim: int) -> np.ndarray:
    return np.random.RandomState(seed).randn(1, z_dim)


def principal_directions(g, n: int = 256, k: int = 8, seed: int = 0):
    """GANSpace: PCA of sampled W. The top components are the axes the model actually varies
    along, which makes them the useful slider handles -- usually coverage, then structure."""
    z = np.random.RandomState(seed).randn(n, g.z_dim)
    w = g.map(z, truncation=1.0)[:, 0, :]
    w = w - w.mean(0, keepdims=True)
    _, s, vt = np.linalg.svd(w, full_matrices=False)
    return vt[:k], s[:k] / np.sqrt(max(n - 1, 1))  # directions, and their std in W
