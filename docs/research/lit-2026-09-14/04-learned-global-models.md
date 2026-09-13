# Learned weather models as context only

_Agent run 2026-09-14. 12 items, 10 verified, 2 unverified._

## FourCastNet — 2022
- **Title:** FourCastNet: A Global Data-driven High-resolution Weather Model using Adaptive Fourier Neural Operators
- **Authors/venue:** Pathak, Subramanian, Harrington, et al. (NVIDIA/Berkeley/Rice). arXiv, physics.ao-ph, Feb 2022.
- **Fetched:** https://arxiv.org/abs/2202.11214 (VERIFIED)
- **Input -> output:** ERA5-trained; 0.25° (~25-30 km) global grid -> surface wind speed, precipitation, atmospheric water vapor, standard upper-air fields; week-long forecast in <2 sec.
- **Code / weights / licence:** NVIDIA's github (NVlabs/FourCastNet / part of Earth-2 / modulus); abstract page doesn't state licence — not independently confirmed this run.
- **Data:** ERA5 reanalysis.
- **For HRRR+GOES:** Global, coarse (~25 km) — no cloud fields in the base variable set, wrong scale for a 2 km California-coast frame either way. Pure background.

## Pangu-Weather — 2022
- **Title:** Pangu-Weather: A 3D High-Resolution Model for Fast and Accurate Global Weather Forecast
- **Authors/venue:** Bi, Xie, Zhang, Chen, Gu, Tian (Huawei). arXiv physics.ao-ph, Nov 2022.
- **Fetched:** https://arxiv.org/abs/2211.02556 ; https://github.com/198808xc/Pangu-Weather (both VERIFIED)
- **Input -> output:** ERA5 13-pressure-level Z/Q/T/U/V + surface MSLP/U10/V10/T2M, 0.25° grid -> same fields at 1h/3h/6h/24h lead times (four separate ONNX checkpoints).
- **Code / weights / licence:** Weights on Google Drive/Baidu, **CC BY-NC-SA 4.0 — commercial use explicitly forbidden**, research only. No cloud variables in the input/output list.
- **Data:** ERA5.
- **For HRRR+GOES:** Same ERA5 variable set as GraphCast — no cloud fields, and the licence blocks any commercial product use even as inspiration for output format. Context only.

## GraphCast — 2022
- **Title:** GraphCast: Learning skillful medium-range global weather forecasting
- **Authors/venue:** Lam, Sanchez-Gonzalez, Willson, et al. (Google DeepMind). arXiv cs.LG, Dec 2022 (Science 2023).
- **Fetched:** https://arxiv.org/abs/2212.12794 ; https://github.com/google-deepmind/graphcast (both VERIFIED)
- **Input -> output:** ERA5, 0.25° global grid -> "hundreds of weather variables" over 10 days, 6h steps, <1 min to run.
- **Code / weights / licence:** Code Apache 2.0; **pretrained weights CC BY 4.0**, hosted on a public Google Cloud bucket (`gs://dm_graphcast`) — the most permissively licensed of the group. Variable list is the standard ERA5 dynamical set (Z/T/U/V/Q, MSLP, 2m temp, 10m wind, precip) — no total cloud cover or cloud water among the outputs.
- **Data:** ERA5 reanalysis.
- **For HRRR+GOES:** Freely reusable weights, but 0.25° (~28 km) global and no cloud output — useless as a direct conditioning source, only as an architecture reference (graph-based decoder).

## GenCast — 2023/2024
- **Title:** GenCast: Diffusion-based ensemble forecasting for medium-range weather
- **Authors/venue:** Price, Sanchez-Gonzalez, Alet, et al. (Google DeepMind). arXiv cs.LG, Dec 2023, rev. May 2024 (Nature 2024/2025).
- **Fetched:** https://arxiv.org/abs/2312.15796 (VERIFIED)
- **Input -> output:** ERA5, 0.25° grid -> 80+ surface/atmospheric variables, 15-day forecasts at 12h steps, diffusion-based ensemble (8 min/forecast).
- **Code / weights / licence:** CC-BY 4.0 noted on the arXiv page; code/weights release not confirmed from the abstract page alone.
- **Data:** ERA5.
- **For HRRR+GOES:** Diffusion-ensemble architecture is directly relevant methodologically (same family as the CorrDiff pattern this project is evaluating) but it's a global 0.25° deterministic-variable model with no cloud fields — a technique reference, not a data source.

