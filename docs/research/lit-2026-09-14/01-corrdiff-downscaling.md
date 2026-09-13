# Generative downscaling from NWP — NVIDIA CorrDiff and relatives
_Agent run 2026-09-14. 10 items, 10 verified, 0 unverified._

## CorrDiff (original) — 2023/2024
- **Title:** Residual Corrective Diffusion Modeling for Km-scale Atmospheric Downscaling
- **Authors/venue:** Mardani, Brenowitz, Cohen, Pathak, Chen, Liu, Vahdat, Nabian, Ge, Subramaniam, Kashinath, Kautz, Pritchard. arXiv 2309.15214 (posted Sep 2023, v2 Aug 2024); also published as *Communications Earth & Environment* (2025), DOI 10.1038/s43247-025-02042-5.
- **Fetched:** https://arxiv.org/abs/2309.15214 (VERIFIED)
- **Input -> output:** 25 km global reanalysis/GFS-scale fields -> 2 km regional weather over Taiwan (Taiwan Central Weather Administration model domain). Two-stage architecture: a UNet regression predicts the conditional mean, then a diffusion model (EDM-style) predicts the residual correction on top. Demonstrated on Typhoon Haikui (2023): reduced radius-of-max-winds from 75 km (ERA5) to ~50 km and raised peak wind from 22 to 33 m/s.
- **Code / weights / licence:** Reference implementation lives in NVIDIA's open-source `physicsnemo` repo (`examples/weather/corrdiff`), Apache-licensed. The Taiwan training dataset itself is CC BY-NC-ND 4.0 (non-commercial, no redistribution of derivatives). No standalone Taiwan checkpoint is publicly released for download (see PhysicsNeMo entry below — only the CONUS GEFS-HRRR checkpoint is distributed).
- **Data:** Taiwan CWA regional NWP output (2 km) conditioned on 25 km global reanalysis.
- **For HRRR+GOES:** This is the founding recipe (regression-mean + residual-diffusion) that the whole family below inherits. Output channels are physical state variables (wind, temp, precip), not image/radiance fields, and the input is coarse reanalysis, not HRRR — so it's the architecture template, not a drop-in HRRR->image pipeline.

## StormCast — 2024
- **Title:** StormCast: Kilometer-Scale Convection Allowing Model Emulation using Generative Diffusion Modeling
- **Authors/venue:** Pathak, Cohen, Garg, Harrington, Brenowitz, Durran, Mardani, Vahdat, Xu, Kashinath, Pritchard. arXiv 2408.10958 (Aug 2024), physics.ao-ph.
- **Fetched:** https://arxiv.org/abs/2408.10958 (VERIFIED)
- **Input -> output:** Conditioned on 26 synoptic-scale variables, autoregressively predicts 99 HRRR state variables at ~3 km / 1-hour steps for 1-6 hour lead times, CONUS domain. Output explicitly includes composite radar reflectivity and cold-pool morphology (i.e., HRRR itself is the emulation target).
- **Code / weights / licence:** Ships as a checkpoint + diagnostic model in NVIDIA PhysicsNeMo / Earth2Studio ("StormCast-CONUS"). NGC checkpoint `stormcast-v1-era5-hrrr`: Apache 2.0, downloadable via NGC CLI (763.92 MB), but labelled "for research and development only."
- **Data:** ERA5 (Jul 2018-Dec 2021) paired with HRRR analyses over the same period, CONUS.
- **For HRRR+GOES:** Closest existing system to "HRRR in spirit, generative image-like output out" — it emulates HRRR itself and one of its 99 output channels (radar reflectivity) is genuinely image-like. Strong evidence the residual/diffusion recipe generalizes past state-variable regression into radar-image space; the Apache-2.0 checkpoint is a legitimate architecture reference even though it doesn't touch GOES.

