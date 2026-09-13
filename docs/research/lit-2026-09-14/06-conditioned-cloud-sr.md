# Conditional image generation from coarse cloud fields
_Agent run 2026-09-14. 10 items, 10 verified, 0 unverified._

## Leinonen GAN downscaling — 2020/2021
- **Title:** Stochastic Super-Resolution for Downscaling Time-Evolving Atmospheric Fields with a Generative Adversarial Network
- **Authors/venue:** Jussi Leinonen, Daniele Nerini, Alexis Berne. IEEE Transactions on Geoscience and Remote Sensing, Vol. 59, No. 9, pp. 7211–7223 (2021); posted arXiv May 2020.
- **Fetched:** https://arxiv.org/abs/2005.10374 (VERIFIED) ; code repo https://github.com/jleinonen/downscaling-rnn-gan (VERIFIED)
- **Input -> output:** A recurrent, stochastic super-resolution GAN takes a low-resolution *sequence* of images and outputs an ensemble of temporally-consistent high-resolution sequences. Tested on two datasets: MCH-RZC radar precipitation (Switzerland) and GOES-16 cloud optical thickness (GOES-COT) — i.e. it super-resolves a coarse satellite-derived cloud field, not a raw radiance image.
- **Code / weights / licence:** Code public on GitHub (reference implementation, includes GOES-COT data pointers); no pretrained weights bundled, licence not checked on repo page.
- **Data:** Radar precipitation (Switzerland) + GOES-16 cloud optical thickness.
- **For HRRR+GOES:** This is the closest published precedent to "coarse physical cloud field in, realistic-looking satellite-scale cloud texture out" — it is literally GOES cloud-field super-resolution with a GAN, and it's an ensemble/stochastic method (multiple plausible high-res realizations from one coarse input), which maps directly onto wanting varied, non-deterministic sky texture from a single HRRR cloud-fraction frame. Old (2020) architecture — a straight recurrent conv-GAN, not diffusion/ControlNet — but the open code is a legitimate baseline to benchmark against before reaching for a heavier SD pipeline.

## CorrDiff (NVIDIA) — 2023
- **Title:** Residual Corrective Diffusion Modeling for Km-scale Atmospheric Downscaling
- **Authors/venue:** Morteza Mardani, Noah Brenowitz, Yair Cohen, Jaideep Pathak, Chieh-Yu Chen, Cheng-Chin Liu, Arash Vahdat, Mohammad Amin Nabian, Tao Ge, Akshay Subramaniam, Karthik Kashinath, Jan Kautz, Mike Pritchard. Published in Communications Earth & Environment (2025); arXiv Sept 2023.
- **Fetched:** https://arxiv.org/abs/2309.15214 (VERIFIED); code/weights via https://github.com/NVIDIA/earth2studio (VERIFIED — CorrDiff is a shipped `earth2studio` diagnostic model)
- **Input -> output:** 25 km global reanalysis -> 2 km regional-weather-model fields over Taiwan. Two-stage: a UNet regression predicts the mean field, then a residual *corrector* diffusion model adds the sub-grid-scale, multi-scale physical texture the regression smooths away.
- **Code / weights / licence:** Pretrained CorrDiff weights are distributed via the NVIDIA NGC model registry (`ngc://models/nvidia/modulus/corrdiff_inference_package@1`) and loaded automatically by `earth2studio`'s `CorrDiffCosmoEra5` diagnostic model — i.e. yes, runnable pretrained weights exist, plus a newer COSMO-REA6/ERA5 downscaler variant. Paper itself CC BY 4.0.
- **Data:** 25 km reanalysis + a 2 km regional NWP simulation (Taiwan domain) as paired training data.
- **For HRRR+GOES:** This is the exact pattern named in the brief (physical field in, corrective diffusion adds texture out) and it is production code with pretrained weights you can actually run today via `earth2studio` — but note it downscales one NWP field to another NWP field (25 km reanalysis -> 2 km regional model), not NWP-to-satellite-image. It's the strongest architectural template (regression-mean + diffusion-residual, avoiding mode-averaging) but would need re-training (or at least re-conditioning) to target GOES-like RGB/brightness-temperature output instead of another physical grid.