## Aurora — 2024
- **Title:** A Foundation Model for the Earth System (Aurora)
- **Authors/venue:** Bodnar, Bruinsma, Lucic, et al. (Microsoft). arXiv physics.ao-ph, May 2024, rev. Nov 2024.
- **Fetched:** https://arxiv.org/abs/2405.13063 ; https://github.com/microsoft/aurora (both VERIFIED)
- **Input -> output:** Foundation model with fine-tuned "heads" — medium-res (0.25°) and high-res (0.1°, ~11 km) weather prediction, plus separate air-pollution and ocean-wave heads. Base variables shown in the repo example are the standard ERA5 set (2t, 10u/10v, msl, z/u/v/t/q) — no cloud variable confirmed in what was fetched.
- **Code / weights / licence:** Code has a repo LICENSE file (not read in full this run); repo explicitly disclaims non-academic use ("has not been developed nor tested for non-academic purposes... at your own risk"). Weights download automatically (~500 MB for the small checkpoint) but hosting location wasn't confirmed (commonly HuggingFace).
- **Data:** ERA5 + multiple operational/reanalysis sources depending on head.
- **For HRRR+GOES:** The 0.1° (~11 km) high-res head is the finest native resolution of the global models surveyed here, still ~5x coarser than the ~2 km target frame, and no cloud output confirmed. Licence caution flagged — verify before any product use.

## ECMWF AIFS + AIFS-CRPS — 2024
- **Title:** AIFS — ECMWF's data-driven forecasting system; AIFS-CRPS: Ensemble forecasting using a model trained with a CRPS-based loss
- **Authors/venue:** Lang, Alexe, Chantry, et al. (ECMWF). arXiv physics.ao-ph, Jun 2024 (rev. Aug 2024) / Dec 2024 (npj Artificial Intelligence 2026).
- **Fetched:** https://arxiv.org/abs/2406.01465 ; https://arxiv.org/abs/2412.15832 ; https://arxiv.org/abs/2509.18994 (all VERIFIED, but none of the three abstract pages stated a precise resolution or full variable list)
- **Input -> output:** GNN encoder/decoder + sliding-window transformer, trained on ERA5 + ECMWF operational analyses -> "upper-air variables, surface weather parameters, tropical cyclone tracks"; AIFS-CRPS extends this to a stochastic ensemble (CRPS/"almost fair CRPS" loss) that beats physics-based IFS ensemble on many variables/lead times. A Sept 2025 update ("An update to ECMWF's ML forecast model AIFS") adds an "expanded set of variables" — the abstract text fetched did not name them, so whether cloud fields were added is **UNVERIFIED**.
- **Code / weights / licence:** Papers are CC-BY-SA 4.0; ECMWF publishes AIFS forecasts under its open-data policy, operational since Feb 2025. Code/weight release mechanics not confirmed from these three abstract pages (ECMWF ships AIFS via the Anemoi framework on GitHub, not independently checked this run).
- **Data:** ERA5 + ECMWF operational analyses.
- **For HRRR+GOES:** Global ~0.25°-class model (like the above); the CRPS-ensemble training approach is a useful methodological analog for any generative/ensemble cloud output, but resolution and cloud-variable presence are unconfirmed here — treat as background, not a candidate feed.

## AIFS-LAM / stretched-grid regional effort — 2025
- **Title:** A comparison of stretched-grid and limited-area modelling for data-driven regional weather forecasting
- **Authors/venue:** ECMWF / MET Norway collaboration, built on the Anemoi/AIFS codebase. arXiv, Jul 2025.
- **Fetched:** https://arxiv.org/abs/2507.18378 (VERIFIED, but abstract page gave no resolution/variable specifics)
- **Input -> output:** Compares a Limited-Area Model (needs external boundary forcing) vs a Stretched-Grid Model (self-contained, embeds a high-res regional patch inside the coarse global grid) for a **European** domain; ECMWF's own blog describes this program as targeting higher resolution over the Nordics. Exact native km-spacing not stated in what was fetched — **UNVERIFIED**.
- **Code / weights / licence:** Built on the open-source Anemoi framework (PyTorch, ECMWF); specific licence/weights for this experiment not confirmed.
- **Data:** ERA5 + regional reanalysis/boundary data (Europe/Nordics).
- **For HRRR+GOES:** This is the closest thing to "AIFS-LAM" the topic asked about, but it's Europe-only, resolution unconfirmed, and not demonstrated at km-scale over any US coast — no path to a California conditioning source.

