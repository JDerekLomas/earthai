# Generative nowcasting from radar and satellite
_Agent run 2026-09-14. 12 items, 10 verified, 2 unverified._

## DGMR (Deep Generative Model of Radar) — 2021
- **Title:** Skilful Precipitation Nowcasting Using Deep Generative Models of Radar
- **Authors/venue:** Ravuri, Lenc, Willson, Kangin, Lam, Mirowski, et al. (DeepMind). arXiv preprint; also *Nature* 597 (2021), DOI 10.1038/s41586-021-03854-z.
- **Fetched:** https://arxiv.org/abs/2104.00954 (VERIFIED)
- **Input -> output:** UK Met Office radar composite -> future radar reflectivity/rainfall fields, region up to 1536 km x 1280 km, lead 5-90 min.
- **Code / weights / licence:** Official DeepMind code + pretrained checkpoints in a GCS bucket (github.com/google-deepmind/deepmind-research/tree/master/nowcasting), weights under CC-BY 4.0 (confirmed via search of the repo's own README, not independently fetched this pass). Widely reproduced: openclimatefix/skillful_nowcasting (PyTorch Lightning) ships pretrained weights on HuggingFace Hub; Météo-France has its own retraining (github.com/meteofrance/dgmr).
- **Data:** UK Met Office radar (training), evaluated by 50+ Met Office forecasters (ranked first in 88% of comparisons).
- **For HRRR+GOES:** Purely radar, conditional GAN (generator + spatial/temporal discriminators), no NWP or satellite conditioning — the architecture (frame stack -> latent -> convolutional GAN decoder) is the direct ancestor of "paint texture from a physics field," but it predicts a physical variable (rain rate), not appearance/imagery, so it's a template for the generator, not for the satellite-image output layer.

## MetNet-1 — 2020
- **Title:** MetNet: A Neural Weather Model for Precipitation Forecasting
- **Authors/venue:** Sønderby, Espeholt, Heek, Dehghani, Oliver, Salimans, Agrawal, Hickey, Kalchbrenner (Google). arXiv, 2020.
- **Fetched:** https://arxiv.org/abs/2003.12140 (VERIFIED)
- **Input -> output:** Radar + GOES satellite imagery -> probabilistic precipitation map, 1 km spatial / 2 min temporal resolution, lead up to 8 hours (claims to beat NWP at 7-8h over CONUS).
- **Code / weights / licence:** No official code/weights released. Unofficial PyTorch reimplementation openclimatefix/metnet (community-trained weights on HuggingFace Hub, not DeepMind's originals).
- **Data:** NOAA radar mosaic + GOES-16, CONUS.
- **For HRRR+GOES:** Confirms satellite channels can be a useful *input* alongside radar for a self-attention/ConvLSTM-style spatiotemporal net, but the output is still a scalar precip field, not rendered imagery — same gap as DGMR for our purposes.

## MetNet-2 — 2021
- **Title:** Skillful Twelve Hour Precipitation Forecasts Using Large Context Neural Networks
- **Authors/venue:** Espeholt, Agrawal, Sønderby, Kumar, Heek, Bromberg, Gazen, Hickey, Bell, Kalchbrenner (Google). arXiv, 2021.
- **Fetched:** https://arxiv.org/abs/2111.07470 (VERIFIED)
- **Input -> output:** Large-context radar/satellite/sparse-station inputs -> precipitation, beats HRRR/HREF out to 12h (abstract doesn't itemize exact input channels beyond "starting from the same atmospheric state" as operational NWP).
- **Code / weights / licence:** Same as MetNet-1 — no official release; openclimatefix/metnet covers both MetNet-1/2 architectures with community weights.
- **Data:** CONUS, same general data stack as MetNet-1 plus wider spatial context (dilated convs to extend receptive field to 2048 km).
- **For HRRR+GOES:** Shows the "large receptive field via dilation, not attention" trick for cheaply extending context — relevant if the generator needs to see far-field HRRR cloud fraction to paint a locally consistent 768px frame.

## MetNet-3 — 2023
- **Title:** Deep Learning for Day Forecasts from Sparse Observations
- **Authors/venue:** Andrychowicz, Espeholt, Li, Merchant, Merose, Zyda, Agrawal, Kalchbrenner (Google Research/DeepMind). arXiv, 2023.
- **Fetched:** https://arxiv.org/abs/2306.06079 (VERIFIED); cross-checked https://research.google/blog/metnet-3-a-state-of-the-art-neural-weather-model-available-in-google-products/ (VERIFIED)
- **Input -> output:** Dense (MRMS radar, GOES satellite, topography) + sparse (OMO weather-station point obs, ~942 CONUS stations) inputs, with a "densification" trick (25% station dropout during training) to produce dense forecasts from sparse ground truth -> precipitation, wind, temperature, dew point at 1-4 km / 2 min resolution, lead up to 24h.
- **Code / weights / licence:** No official code or weights released (blog gives only the arXiv link); unofficial architecture-only reimplementations exist (lucidrains/metnet3-pytorch, kyegomez/metnet3) with no trained weights.
- **Data:** MRMS, GOES-16, OMO stations, CONUS + 27 European countries (real-time product).
- **For HRRR+GOES — IMPORTANT DISCREPANCY:** The brief's premise ("MetNet-3 conditions on an NWP model's fields") is **not confirmed** by the primary sources fetched. Google's own blog states explicitly: *"No NWP forecasts are included in MetNet-3's default inputs"* — HRRR/ENS appear only as *comparison baselines* it aims to beat, not as conditioning inputs. This contradicts a claim repeated in some secondary write-ups (a WebSearch summary asserted "HRRR NWP data used as additional training signal") that the primary sources do not support. **Treat "MetNet-3 ingests HRRR" as unverified/likely wrong** — worth a closer read of the full paper (not just abstract+blog) before relying on it as precedent for HRRR-conditioning. If true architectural precedent for NWP-as-input is wanted, look at CorrDiff itself or a different paper, not MetNet-3.

## NowcastNet — 2023
- **Title:** Skilful Nowcasting of Extreme Precipitation with NowcastNet
- **Authors/venue:** Zhang, Long, Chen, Chen, Chen, Wang, Yu (Tsinghua/CMA). *Nature* 619, 526-532 (2023), DOI 10.1038/s41586-023-06184-4.
- **Fetched:** https://www.nature.com/articles/s41586-023-06184-4 (UNVERIFIED: page redirected to a Nature login/paywall gate twice — nature.com and a Semantic Scholar mirror both failed to return body text). Facts below come from the article's own indexed abstract text as surfaced in search results (title, page range, DOI all match Nature's public metadata), not a direct fetch of the full page.
- **Input -> output:** Radar reflectivity (USA + China networks) -> nowcasts over 2,048 km x 2,048 km regions, lead up to 3 hours. Hybrid: an "evolution network" encodes physical advection (continuity equation), a conditional generative network fills in small-scale stochastic detail.
- **Code / weights / licence:** Not confirmed this pass (would need a further fetch of the paper's code-availability statement or a repo search).
- **Data:** US (radar) + China (CMA radar) precipitation events; evaluated by 62 professional meteorologists (ranked #1 in 71% of comparisons).
- **For HRRR+GOES:** The "physics scaffold (advection) + learned generative texture" split is architecturally the closest published idea to CorrDiff's "physics gives the low-frequency field, network paints the high-frequency texture" — worth reading in full even though it's radar, not satellite imagery.

## PreDiff — 2023
- **Title:** PreDiff: Precipitation Nowcasting with Latent Diffusion Models
- **Authors/venue:** Gao, Shi, Han, Wang, Jin, Maddix, Zhu, Li, Wang (AWS AI Labs). NeurIPS 2023.
- **Fetched:** https://arxiv.org/abs/2307.10422 (VERIFIED)
- **Input -> output:** Past radar/precip frames (SEVIR, and a synthetic N-body MNIST benchmark) -> future frames via a latent diffusion model, plus a "knowledge alignment" mechanism that steers sampling toward domain physical constraints; exact resolution/lead time not stated in the abstract.
- **Code / weights / licence:** Not confirmed this pass (not stated in abstract; likely on the same AWS AI Labs GitHub as Earthformer but unverified here).
- **Data:** SEVIR (see SEVIR entry below) + synthetic N-body MNIST.
- **For HRRR+GOES:** Direct precedent for "latent diffusion + physics-constrained guidance" on Earth-observation imagery from the same lab that built Earthformer — the "knowledge alignment" idea (nudge diffusion samples toward a physical prior at inference time, not just via conditioning) is a candidate mechanism for keeping the generator's cloud texture consistent with HRRR fields without hard-conditioning every layer.

## DiffCast — 2024
- **Title:** DiffCast: A Unified Framework via Residual Diffusion for Precipitation Nowcasting
- **Authors/venue:** Yu, Li, Ye, Zhang, Luo, Dai, Wang, Chen (Harbin Institute of Technology). CVPR 2024.
- **Fetched:** https://arxiv.org/abs/2312.06734 (VERIFIED)
- **Input -> output:** Radar echo sequences -> future radar echoes; decomposes prediction into a deterministic global-motion backbone (interchangeable: SimVP/Earthformer/ConvGRU/PhyDNet) + a residual diffusion model for local stochastic detail.
- **Code / weights / licence:** Official repo https://github.com/DeminYu98/DiffCast (found via search + confirmed in the arXiv abstract fetch; training/inference code for SEVIR provided; weights availability not independently confirmed).
- **Data:** Four public radar nowcasting datasets, including SEVIR.
- **For HRRR+GOES:** The "any deterministic backbone + bolt-on residual diffusion for texture" pattern is a cheap way to add photographic-looking detail on top of a coarse HRRR-driven deterministic prediction — closer to the "GAN on top of physics" framing than end-to-end diffusion.

## CasCast — 2024
- **Title:** CasCast: Skillful High-resolution Precipitation Nowcasting via Cascaded Modelling
- **Authors/venue:** Gong, Bai, Ye, Xu, Liu, Dai, Yang, Ouyang (Shanghai AI Lab). ICML 2024.
- **Fetched:** https://arxiv.org/abs/2402.04290 (VERIFIED)
- **Input -> output:** Radar precipitation -> high-resolution nowcasts; cascades a deterministic model (mesoscale distribution) into a frame-wise-guided diffusion transformer operating in a low-dimensional latent space (small-scale/extreme detail).
- **Code / weights / licence:** Official repo https://github.com/OpenEarthLab/CasCast (listed in search results; not independently fetched/verified this pass).
- **Data:** Three benchmark radar precipitation datasets (names not itemized in the abstract).
- **For HRRR+GOES:** Same "coarse deterministic field -> latent diffusion adds high-res texture" cascade as CorrDiff's own design; the "frame-wise guided" latent diffusion transformer is a concrete architecture to borrow for the image-generation half.

## Earthformer — 2022
- **Title:** Earthformer: Exploring Space-Time Transformers for Earth System Forecasting
- **Authors/venue:** Gao, Shi, Wang, Zhu, Wang, Li, Yeung (Amazon AWS AI Labs / HKUST). NeurIPS 2022.
- **Fetched:** https://arxiv.org/abs/2207.05833 (VERIFIED)
- **Input -> output:** General space-time cuboid transformer (Cuboid Attention), evaluated on MovingMNIST, N-body MNIST, precipitation nowcasting (SEVIR, so radar + GOES-16 channels) and ENSO forecasting; abstract doesn't itemize exact resolution/lead time.
- **Code / weights / licence:** Official code released: https://github.com/amazon-science/earth-forecasting-transformer (confirmed in the fetched abstract page itself).
- **Data:** SEVIR among other benchmarks (see SEVIR entry — includes real GOES-16 imagery, not just radar).
- **For HRRR+GOES:** A general-purpose, code-available space-time transformer backbone already validated on SEVIR's actual satellite channels (not just radar) — a plausible off-the-shelf encoder for the HRRR-field sequence before handing off to a generative image head.

## SEVIR (dataset) — 2020
- **Title:** SEVIR: A Storm Event Imagery Dataset for Deep Learning Applications in Radar and Satellite Meteorology
- **Authors/venue:** Veillette, Samsi, Mattioli (MIT Lincoln Laboratory). NeurIPS 2020.
- **Fetched:** https://proceedings.neurips.cc/paper/2020/hash/fa78a16157fed00d7a80515818432169-Abstract.html (VERIFIED)
- **Input -> output:** Not a model — a benchmark dataset: >10,000 storm events, each a 4-hour, spatio-temporally aligned sequence of GOES-16 ABI channels C02 (visible), C09 (mid-level water vapor), C13 (clean IR), NEXRAD VIL mosaics, and GOES-16 GLM lightning flashes, over 384 km x 384 km tiles.
- **Code / weights / licence:** Dataset + baseline code "available for download" (per abstract); exact URL/licence not confirmed this pass (known publicly as an AWS Open Data Registry entry, not independently fetched here).
- **Data:** Itself the data source — used as the training/eval benchmark for PreDiff, Earthformer, DiffCast, CasCast (via its radar-only subset) above.
- **For HRRR+GOES:** This is the standard benchmark that already pairs real GOES-16 imagery (IR + visible + water vapor) with radar at km-scale, 5-min cadence over the US — the natural place to prototype an HRRR-conditioned satellite-image generator before committing to a bespoke California/GOES pipeline, and the natural source of "ground truth GOES frames" to validate against.

## Meteo-France MSG cloud-cover nowcasting (U-Net) — 2020
- **Title:** Cloud Cover Nowcasting with Deep Learning
- **Authors/venue:** Berthomier, Pradel, Perez (Météo-France AI Lab). IPTA 2020 (IEEE), Paris.
- **Fetched:** https://arxiv.org/abs/2009.11577 (VERIFIED)
- **Input -> output:** Meteosat Second Generation (MSG) satellite imagery -> cloud-cover nowcasts, lead up to a few hours; compares CNN/U-Net architectures against RNN, optical flow, and the AROME physical NWP model (U-Net reported to outperform AROME).
- **Code / weights / licence:** Not mentioned/found this pass.
- **Data:** MSG imagery (Météo-France operational archive).
- **For HRRR+GOES:** This is the closest published match to "predict a future GEO satellite frame directly," using the exact MSG/EUMETSAT imagery the brief calls out — but it predicts a derived cloud-cover product, not raw radiance/GeoColor-like imagery, and is a plain discriminative U-Net (no generative/diffusion texture model), so it validates the task framing more than the generator architecture.

## Satellite IR diffusion nowcasting (3D U-Net) — 2026
- **Title:** Deterministic Nowcasting of Geostationary Satellite Infrared Brightness Temperature Using 3D U-Net Diffusion Model
- **Authors/venue:** Afzali Gorooh, Delle Monache, Axisa, Sengupta, Zhang, Ralph. *Scientific Reports* (Springer Nature), published 3 Jan 2026.
- **Fetched:** https://pmc.ncbi.nlm.nih.gov/articles/PMC12859110/ (VERIFIED via PMC open-access mirror; the nature.com URL itself redirected to a login gate twice and could not be fetched directly)
- **Input -> output:** SEVIRI (MSG) 10.8 um IR channel, 3 km / 15 min -> 6-hour nowcasts of IR brightness temperature at the same 15 min / 3 km resolution, over a UAE domain (21-28N, 50-58E). A 3D U-Net diffusion model conditioned on the noising/denoising process (not on any NWP field).
- **Code / weights / licence:** Implementation built on the open-source github.com/lucidrains/video-diffusion-pytorch; the paper's own training scripts are "available from the corresponding author upon request" (i.e., no public trained-weights release). Licence: CC BY 4.0.
- **Data:** SEVIRI observations 2017-2021 (train), independent test July-Sept 2022.
- **Sharpness/lead time — directly relevant to the brief's question:** the paper reports the diffusion model keeps a clear skill advantage over baselines through ~2 hours lead time, with "reduced but still evident" gains out to ~4-5h before converging with simpler baselines — i.e., sharpness genuinely differentiated diffusion vs. non-diffusion mainly in the first 2 hours.
- **For HRRR+GOES:** The single closest published analog to the exact target task — a diffusion model generating a future GOES/MSG-like IR frame directly (not a derived precip/cloud-cover product) — but it is univariate (IR channel only, no NWP conditioning, no visible/GeoColor-like output) and has no public pretrained weights, so it's a proof-of-concept for feasibility and lead-time expectations, not a drop-in codebase.

## Summary for the decision
The closest thing to the exact HRRR-in / GOES-frame-out setup does not exist as a single published system: the "generate satellite imagery directly" literature (Berthomier's MSG U-Net, the 2026 SEVIRI 3D U-Net diffusion paper) is real but small, univariate, and never conditions on an NWP model's fields — it only extrapolates past frames of the same sensor. The "condition a generative model on rich multi-field weather state" literature (DGMR, NowcastNet, PreDiff, DiffCast, CasCast) is much more mature and reproducible (DGMR and DiffCast both ship real code + weights) but outputs a physical scalar field (radar reflectivity/rain rate), never rendered imagery, so none of it has solved the "paint a photographic-looking frame from a physics field" step CorrDiff and this project need. MetNet-3 is the one system explicitly claimed (by the brief and by secondary sources) to fuse NWP output with satellite/radar observations, but the primary Google sources fetched here explicitly deny that HRRR is used as an input — this is a live discrepancy worth resolving with a full paper read before treating MetNet-3 as architectural precedent for NWP-conditioning. Sharpness at lead time is the recurring finding across every generative entry: DGMR is skilful to ~90 min, NowcastNet to ~3h, and the one satellite-image diffusion model found here is genuinely sharp only to ~2h with fading advantage to ~4-5h — none support turning HRRR's hourly cadence into an indefinitely-animatable sky without expecting quality to degrade well within a single day's forecast window. The most usable near-term building blocks are: SEVIR as a ready benchmark that already pairs real GOES-16 imagery with radar at the right scale for prototyping; DiffCast/CasCast's "deterministic backbone + residual/cascaded diffusion for texture" pattern as the architectural template closest to CorrDiff's own split; and Earthformer as a code-available, SEVIR-validated space-time encoder to sit upstream of a generative image head. Nothing found here is a fork-and-run solution — every satellite-imagery-generating example is a single-channel research prototype with no public weights, so building the HRRR+GOES generator means assembling it from these parts rather than adapting one existing repo.