## NVIDIA PhysicsNeMo CorrDiff example + CorrDiff-Mini — code repo
- **Title:** `physicsnemo/examples/weather/corrdiff` (README)
- **Authors/venue:** NVIDIA, open-source repo `NVIDIA/physicsnemo` (formerly Modulus).
- **Fetched:** https://github.com/NVIDIA/physicsnemo/blob/main/examples/weather/corrdiff/README.md (VERIFIED)
- **Input -> output:** Configurable regression+diffusion pipeline supporting both the Taiwan config (ERA5 -> 2 km CWA) and a CONUS config (GEFS 25 km -> 3 km HRRR). "CorrDiff-Mini" is an explicitly lightweight variant — smaller network + a reduced training set **based on HRRR** — that cuts training from thousands of A100 GPU-hours to ~10 hours on A100s. Also supports a patch-based diffusion mode (trains/generates on small tiles of the target region for scalability).
- **Code / weights / licence:** Apache-licensed repo. Full CONUS CorrDiff training is reported at **~5,000 A100 GPU-hours** (~80 wall-clock hours on 64 GPUs) — this is the number to budget against. Released pretrained checkpoints are explicitly flagged as "not necessarily compatible with the current `train.py`/`generate.py`" and recommended for inference-only use via Earth2Studio, not as fine-tuning starting points in this repo.
- **Data:** Taiwan CWA + GEFS-HRRR CONUS.
- **For HRRR+GOES:** CorrDiff-Mini is the realistic on-ramp for a solo prototype — a small, genuinely HRRR-conditioned reference implementation (~10 A100-hrs) that could be retargeted toward a GOES-image output head, versus reimplementing the regression+diffusion pipeline from the paper. The ~5,000 A100-hr full-scale number is the ceiling if the mini version doesn't generalize.

## NGC checkpoint: Earth-2 CorrDiff US (GEFS-HRRR)
- **Title:** Earth-2 CorrDiff US GEFS-HRRR (NGC model)
- **Authors/venue:** NVIDIA, NGC model catalog.
- **Fetched:** https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/models/earth2-corrdiff-us-gefs-hrrr (VERIFIED)
- **Input -> output:** Input = 38 surface/atmospheric GEFS forecast variables + lead time on a 0.25° CONUS grid (129x301). Output = 8 surface variables (wind, temperature, precipitation, precipitation type) at 3 km HRRR-grid resolution, Lambert conformal (1056x1792).
- **Code / weights / licence:** NVIDIA AI Foundation Models Community License + NVIDIA AI Product Agreement. Requires NGC login and a subscription tier (a free NVIDIA Developer Program tier exists). Checkpoint is 3.89 GB compressed.
- **Data:** GEFS (25 km forecast) paired with HRRR (3 km) analyses, CONUS.
- **For HRRR+GOES: important nuance** — in this checkpoint **HRRR is the high-resolution TARGET, not the input.** The input is coarse GEFS (25 km); CorrDiff learns to produce HRRR-like fields. So the literal answer to "does a released CorrDiff checkpoint take HRRR as input" is **no** for this one — it takes GEFS in and predicts HRRR-resolution *state variables* out, none of which are image-like (radiance/reflectivity/brightness-temp). Using it as-is would not give Derek an image channel; he'd need his own HRRR->GOES-image training, following this checkpoint mainly as an I/O-shape template.

## NGC checkpoint: StormCast-V1-ERA5-HRRR
- **Title:** StormCast-V1-ERA5-HRRR (PhysicsNeMo Checkpoints)
- **Authors/venue:** NVIDIA, NGC model catalog (`orgs/nvidia/teams/earth-2`).
- **Fetched:** https://catalog.ngc.nvidia.com/orgs/nvidia/teams/earth-2/models/stormcast-v1-era5-hrrr (VERIFIED)
- **Input -> output:** 125 input variables (99 state variables + 26 ERA5-derived conditioning variables) -> 99 output state/surface variables at km-scale, 1-hour autoregressive step, including radar reflectivity across multiple atmospheric layers.
- **Code / weights / licence:** Apache 2.0. Downloadable via NGC CLI, 763.92 MB (v1.0.1). Explicitly scoped "for research and development only."
- **Data:** ERA5 (Jul 2018-Dec 2021) + HRRR same period.
- **For HRRR+GOES:** Here HRRR-derived state genuinely round-trips as both conditioning and predicted output (the model emulates HRRR), and radar reflectivity is a real image-like output channel already present. This is the strongest concrete evidence a HRRR-conditioned generative model can output an image-like field, with a freely downloadable, permissively licensed (Apache 2.0, non-commercial-in-practice via "research only" framing) checkpoint to inspect or fine-tune.

