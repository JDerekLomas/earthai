"""Interactive latent explorer: drive the sky with sliders, then record the path.

    python explore/app.py                                   # stub generator, no checkpoint needed
    python explore/app.py --network runs/.../snapshot.pkl   # a trained net (needs the sg3 env)

What the controls mean
  seed          which point in Z you start from -- each seed is a different sky
  truncation    how far W may stray from the average sky: low is clean and samey, high is varied
  blend         a straight line in W from seed A to seed B; W is where linear paths look smooth
  PC 1..6       the directions W actually varies along (PCA over sampled W, i.e. GANSpace),
                in units of that component's own standard deviation

Recording walks the same controls you just set: either A->B, or a seamless circle in Z.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gradio as gr
import numpy as np
from PIL import Image

import generator as gen_mod  # same directory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from latent import write_video  # noqa: E402  the CLI already knows how to write mp4/gif

K = 6  # number of PCA sliders
OUT = Path("out")  # recordings land here; gradio must be told it may serve them


class Explorer:
    def __init__(self, network: str | None, device: str | None):
        self.g = gen_mod.load(network, device)
        self.dirs, self.sigma = gen_mod.principal_directions(self.g, k=K)
        self.label = f"{self.g.kind} generator, {self.g.res}px"

    def w_for(self, seed_a: int, seed_b: int, blend: float, trunc: float, sliders: list[float]) -> np.ndarray:
        wa = self.g.map(gen_mod.z_of(int(seed_a), self.g.z_dim), trunc)
        if blend > 0:
            wb = self.g.map(gen_mod.z_of(int(seed_b), self.g.z_dim), trunc)
            wa = (1 - blend) * wa + blend * wb
        delta = sum(float(s) * self.sigma[i] * self.dirs[i] for i, s in enumerate(sliders))
        return wa + delta  # broadcasts over the num_ws axis

    def image(self, *args) -> Image.Image:
        return Image.fromarray(self.g.synth(self.w_for(*args[:4], list(args[4:])))[0])

    def record(self, seed_a, seed_b, blend, trunc, *rest):
        sliders, mode, frames, fps = list(rest[:K]), rest[K], int(rest[K + 1]), int(rest[K + 2])
        ws = []
        if mode == "A to B":
            for i in range(frames):
                t = i / (frames - 1)
                t = t * t * (3 - 2 * t)  # smoothstep, so it eases in and out
                ws.append(self.w_for(seed_a, seed_b, t, trunc, sliders)[0])
        else:  # a circle through the top two PCs returns exactly to its start
            base = self.w_for(seed_a, seed_b, blend, trunc, sliders)[0]
            for i in range(frames):
                th = 2 * np.pi * i / frames
                ws.append(base + np.cos(th) * self.sigma[0] * self.dirs[0] + np.sin(th) * self.sigma[1] * self.dirs[1])
        OUT.mkdir(exist_ok=True)
        path = OUT / f"walk_{int(seed_a)}_{int(seed_b)}_{mode.split()[0]}.mp4"
        write_video(list(self.g.synth(np.stack(ws))), path, fps)
        return str(path), str(path)


def build(ex: Explorer):
    with gr.Blocks(title="earthai latent explorer") as demo:
        gr.Markdown(f"### earthai latent explorer\n{ex.label} — sliders are in units of each component's own std")
        with gr.Row():
            with gr.Column(scale=3):
                img = gr.Image(label="sky", type="pil", elem_id="sky")
            with gr.Column(scale=2):
                seed_a = gr.Number(value=0, label="seed A", precision=0)
                seed_b = gr.Number(value=1, label="seed B", precision=0)
                blend = gr.Slider(0, 1, 0, step=0.01, label="blend A → B")
                trunc = gr.Slider(0.1, 1.5, 0.8, step=0.05, label="truncation")
                sliders = [gr.Slider(-3, 3, 0, step=0.05, label=f"PC {i + 1}") for i in range(K)]
                with gr.Row():
                    shuffle = gr.Button("random seeds")
                    reset = gr.Button("reset sliders")
        with gr.Accordion("record", open=False):
            with gr.Row():
                mode = gr.Radio(["A to B", "circle (seamless)"], value="A to B", label="path")
                frames = gr.Slider(12, 240, 60, step=6, label="frames")
                fps = gr.Slider(6, 30, 15, step=1, label="fps")
                go = gr.Button("record", variant="primary")
            anim = gr.Video(label="animation", height=320, autoplay=True, loop=True)
            where = gr.Textbox(label="saved to", interactive=False)

        controls = [seed_a, seed_b, blend, trunc, *sliders]
        for c in controls:
            c.change(ex.image, controls, img, show_progress="hidden")
        demo.load(ex.image, controls, img)
        shuffle.click(lambda: (int(np.random.randint(1e6)), int(np.random.randint(1e6))), None, [seed_a, seed_b])
        reset.click(lambda: [0.0] * K, None, sliders)
        go.click(ex.record, controls + [mode, frames, fps], [anim, where])
    return demo


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--network", help="network-snapshot .pkl; omit for the stub generator")
    p.add_argument("--device", help="cuda | mps | cpu (default: best available)")
    p.add_argument("--port", type=int, default=7860)
    p.add_argument("--share", action="store_true")
    a = p.parse_args()
    build(Explorer(a.network, a.device)).launch(server_port=a.port, share=a.share, inbrowser=True,
        css="#sky img{width:100%;height:auto}", allowed_paths=[str(OUT.resolve())])