## NeuralGCM — 2024
- **Title:** Neural General Circulation Models for Weather and Climate
- **Authors/venue:** Kochkov, Yuval, Langmore, et al. (Google Research). Nature, 2024.
- **Fetched:** https://arxiv.org/abs/2311.07222 ; https://github.com/google-research/neuralgcm ; WebSearch confirming variable list via arXiv 2412.11973 and NeuralGCM docs (VERIFIED)
- **Input -> output:** Hybrid — a differentiable dynamical-core GCM with learned physics parameterizations, not a pure emulator. Runs on ERA5's 37 pressure levels; prognostic variables include u/v wind, geopotential, temperature, **specific humidity, specific cloud ice water content, and specific cloud liquid water content** — i.e. it DOES carry cloud water as a native prognostic field (not cloud-fraction/cover directly; that would need diagnosing from the water content fields). Climate-mode runs used at ~140 km resolution in the paper.
- **Code / weights / licence:** Code Apache 2.0; trained weights CC BY-SA 4.0 (per repo).
- **Data:** ERA5 reanalysis; a follow-on paper (2412.11973) further optimizes against IMERG satellite precipitation.
- **For HRRR+GOES:** The one global model in this list that outputs actual cloud water content, openly licensed — but at ~140 km grid spacing, roughly 70x coarser than the 2 km target. Notable as the only "yes" on cloud fields; still nowhere near usable resolution.

## NVIDIA StormCast — 2024
- **Title:** Kilometer-Scale Convection Allowing Model Emulation using Generative Diffusion Modeling
- **Authors/venue:** Pathak, Cohen, Garg, et al. (NVIDIA/U. Washington). arXiv physics.ao-ph, Aug 2024.
- **Fetched:** https://arxiv.org/abs/2408.10958 (VERIFIED); code/weights path via WebSearch of https://github.com/NVIDIA/earth2studio (VERIFIED as an available model in that repo, not independently confirmed weights are downloadable without an NVIDIA account/licence gate)
- **Input -> output:** 26 synoptic-scale variables condition a diffusion model that emulates HRRR at native **3 km** grid spacing, autoregressive 1h steps, demonstrated to 6h; outputs 99 state variables including **composite radar reflectivity**, moist updrafts, cold-pool structure. Abstract text didn't confirm a total-cloud-cover or cloud-water field by name, though the underlying HRRR state includes cloud hydrometeors.
- **Code / weights / licence:** Model is exposed as one of NVIDIA's "Earth-2 Open Models" through the Apache-2.0-licensed `earth2studio` framework; precise weight licence/gating not verified this run.
- **Data:** NOAA HRRR analyses/forecasts (CONUS).
- **For HRRR+GOES:** The single closest architectural precedent to this project's plan — HRRR-conditioned, generative, km-scale, CONUS — but it targets radar/dynamics, not cloud imagery, and the exact domain and full variable/cloud-field list need the full paper/repo, not just the abstract. Worth a deeper look if StormCast's code is actually runnable, but it is not a source of cloud fields per se — it's the closest **methodological** twin of the proposed HRRR+GOES pipeline.

## Aardvark Weather — 2024
- **Title:** Aardvark weather: end-to-end data-driven weather forecasting
- **Authors/venue:** Vaughan, Markou, Tebbutt, et al. (Cambridge/Alan Turing Institute). arXiv, Mar 2024.
- **Fetched:** https://arxiv.org/abs/2404.00411 (VERIFIED, but abstract gave no resolution or variable-list specifics)
- **Input -> output:** Learns forecasting **directly from raw observations** (no NWP analysis step) -> global gridded forecasts and local station forecasts, out to 10 days. Native resolution and full field list — **UNVERIFIED** from the abstract alone.
- **Code / weights / licence:** CC BY 4.0 on the paper; code/weights availability not confirmed this run.
- **Data:** Raw observation streams (satellite, surface stations, etc.), not ERA5.
- **For HRRR+GOES:** Interesting as a concept (skip the analysis/assimilation step entirely) but not a resolution or cloud-field candidate as documented here — pure background.