## NVIDIA Earth2Studio — framework
- **Title:** Earth2Studio
- **Authors/venue:** NVIDIA, open-source repo `NVIDIA/earth2studio`.
- **Fetched:** https://github.com/NVIDIA/earth2studio and https://nvidia.github.io/earth2studio/main/ (VERIFIED)
- **Input -> output:** N/A — this is an inference-orchestration framework, not a model. It wraps prognostic models (FourCastNet 3, Pangu-Weather, GraphCast, Aurora, ACE-2, AIFS 2.0) and diagnostic models including **StormCast-CONUS**, **CorrDiff**, StormScope, and DLESyM, plus data connectors for NOAA (HRRR lives here), ECMWF, NASA, EUMETSAT, AWS Open Data, and others.
- **Code / weights / licence:** Apache License 2.0, fully open source, actively developed (700+ commits at time of fetch).
- **Data:** n/a (orchestration layer over the data sources above).
- **For HRRR+GOES:** The realistic path to a running prototype is installing Earth2Studio, pulling live/archival HRRR through its NOAA connector, and invoking the StormCast-CONUS or CorrDiff diagnostic model directly — rather than reimplementing CorrDiff's data plumbing and inference loop from the paper.

## nvidia/corrdiff-cmip6-era5 — Hugging Face checkpoint
- **Title:** CorrDiff-CMIP6-ERA5
- **Authors/venue:** NVIDIA, Hugging Face model card.
- **Fetched:** https://huggingface.co/nvidia/corrdiff-cmip6-era5 (VERIFIED)
- **Input -> output:** CMIP6 global climate output (2.8°, 64x128 grid, 24-hr steps, 74 variables) -> ERA5-like global reanalysis (25 km, 721x1440 grid, 1-hr steps, 75 variables). U-Net corrector-diffusion model, 158M parameters.
- **Code / weights / licence:** **NVIDIA Open Model License** — explicitly "ready for commercial/non-commercial use." Weights are downloadable directly from Hugging Face with no NGC subscription gate.
- **Data:** CMIP6 (decadal slices 1981-2016) matched to ERA5 for the same years.
- **For HRRR+GOES:** Not CONUS/HRRR-relevant physically (global climate downscaler, opposite scale direction from Derek's need), but it's the one CorrDiff checkpoint that's openly licensed for commercial use AND freely downloadable with no gate — useful as a template for how NVIDIA packages CorrDiff config/preprocessing/inference code, and a data point that NVIDIA does sometimes release CorrDiff weights under a genuinely permissive licence.

## Harris et al. — 2022
- **Title:** A Generative Deep Learning Approach to Stochastic Downscaling of Precipitation Forecasts
- **Authors/venue:** Lucy Harris, Andrew T. T. McRae, Matthew Chantry, Peter D. Dueben, Tim N. Palmer. arXiv 2204.02028 (Apr 2022); published *JAMES* 2022, DOI 10.1029/2022MS003120.
- **Fetched:** https://arxiv.org/abs/2204.02028 (VERIFIED)
- **Input -> output:** Low-resolution NWP precipitation forecast fields -> high-resolution radar-like precipitation fields (UK domain), via a conditional GAN trained against radar composite ground truth; produces stochastic ensembles.
- **Code / weights / licence:** No code/weights link surfaced on the abstract page — treat as **none found**. Standard arXiv distribution licence only.
- **Data:** Met Office NWP forecasts + UK radar-derived precipitation composites (an image-like ground truth).
- **For HRRR+GOES:** The direct conceptual ancestor of "condition an image-shaped model on coarse NWP fields to paint a fine radar/satellite-like texture" — pre-diffusion (GAN), precipitation-only, UK-scale, but establishes that the paired coarse-field / fine-image-texture training setup works and produces visually radar-like, spatially coherent output.

## Leinonen, Nerini & Berne — 2020/2021
- **Title:** Stochastic Super-Resolution for Downscaling Time-Evolving Atmospheric Fields with a Generative Adversarial Network
- **Authors/venue:** Jussi Leinonen, Daniele Nerini, Alexis Berne. arXiv 2005.10374 (May 2020); published *IEEE Transactions on Geoscience and Remote Sensing* 59(9):7211-7223 (2021).
- **Fetched:** https://arxiv.org/abs/2005.10374 (VERIFIED)
- **Input -> output:** Low-resolution time sequences of an atmospheric field -> temporally-consistent high-resolution sequences, via a recurrent stochastic super-resolution GAN. Tested on (a) MeteoSwiss radar-measured precipitation and (b) **GOES-16 cloud optical thickness** — i.e., an actual satellite-derived image field.
- **Code / weights / licence:** Code released at https://github.com/jleinonen/downscaling-rnn-gan. No explicit licence stated on the arXiv page.
- **Data:** Swiss radar precipitation (3-month dataset); GOES-16 cloud optical thickness.
- **For HRRR+GOES:** The closest prior-art match to Derek's exact modality in the whole set — it already super-resolves a GOES-derived satellite field with a GAN into temporally-coherent animated sequences, i.e. "a generative model paints a moving satellite-like sky." Pre-diffusion and small-scale by today's standards, but the public code repo is a plausible architecture to study even outside the CorrDiff lineage.

## Watt & Mansfield — 2024
- **Title:** Generative Diffusion-based Downscaling for Climate
- **Authors/venue:** Robbie A. Watt, Laura A. Mansfield. arXiv 2404.17752 (Apr 2024), physics.ao-ph.
- **Fetched:** https://arxiv.org/abs/2404.17752 (VERIFIED)
- **Input -> output:** Coarse climate fields (2°) -> high-resolution ERA5-like fields (0.25°), global, via a conditional/guided diffusion model (reported simpler to train than a full conditional model, using reconstruction guidance rather than a bespoke residual-correction architecture).
- **Code / weights / licence:** No code/weights link found in the abstract. Paper itself carries a CC BY-NC-ND 4.0 badge.
- **Data:** ERA5.
- **For HRRR+GOES:** A lighter-weight alternative recipe — a generic guided-diffusion conditioning method rather than CorrDiff's two-stage regression+residual-diffusion — worth a fallback prototype if adapting the full CorrDiff pipeline (and its Modulus/PhysicsNeMo dependency stack) proves too heavy for a single-output-channel experiment.

## Summary for the decision
CorrDiff's actual recipe — a regression UNet for the mean plus an EDM-style residual diffusion model for the correction — is real, open-sourced (Apache 2.0, `NVIDIA/physicsnemo`), and has a genuinely cheap on-ramp: CorrDiff-Mini trains on a reduced HRRR-based dataset in ~10 A100-hours, versus ~5,000 A100-hours for the full CONUS model. But no released CorrDiff checkpoint takes HRRR as *input* and produces an image-like field as output: the CONUS GEFS-HRRR checkpoint takes coarse GEFS in and predicts HRRR-resolution *state variables* (wind, temp, precip) out, not radiance or reflectivity images. The closest thing to Derek's exact HRRR-in/image-out setup is StormCast, whose released checkpoint (Apache 2.0, "research only") emulates HRRR itself and includes composite radar reflectivity as one of its 99 output channels — the best evidence in this set that the family can produce an image-like channel from HRRR-adjacent conditioning. Leinonen et al. 2021 is the closest match on modality alone: a GAN, not a diffusion model, but it already super-resolves an actual GOES-derived field (cloud optical thickness) into animated sequences, with public code. Harris et al. 2022 and Watt & Mansfield 2024 round out the method space (GAN vs. lighter guided-diffusion) without touching HRRR or satellite imagery directly. Earth2Studio (Apache 2.0) is the practical harness — it already wires up HRRR ingestion and both CorrDiff and StormCast diagnostic models, so the fastest real prototype path is running StormCast-CONUS or CorrDiff-Mini through Earth2Studio and inspecting whether their outputs (or an added GOES-image head trained on top of CorrDiff-Mini's HRRR conditioning) can be pushed toward brightness-temperature or reflectance textures. The biggest gap: nobody in this set has published a HRRR -> GOES-band-image generator; every image-like output found (StormCast's radar reflectivity, Leinonen's cloud optical thickness) is a physical retrieval field, not a satellite-band render, so reaching a true "paints a GOES frame" output means either training a new output head on CorrDiff-Mini/StormCast infrastructure or treating the existing StyleGAN2-on-GOES-frames approach as the only proven route to that specific visual target today.