## DiffusionSat — 2023/2024
- **Title:** DiffusionSat: A Generative Foundation Model for Satellite Imagery
- **Authors/venue:** Samar Khanna, Patrick Liu, Linqi Zhou, Chenlin Meng, Robin Rombach, Marshall Burke, David Lobell, Stefano Ermon. ICLR 2024.
- **Fetched:** https://arxiv.org/abs/2312.03606 (VERIFIED)
- **Input -> output:** A latent-diffusion foundation model for satellite imagery, conditioned on numerical metadata (geolocation, time, satellite/sensor keys) instead of text captions. Supports temporal generation, super-resolution, and inpainting as downstream conditional tasks on top of one pretrained base — not ControlNet per se, but the same "freeze a big pretrained generator, add a lightweight conditioning head" philosophy.
- **Code / weights / licence:** Project page/code referenced (fal-ai/Stanford project site); CC BY 4.0 on the paper. Pretrained weights: released alongside the ICLR paper (not independently re-verified in this pass — treat as likely available given the project-page pattern, but confirm before relying on it).
- **Data:** Multiple public large high-resolution remote-sensing datasets (multi-spectral, irregularly sampled) — not cloud/GOES-specific.
- **For HRRR+GOES:** Useful mainly as evidence that a single pretrained satellite-imagery diffusion backbone can be metadata-conditioned for several tasks at once (temporal interpolation + super-res + inpainting) rather than training one narrow model per task — relevant if the eventual system wants to reuse one backbone for both the "HRRR->GOES-texture" task and frame interpolation between 10-min GOES frames. It is not conditioned on physical cloud fields and is not cloud-specific, so it's an architecture reference, not a drop-in model.

## CloudDiff — 2024/2025
- **Title:** High-resolution ensemble retrieval of cloud properties for all-day based on geostationary satellite
- **Authors/venue:** Haixia Xiao, Feng Zhang, Lingxiao Wang, Baoxiang Pan, Yannian Zhu, Minghuai Wang, Wenwen Li, Bin Guo, Jun Li. npj Climate and Atmospheric Science (2025); arXiv May 2024 (revised Oct 2025).
- **Fetched:** https://arxiv.org/abs/2405.04483 (VERIFIED)
- **Input -> output:** Himawari-8 AHI thermal-infrared radiances (2 km) -> cloud properties (phase, liquid/ice water path) at 1 km, day AND night, as an ensemble (uncertainty-quantified) rather than a single deterministic map.
- **Code / weights / licence:** Not stated in the abstract page; unknown.
- **Data:** Himawari-8/AHI TIR as input, MODIS cloud products as supervised target.
- **For HRRR+GOES:** Direct precedent for "generative diffusion model, geostationary satellite, cloud-field super-resolution, ensemble output" using a different satellite family (Himawari) — architecture and evaluation-against-MODIS/CALIPSO protocol are transferable to a GOES+HRRR ensemble-cloud-texture generator, and its day/night handling (using only TIR, no reflectance) is relevant if the eventual generator needs to run at night when GOES visible bands go dark.

## Cascaded Diffusion Inversion for cloud microstructure — 2026
- **Title:** Recovering Cloud Microstructures with Cascaded Diffusion Inversion
- **Authors/venue:** Hanan Gani, Guy Pulik, Daniel Rosenfeld, Duncan Watson-Parris, Salman Khan. ML4RS Workshop, ICLR 2026.
- **Fetched:** https://arxiv.org/abs/2607.05637 (VERIFIED)
- **Input -> output:** Low-resolution multi-spectral satellite cloud imagery -> 4x super-resolved cloud microstructure (fine features like convective turrets and cloud gaps), via a two-stage cascade: stage 1 trains on real paired sensor data for degradation/alignment, stage 2 self-supervises on downsampled high-res imagery to sharpen texture.
- **Code / weights / licence:** Paper states "code and models are publicly available" on GitHub (link in paper; not independently opened in this pass — verify link before citing further).
- **Data:** Real paired multi-spectral satellite sensor data (dataset name/size not disclosed in the abstract).
- **For HRRR+GOES:** A recent (2026), explicitly cloud-specific diffusion super-resolution pipeline with claimed public code — worth cloning first among the "pure SR" candidates here to check exact GPU/data budget, since it targets the same km-scale cloud-texture problem (just satellite-to-satellite SR rather than model-to-satellite).

