"""Measure a vision labeler before trusting it: cost, latency, and consistency, per condition.

Conditions on the same tiles:
  single         one tile per call, no metadata
  single+meta    one tile + region / lat-lon / sensor / date / window
  grid2 / grid3 / grid4   2x2, 3x3, 4x4 tiles stitched into one image with a labelled
                          grid, metadata per cell -- because Gemini bills a whole image
                          up to ~768 px as one unit, so a 3x3 mosaic costs about what one
                          tile costs in image tokens.

Quality here is measured without human verdicts (those come from the triage page):
  - coverage: correlation between the model's cloud_cover and the pixel-counted cloud_frac
  - artifacts: does it flag swath_gap on tiles the black-pixel count says have one (positive control)
  - stability: agreement between stitched and single-tile labels on the same tile

    set -a; source ~/sourcelibrary/.env.production.local; set +a
    python scripts/label_probe.py --n 72 --out scratch/probe

Realtime API, gemini-3.1-flash-lite, thinkingBudget 0, JSON schema output.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click
import numpy as np
import requests
from PIL import Image, ImageDraw

MODEL = os.environ.get("GEMINI_MODEL_PROBE", "gemini-3.1-flash-lite")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
PRICE_IN, PRICE_OUT = 0.25, 1.50  # $/M tokens, realtime, 2026-09-12 price page; batch is half

CLOUD_FORMS = ["closed_cells", "open_cells", "streets", "cirrus", "overcast", "convective", "frontal", "clear", "other"]
LANDFORMS = ["mountains", "delta", "salt", "agriculture", "dunes", "volcanic", "ice", "canyon", "rock", "wetland", "river", "reef", "water", "coast", "forest", "other"]
ARTIFACTS = ["swath_gap", "haze", "blur", "sun_glint", "coastline_in_ocean_tile", "cloud_in_land_tile", "none"]

LABEL = {
    "type": "OBJECT",
    "properties": {
        "cell": {"type": "STRING"},
        "surface": {"type": "STRING", "enum": ["ocean", "land", "coast", "mixed"]},
        "cloud_cover": {"type": "INTEGER", "description": "percent of the tile covered by cloud, 0-100"},
        "cloud_form": {"type": "STRING", "enum": CLOUD_FORMS},
        "landform": {"type": "STRING", "enum": LANDFORMS},
        "artifacts": {"type": "ARRAY", "items": {"type": "STRING", "enum": ARTIFACTS}},
        "usable": {"type": "INTEGER", "description": "1-5: would a curator keep this as a training example of its subject"},
        "note": {"type": "STRING", "description": "at most 12 words"},
    },
    "required": ["cell", "surface", "cloud_cover", "cloud_form", "landform", "artifacts", "usable", "note"],
}

SYSTEM = ("You label satellite image tiles for a generative model's training set. For each tile give: the surface type; "
          "cloud cover as a percent; the dominant cloud organisation; the dominant landform if land is visible (else 'other'); "
          "any artifacts -- swath_gap means a black wedge or band of missing data; and a usability score where 5 is a clean, "
          "well-structured example of its subject and 1 is featureless, corrupted, or off-subject. Be literal about what is in the pixels.")


def meta_line(t):
    if t["set"] == "landshapes":
        return f"Sentinel-2 cloud-free composite, region {t['reg']}, family hint {t['rgm']}, window {t['km']} km at ~{round(t['km']*1000/512)} m/px."
    src = {"modis": "MODIS", "viirs": "VIIRS", "s2": "Sentinel-2"}[t["src"]]
    return f"{src} true colour, region {t['reg']} ({t['rgm']}), date {t['date']}, window {t['km']} km at ~{round(t['km']*1000/256)} m/px."


def stitch(imgs, n):
    cell = imgs[0].width
    sheet = Image.new("RGB", (n * cell, n * cell), (255, 0, 255))
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(imgs):
        x, y = (i % n) * cell, (i // n) * cell
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + cell - 1, y + cell - 1], outline=(255, 0, 255), width=2)
        lab = f"{'ABCD'[i // n]}{i % n + 1}"
        d.rectangle([x + 3, y + 3, x + 34, y + 19], fill=(255, 0, 255))
        d.text((x + 6, y + 5), lab, fill=(255, 255, 255))
    return sheet


def call(image, prompt, many, key, retries=4):
    buf = io.BytesIO(); image.save(buf, "JPEG", quality=88)
    schema = {"type": "ARRAY", "items": LABEL} if many else LABEL
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}, {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": schema, "thinkingConfig": {"thinkingBudget": 0}},
    }
    for a in range(retries):
        t0 = time.time()
        r = requests.post(URL, params={"key": key}, json=body, timeout=120)
        dt = time.time() - t0
        if r.status_code == 200:
            j = r.json(); u = j.get("usageMetadata", {})
            text = j["candidates"][0]["content"]["parts"][0]["text"]
            return dict(ok=True, dt=dt, tin=u.get("promptTokenCount", 0), tout=u.get("candidatesTokenCount", 0),
                        tthink=u.get("thoughtsTokenCount", 0), labels=json.loads(text), bytes=len(buf.getvalue()))
        if r.status_code in (429, 500, 503):
            time.sleep(2 ** a); continue
        return dict(ok=False, dt=dt, err=f"{r.status_code} {r.text[:160]}")
    return dict(ok=False, dt=0, err="retries exhausted")


@click.command()
@click.option("--tiles", default="scratch/triage/tiles.min.json", type=click.Path(path_type=Path))
@click.option("--sheets", default="scratch/triage/sheets", type=click.Path(path_type=Path))
@click.option("--landshapes", default="scratch/ls_bundle", type=click.Path(path_type=Path), help="bundle with tiles.landshapes.json + full/")
@click.option("--n", default=72, type=int)
@click.option("--out", default="scratch/probe", type=click.Path(path_type=Path))
@click.option("--conditions", default="single,single+meta,grid2,grid3,grid4")
@click.option("--seed", default=7, type=int)
def main(tiles, sheets, landshapes, n, out, conditions, seed):
    key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY not set")
    rng = random.Random(seed)
    base = json.load(open(tiles))
    ls = json.load(open(landshapes / "tiles.landshapes.json")) if (landshapes / "tiles.landshapes.json").exists() else []
    n_ls = n // 4
    clouds = [t for t in base if t["set"] == "clouds256"]
    by_b = {}
    for t in clouds: by_b.setdefault(t["b"], []).append(t)
    per = (n - n_ls) // len(by_b)
    sample = [t for b in sorted(by_b) for t in rng.sample(by_b[b], per)]
    sample += rng.sample(ls, min(n_ls, len(ls)))
    sample = sample[:n]

    cache = {}
    def img(t):
        if t["set"] == "landshapes":
            return Image.open(landshapes / t["f"]).convert("RGB").resize((256, 256), Image.LANCZOS)
        if t["s"] not in cache: cache[t["s"]] = Image.open(sheets / f"sheet_{t['s']:02d}.jpg").convert("RGB")
        sh = cache[t["s"]]
        return sh.crop((t["ix"] * 256, t["iy"] * 256, t["ix"] * 256 + 256, t["iy"] * 256 + 256))

    out.mkdir(parents=True, exist_ok=True)
    results = {}   # cond -> {tile id -> label}
    usage = {}     # cond -> list of call dicts
    for cond in conditions.split(","):
        g = {"grid2": 2, "grid3": 3, "grid4": 4}.get(cond, 1)
        groups = [sample[i:i + g * g] for i in range(0, len(sample) - g * g + 1, g * g)] if g > 1 else [[t] for t in sample]
        def run(group):
            if g == 1:
                t = group[0]
                prompt = "Label this tile. cell = 'A1'." + (" " + meta_line(t) if cond.endswith("meta") else "")
                res = call(img(t), prompt, False, key)
                if res["ok"]: res["labels"] = [res["labels"]]
                return group, res
            im = stitch([img(t) for t in group], g)
            lines = [f"{'ABCD'[i // g]}{i % g + 1}: {meta_line(t)}" for i, t in enumerate(group)]
            prompt = (f"This image is a {g}x{g} grid of separate tiles divided by magenta lines, each labelled in its top-left corner. "
                      f"Return one label object per cell, {g*g} in total, in reading order.\n" + "\n".join(lines))
            return group, call(im, prompt, True, key)
        with ThreadPoolExecutor(4) as ex:
            got = list(ex.map(run, groups))
        results[cond], usage[cond] = {}, []
        for group, res in got:
            usage[cond].append(res)
            if not res["ok"]: continue
            labs = res["labels"]
            for i, t in enumerate(group):
                lab = labs[i] if i < len(labs) else None
                if lab: results[cond][t["id"]] = lab
        ok = [u for u in usage[cond] if u["ok"]]
        tiles_labelled = len(results[cond])
        tin, tout, think = sum(u["tin"] for u in ok), sum(u["tout"] for u in ok), sum(u["tthink"] for u in ok)
        cost = tin / 1e6 * PRICE_IN + (tout + think) / 1e6 * PRICE_OUT
        click.echo(f"{cond:12} calls {len(ok)}/{len(usage[cond])}  tiles {tiles_labelled}  in {tin} out {tout} think {think}  "
                   f"{tin/max(tiles_labelled,1):.0f}+{tout/max(tiles_labelled,1):.0f} tok/tile  ${cost:.4f} = ${cost/max(tiles_labelled,1)*1e3:.3f}/1K tiles  "
                   f"{st.median([u['dt'] for u in ok]) if ok else 0:.1f}s/call")
        errs = [u["err"] for u in usage[cond] if not u["ok"]]
        if errs: click.echo("   errors: " + "; ".join(errs[:3]))

    # ---- quality without humans ----
    click.echo("\nquality proxies")
    ref = "single+meta" if "single+meta" in results else next(iter(results))
    for cond, labs in results.items():
        ids = [t["id"] for t in sample if t["id"] in labs and t["set"] != "landshapes"]
        if not ids: continue
        cov = [labs[i]["cloud_cover"] for i in ids]; cf = [next(t for t in sample if t["id"] == i)["cf"] * 100 for i in ids]
        r = float(np.corrcoef(cov, cf)[0, 1]) if len(ids) > 2 else float("nan")
        gap_truth = [next(t for t in sample if t["id"] == i)["bk"] > 0.02 for i in ids]
        gap_pred = ["swath_gap" in labs[i]["artifacts"] for i in ids]
        tp = sum(a and b for a, b in zip(gap_truth, gap_pred)); fp = sum((not a) and b for a, b in zip(gap_truth, gap_pred)); pos = sum(gap_truth)
        agree = None
        if cond != ref:
            both = [i for i in labs if i in results[ref]]
            agree = (sum(labs[i]["cloud_form"] == results[ref][i]["cloud_form"] for i in both) / max(len(both), 1),
                     sum(abs(labs[i]["usable"] - results[ref][i]["usable"]) <= 1 for i in both) / max(len(both), 1))
        click.echo(f"{cond:12} cover~cloud_frac r={r:.2f}   swath_gap recall {tp}/{pos} fp {fp}" +
                   (f"   vs {ref}: cloud_form agree {agree[0]:.0%}, usable within 1 {agree[1]:.0%}" if agree else ""))

    json.dump({"sample": sample, "results": results, "usage": {k: [{kk: vv for kk, vv in u.items() if kk != "labels"} for u in v] for k, v in usage.items()}},
              open(out / "probe.json", "w"), indent=1)
    click.echo(f"\n-> {out / 'probe.json'}")


if __name__ == "__main__":
    main()