## HRRRCast — 2025/2026
- **Title:** HRRRCast: a data-driven emulator for regional weather forecasting at convection-allowing scales
- **Authors/venue:** NOAA-affiliated authors (per search results). arXiv Jul 2025; Artificial Intelligence for the Earth Systems, Vol 5(2), 2026.
- **Fetched:** https://arxiv.org/abs/2507.05658 (VERIFIED)
- **Input -> output:** Two architectures (ResNet-based ResHRRR, GNN-based GraphHRRR) emulate HRRR at its native **3 km CONUS** grid; trained for 1h/3h/6h lead times with greedy rollout for longer horizons; evaluated on composite reflectivity skill (beats HRRR at 20 dBZ). No cloud-cover/cloud-water output mentioned — evaluation is reflectivity-only.
- **Code / weights / licence:** Not stated on the abstract page — **UNVERIFIED**.
- **Data:** NOAA HRRR analyses (CONUS).
- **For HRRR+GOES:** Exactly the "HRRR-trained learned regional model" the topic asked about to confirm existence of — it exists, is 3 km/CONUS like HRRR itself, but is reflectivity/dynamics-focused with no confirmed cloud fields and no confirmed public code/weights. Same category as StormCast: relevant precedent, not a usable conditioning source.

## Silurian "GFT" (Brightband adjacent) — 2024-2026
- **Title:** No paper found; product blog posts only ("Clear skies ahead: GFT upgrades", "GFT-C: AI-Powered Tropical Cyclone Forecasts", "Introducing the Earth API"), Silurian AI (YC S24, founded by ex-Microsoft AI researchers).
- **Authors/venue:** Silurian AI (company blog), not peer-reviewed. Brightband is a separate, adjacent company (also AI weather forecasting) with no confirmed relation to GFT found this run.
- **Fetched:** https://silurian.ai/blog/clear-skies-ahead (VERIFIED for what it does/doesn't say); resolution figure (~11 km) comes only from a secondary GeekWire article surfaced in search, **not independently fetched — UNVERIFIED**.
- **Input -> output:** 1.5B-parameter global transformer ("Generative Forecasting Transformer"); the fetched blog post confirms a 2026 update added **Cloud Cover, Probability of Precipitation, and Humidity** as new output variables, and says the model "handles complex cloud physics" — but doesn't state whether cloud output is a scalar fraction, cloud water, or imagery-like field, nor confirm native resolution.
- **Code / weights / licence:** Fully proprietary, API-only ("Earth API") — no open weights, no code, no licence for reuse.
- **Data:** Not disclosed in what was fetched.
- **For HRRR+GOES:** The only model surveyed here that markets cloud cover as a named output, but it's closed, API-gated, and (per unverified secondary reporting) ~11 km — irrelevant as a data source for a project needing free, offline, ~2 km conditioning fields.

## Summary for the decision

None of the global learned emulators (GraphCast, Pangu-Weather, FourCastNet, GenCast, Aurora, AIFS/AIFS-CRPS) output cloud fields as part of their standard ERA5-derived variable sets, and all run at 0.1°-0.25° (~11-30 km) — one to two orders of magnitude coarser than the ~1.9 km/px California frame this project needs, so none can serve as a conditioning source at the target resolution. NeuralGCM is the one clear exception on variables (it carries specific cloud ice/liquid water content as a native prognostic field, openly licensed, Apache 2.0 code + CC BY-SA weights) but its published resolution (~140 km) is even coarser, ruling it out on scale. The km-scale, HRRR-conditioned models — NVIDIA StormCast and HRRRCast — are the closest architectural and data precedent to the HRRR+GOES pipeline being planned (3 km CONUS, generative/diffusion in StormCast's case), but both are documented here as reflectivity/dynamics-focused with no confirmed cloud-cover or cloud-water output, and neither has clearly confirmed open weights (StormCast is nominally an "Earth-2 Open Model" via NVIDIA's Apache-2.0 `earth2studio`, unverified beyond that; HRRRCast's code/weights status is unconfirmed). The regional AIFS-LAM/stretched-grid work is Europe/Nordics-only and undocumented on resolution in what was fetched. Silurian's GFT is the only product that names "cloud cover" as an output at all, but it is fully proprietary and API-gated. **Bottom line for the decision: confirmed — none of these models can serve as a free, off-the-shelf conditioning source for a ~2 km/px California-coast cloud frame.** The project's own plan to condition directly on HRRR's native cloud fields (rather than a learned emulator's output) remains the only viable path at that resolution; StormCast/HRRRCast are worth a second look only as architecture references for a future km-scale generative step, not as data sources.