## SD + ControlNet for satellite imagery (urban planning) — 2025
- **Title:** Generative AI for Urban Planning: Synthesizing Satellite Imagery via Diffusion Models
- **Authors/venue:** Qingyi Wang, Yuebing Liang, Yunhan Zheng, Kaiyuan Xu, Jinhua Zhao, Shenhao Wang. arXiv, 2025 (cs.CV).
- **Fetched:** https://arxiv.org/abs/2505.08833 (VERIFIED)
- **Input -> output:** Fine-tunes an off-the-shelf Stable Diffusion checkpoint extended with ControlNet, conditioned on OSM-derived control imagery (roads, water bodies) plus land-use/infrastructure text, to synthesize satellite imagery for three major US cities.
- **Code / weights / licence:** Not stated on the abstract page (checked; unknown). Paper itself CC BY 4.0.
- **Data:** Satellite imagery paired with OpenStreetMap layers across three US cities; exact image count not given in the abstract.
- **For HRRR+GOES:** This is exactly the "off-the-shelf SD + ControlNet fine-tune on satellite imagery" pattern the brief is asking about, but the conditioning signal is a discrete map/vector layer (roads, land parcels), not a continuous coarse physical field — so it validates that SD+ControlNet fine-tunes on satellite-scale imagery work at all, but does not answer the "smallest training set / GPU budget" question (that number is not disclosed in the abstract; would need the PDF's implementation section to get it, out of this pass's budget).

## SatelliteMaker (SD + LoRA + ControlNet on DEM) — 2025
- **Title:** A Diffusion-Based Framework for Terrain-Aware Remote Sensing Image Reconstruction (model name: SatelliteMaker)
- **Authors/venue:** Zhenyu Yu, Mohd Yamani Inda Idris, Pei Wang. arXiv, April 2025 (cs.CV).
- **Fetched:** https://arxiv.org/abs/2504.12112 (VERIFIED)
- **Input -> output:** Corrupted/incomplete multi-band satellite imagery (cloud gaps, sensor dropouts) + a Digital Elevation Model, reconstructed into complete imagery. Uses LoRA to specialize Stable Diffusion to the satellite domain, then ControlNet conditioned on DEM for spatial accuracy, plus a VGG-Adapter to fix cross-band colour/style drift.
- **Code / weights / licence:** Not stated on the abstract page; unknown. Training dataset size and GPU budget also not given in the abstract.
- **Data:** Multi-band remote sensing imagery + DEM (dataset name/size unknown from abstract).
- **For HRRR+GOES:** Second direct example of the exact recipe named in the brief — take a public SD checkpoint, LoRA it into the satellite-image domain, then bolt on a ControlNet head keyed to a *continuous, low-resolution physical raster* (DEM here, cloud-fraction/brightness-temperature in our case) instead of vector/text conditioning. Confirms the recipe is used with terrain rasters specifically, which is architecturally closer to conditioning on an HRRR field than the OSM-vector paper above — but again, no training-set-size or GPU-hours figure surfaced in the abstract; would need the paper body or repo to pin down the brief's "smallest demonstrated" question.

## Diffusion bridges for unpaired fluid-flow downscaling (Bischoff & Deck) — 2023
- **Title:** Unpaired Downscaling of Fluid Flows with Diffusion Bridges
- **Authors/venue:** Tobias Bischoff, Katherine Deck. Submitted to Artificial Intelligence for the Earth Systems; arXiv May 2023.
- **Fetched:** https://arxiv.org/abs/2305.01822 (VERIFIED)
- **Input -> output:** Coarse-grained geophysical fluid-simulation fields (e.g. temperature, precipitation) -> fine-grained high-resolution fields, using two independently-trained conditional diffusion models chained via matched Fourier-spectrum statistics — no paired low/high-res training examples required.
- **Code / weights / licence:** Not stated on the abstract page; unknown.
- **Data:** Low- and high-resolution geophysical fluid simulation output (unpaired; specific simulation/dataset not detailed in the abstract).
- **For HRRR+GOES:** Directly relevant if HRRR cloud fields and a curated GOES cloud-texture corpus can't be cleanly time/space-paired (e.g. different projections, different native cadence) — this shows a route to a generator that never needs matched (HRRR-frame, GOES-frame) pairs, just marginal statistics of each domain, which would remove a major data-engineering burden for the "beauty with some truth" system.

## Two-stage diffusion for solar forecasting from NWP (Hatanaka et al.) — 2023
- **Title:** Diffusion Models for High-Resolution Solar Forecasts
- **Authors/venue:** Yusuke Hatanaka, Yannik Glaser, Geoff Galgon, Giuseppe Torri, Peter Sadowski. arXiv, Feb 2023 (cs.LG).
- **Fetched:** https://arxiv.org/abs/2302.00170 (VERIFIED)
- **Input -> output:** A score-based diffusion pipeline that super-resolves coarse-resolution numerical weather prediction (NWP) output into high-resolution weather-satellite-like cloud-cover imagery, for solar-irradiance forecasting; reported (via search snippet, not independently confirmed on the abstract page itself) as a two-stage cascade generating a 64x64 cloud-cover image from atmospheric variables, then upsampling to 128x128 — note the abstract page fetched did not itself state these pixel sizes, so treat the exact numbers as secondary-source and re-check in the PDF if load-bearing.
- **Code / weights / licence:** Not stated on the abstract page; unknown.
- **Data:** Coarse NWP fields as input; high-resolution weather-satellite observations as the target/training image domain (exact satellite/dataset not named on the abstract page).
- **For HRRR+GOES:** This is the paper structurally closest to the brief's actual ask — "a physics model's coarse field in, a diffusion model paints a satellite-like cloud image out" — done already, by an academic group, at modest resolution (tens to ~128 px), which suggests the HRRR->GOES-texture task is tractable at small scale before investing in a full 768 px web-mercator SD/ControlNet pipeline. Worth pulling the PDF (not just the abstract) for their exact architecture, resolution, and compute budget before designing the CorrDiff-style system.

## Deep Diffusion Model of Satellite data (DDMS) — 2024
- **Title:** Four-hour thunderstorm nowcasting using a deep diffusion model of satellite data
- **Authors/venue:** Kuai Dai, Xutao Li, Junying Fang, Yunming Ye, Demin Yu, Hui Su, Di Xian, Danyu Qin, Jingsong Wang. PNAS (2025); arXiv April 2024 (latest revision Dec 2025).
- **Fetched:** https://arxiv.org/abs/2404.10512 (VERIFIED)
- **Input -> output:** FengYun-4A geostationary brightness-temperature imagery at 4 km / 15-minute cadence -> forecast (nowcast) frames up to 4 hours ahead over ~20,000,000 km^2, generated by a diffusion model that learns the spatiotemporal evolution of convective cloud fields directly from the satellite time series.
- **Code / weights / licence:** Not indicated as publicly available on the abstract page; unknown.
- **Data:** FengYun-4A satellite observations (dataset size not disclosed in the abstract).
- **For HRRR+GOES:** This is the closest match in the search to "video diffusion for satellite time series" / temporal generation at the 10-15 minute cadence the brief cares about — it treats a stack of geostationary frames as a generative video-diffusion problem and extrapolates forward, which is the same mechanism needed for smooth frame-to-frame animation (or gap-filling between real GOES frames) rather than single-frame super-resolution. It forecasts brightness temperature, not RGB cloud texture, so a final system would still need a texture-rendering step on top of whatever this predicts.

## Summary for the decision
Nothing in this pass is an exact match for "HRRR 3 km physical cloud fields in, 768 px web-mercator GOES-style texture out" — but the pieces exist and are separable. The closest single precedent to the brief's actual pipeline is Hatanaka et al. (2023), which already does coarse-NWP-field -> satellite-cloud-image diffusion, just at much lower resolution and with an unverified compute budget (get the PDF next). CorrDiff (Mardani et al., NVIDIA) is the strongest *architectural* template — regression-mean-plus-residual-diffusion — and is the only item here with runnable pretrained weights and shipped inference code (via `earth2studio`), though it downscales NWP-to-NWP, not NWP-to-satellite-image. Two papers (Generative AI for Urban Planning; SatelliteMaker) confirm the literal "off-the-shelf Stable Diffusion + ControlNet fine-tuned on satellite imagery" recipe works, one with a continuous physical raster (DEM) as the conditioning signal, which is the closest structural analog to conditioning on an HRRR field — but **neither abstract discloses a training-set size or GPU budget**, so this pass could not answer the brief's specific question about the smallest demonstrated ControlNet-style fine-tune; that requires reading the paper bodies or the SatelliteMaker/Urban-Planning repos directly. Leinonen et al. (2020, open code) is the oldest but most concretely on-topic result: a stochastic GAN doing exactly "GOES cloud optical thickness field in, ensemble of higher-resolution cloud fields out," and is a legitimate cheap baseline to beat. For the 10-minute-cadence animation problem specifically, Dai et al.'s DDMS (2024) is the best precedent for treating a satellite time series as a video-diffusion/extrapolation problem rather than single-frame SR. The biggest gap: no paper found here fine-tunes a public SD/ControlNet checkpoint on GOES/cloud imagery specifically conditioned on a *physical NWP field* (as opposed to a map/DEM layer) — that combination (Stable Diffusion + ControlNet + HRRR-field conditioning, at 768 px) appears to be genuinely unbuilt, meaning CorrDiff's non-SD-based diffusion architecture (or a fresh small-scale replication of Hatanaka et al. at higher resolution) is probably the safer near-term build path than assuming a large SD/ControlNet fine-tune is a solved problem at any budget.
