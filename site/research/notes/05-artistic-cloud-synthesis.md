# Aesthetic, artistic and graphics cloud synthesis
_Agent run 2026-09-14. 10 items, 9 verified, 1 unverified._

## Text2Light — 2022
- **Title:** Text2Light: Zero-Shot Text-Driven HDR Panorama Generation
- **Authors/venue:** Zhaoxi Chen, Guangcong Wang, Ziwei Liu. ACM Transactions on Graphics (SIGGRAPH Asia 2022).
- **Fetched:** https://arxiv.org/abs/2209.09898 (VERIFIED)
- **Input -> output:** Free-form text description -> 4K+ HDR equirectangular panorama, via a two-stage pipeline (CLIP-driven discrete codebook sampling for LDR panorama, then super-resolution + inverse tone mapping to HDR). No physical field input; purely text-conditioned.
- **Code / weights / licence:** Code released, github.com/FrozenBurning/Text2Light; arXiv non-exclusive distribution licence for the paper. Pretrained weights: not confirmed from the abstract page (README likely has them but was not fetched — budget).
- **Data:** Zero-shot; trained on a mix of panorama/HDR datasets to build the codebooks, no exact dataset named in the abstract.
- **For HRRR+GOES:** Architecture pattern (VQGAN codebook + super-res/inverse-tonemap) is a plausible skybox-texture backbone, but it's text-conditioned, not field-conditioned — would need a ControlNet-style adapter bolted on to accept an HRRR cloud-fraction/brightness-temp map instead of a text string. Not a direct fit as-is.

## SkyGAN — 2022/2023
- **Title:** SkyGAN: Realistic Cloud Imagery for Image-based Lighting (extended from an EGSR 2022 paper)
- **Authors/venue:** Martin Mirbauer, Tobias Rittig, Tomáš Iser, Jaroslav Křivánek, Elena Šikudová. Computer Graphics Forum, Nov 2023 (Charles University CGG group).
- **Fetched:** https://cgg.mff.cuni.cz/publications/skygan/ (VERIFIED)
- **Input -> output:** Sun position + user-chosen cloud-coverage ratio, on top of an analytical clear-sky radiance model -> full hemispherical HDR fisheye skydome image (usable directly as an environment map for IBL/rendering).
- **Code / weights / licence:** Code AND a 39,000-image HDR sky dataset both public at github.com/CGGMFF/SkyGAN (StyleGAN3-based generator). Licence not stated on the page fetched.
- **Data:** 39,000 paired HDR fisheye sky photographs matched to physically-accurate clear-sky radiance from the "Prague sky model."
- **For HRRR+GOES:** Closest existing analogue to the target pipeline in spirit: a coarse physical/analytical sky descriptor (sun angle, cloud fraction) drives a GAN that paints photoreal sky texture. The conditioning is scalar/low-dimensional (not a full 2D field like HRRR output), so it would need extending to accept a spatial map, but the released dataset + code + StyleGAN backbone make this the best candidate to actually inspect or fork.

## Nubis / Real-Time Volumetric Cloudscapes of Horizon Zero Dawn — 2015 (evolved through Horizon Forbidden West)
- **Title:** The Real-Time Volumetric Cloudscapes of Horizon Zero Dawn (Advances in Real-Time Rendering, SIGGRAPH 2015 course); later "Nubis: Authoring Realtime Volumetric Cloudscapes with the Decima Engine" (SIGGRAPH 2017) and "Nubis Evolved" (Horizon Forbidden West).
- **Authors/venue:** Andrew Schneider and Nathan Vos, Guerrilla Games. SIGGRAPH course notes / GDC talks, 2015-2022.
- **Fetched:** https://www.guerrilla-games.com/read/the-real-time-volumetric-cloudscapes-of-horizon-zero-dawn (VERIFIED)
- **Input -> output:** Artist-authored 2D coverage/type/height maps + a 3D noise field -> real-time-rendered volumetric cloudscape (all major cloud types, ~2ms GPU budget on PS4). This is procedural graphics, not machine learning — no training data. (The page fetched did not itself spell out the specific noise functions; the widely-published detail, from Schneider's own GDC/SIGGRAPH talks, is a combination of Perlin and Worley noise to build cloud shape and detail — flagged here as a known-industry-standard detail rather than something confirmed on this specific fetched page.)
- **Code / weights / licence:** No code released (proprietary Decima engine); slide decks/PDFs are public.
- **Data:** None (procedural, not data-driven).
- **For HRRR+GOES:** This is the "beauty, real-time, non-ML" end of the spectrum Derek is choosing between. It shows how a coarse artist/physical coverage map (analogous to an HRRR cloud-fraction field) can directly drive believable cloud texture and motion via noise, entirely without a trained generative model — relevant as the baseline the ML pipeline has to beat on "looks like real weather," and as a reminder that field-to-texture mapping doesn't strictly require diffusion/GAN machinery.

## Physically Based Sky, Atmosphere & Cloud Rendering in Frostbite — 2016
- **Title:** Physically Based Sky, Atmosphere and Cloud Rendering in Frostbite
- **Authors/venue:** Sébastien Hillaire, EA/Frostbite. SIGGRAPH 2016 course "Physically Based Shading in Theory and Practice."
- **Fetched:** https://www.frostbite.com/frostbite/news/physically-based-sky-atmosphere-and-cloud-rendering — 301-redirected to https://www.ea.com/news/physically-based-sky-atmosphere-and-cloud-rendering, which returned 404 (UNVERIFIED: could not load either the Frostbite blog post or the redirected EA URL; content below is from search-result snippets only, not a fetched primary source).
- **Input -> output:** Per search snippets: physically-based volumetric participating-media models for sky, atmosphere and clouds, combined and rendered in real time for Frostbite-engine games; not conditioned on any external data field, purely a rendering technique.
- **Code / weights / licence:** Slides/PPT reportedly available via the SIGGRAPH course page (blog.selfshadow.com/publications/s2016-shading-course/); not independently fetched.
- **Data:** None (procedural/physical rendering, no training data).
- **For HRRR+GOES:** Same category as Nubis — a physically-grounded, non-ML atmosphere/cloud renderer used as an industry reference point for what "physically plausible" looks like at real-time speed. Given the failed fetch, treat any specific technical claim here as unconfirmed; it's included only as a well-known named reference Derek may want to look at directly (blog.selfshadow.com link above).

## DiffusionSat — 2023/2024
- **Title:** DiffusionSat: A Generative Foundation Model for Satellite Imagery
- **Authors/venue:** Samar Khanna, Patrick Liu, Linqi Zhou, Chenlin Meng, Robin Rombach, Marshall Burke, David Lobell, Stefano Ermon. ICLR 2024 (arXiv Dec 2023; Stanford/Stability AI).
- **Fetched:** https://arxiv.org/abs/2312.03606 (VERIFIED)
- **Input -> output:** Satellite metadata (geolocation, timestamp) as conditioning, rather than text captions -> generated multi-spectral satellite imagery; also supports temporal generation, super-resolution from multi-spectral inputs, and in-painting as conditional tasks.
- **Code / weights / licence:** Project website referenced in the paper (URL not resolved from the abstract fetch); paper itself under CC BY 4.0. Weights/code release not confirmed from the abstract page alone.
- **Data:** Trained on a collection of publicly available large, high-resolution remote-sensing datasets (fMoW, others — not itemised in the abstract).
- **For HRRR+GOES:** Directly relevant as precedent for a Stable-Diffusion-style backbone that conditions on structured metadata/geolocation instead of text. It's conditioned on scalar/metadata tags, not a dense physical field like an HRRR cloud-fraction grid, so a ControlNet-style spatial adapter would still need to be added — but this is the closest "diffusion model of satellite imagery with non-text conditioning" precedent found.

## MetaEarth — 2024
- **Title:** MetaEarth: A Generative Foundation Model for Global-Scale Remote Sensing Image Generation
- **Authors/venue:** Zhiping Yu, Chenyang Liu, Liqin Liu, Zhenwei Shi, Zhengxia Zou. IEEE TPAMI; arXiv May 2024 (final Oct 2024).
- **Fetched:** https://arxiv.org/abs/2405.13570 (VERIFIED)
- **Input -> output:** Geographic location + target resolution -> unbounded, arbitrary-sized remote-sensing imagery, via a resolution-guided self-cascading diffusion framework and a new noise-sampling strategy for tiling/unbounded generation.
- **Code / weights / licence:** Paper CC BY 4.0; a project page is referenced (jiupinjia.github.io/metaearth/) but code/weight availability was not confirmed from the abstract fetch alone.
- **Data:** A purpose-built multi-resolution optical remote-sensing dataset with geographic metadata (specific source imagery not itemised in the abstract).
- **For HRRR+GOES:** Useful mainly for the "unbounded/tileable generation at arbitrary resolution" trick (relevant if California-coast frames need to be generated at native satellite resolution then tiled/cropped to the 768px web-mercator target) — but conditioning is on location+resolution, not a physical cloud field, so it answers a tiling/scale problem, not the physics-conditioning problem.

## GeoSynth — 2024
- **Title:** GeoSynth: Contextually-Aware High-Resolution Satellite Image Synthesis
- **Authors/venue:** Srikumar Sastry, Subash Khanal, Aayush Dhakal, Nathan Jacobs (WashU multimodal vision & remote sensing lab). CVPRW EarthVision 2024; arXiv April 2024.
- **Fetched:** https://arxiv.org/abs/2404.06637 (VERIFIED)
- **Input -> output:** Two conditioning channels combined: text prompt (style/region description) + OpenStreetMap layout raster (ControlNet-style spatial control) -> high-resolution satellite image matching that layout and style.
- **Code / weights / licence:** Code and model checkpoints released, github.com/mvrl/GeoSynth (confirmed via search: HF weights also published as MVRL/GeoSynth-OSM). Paper CC BY 4.0.
- **Data:** Large paired dataset of satellite imagery with auto-generated captions plus matched OpenStreetMap tiles.
- **For HRRR+GOES:** **This is the single closest architectural precedent found for the exact ask** — a ControlNet literally bolted onto a satellite-image diffusion model, conditioned on a rasterized map layer rather than text alone. It's the strongest concrete starting point to fork/fine-tune: swap the OSM raster channel for an HRRR cloud-fraction/cloud-top-height/brightness-temperature raster and swap the target domain from optical land-cover satellite tiles to GOES cloud-texture tiles. Released weights make this genuinely reproducible, not just a paper to reimplement from scratch.

## AI-CD (Artificial Intelligence Based Cloud Distributor) — 2019
- **Title:** Artificial Intelligence Based Cloud Distributor (AI-CD): Probing Low Cloud Distribution with a Conditional Generative Adversarial Network
- **Authors/venue:** Tianle Yuan. arXiv (physics.ao-ph), May 2019.
- **Fetched:** https://arxiv.org/abs/1905.08700 (VERIFIED)
- **Input -> output:** Large-scale environmental variables (sea surface temperature, estimated inversion strength, surface wind speed, relative humidity, large-scale subsidence rate) -> 2D marine low-cloud (stratocumulus) reflectance field, stochastic (can generate an ensemble of plausible cloud fields from the same fixed conditions).
- **Code / weights / licence:** Not stated in the abstract; not found.
- **Data:** MODIS-observed cloud reflectance fields, paired with reanalysis-derived environmental variables.
- **For HRRR+GOES:** This is the paper in this list closest in spirit to "physics fields in, cloud texture out" for stratocumulus specifically, and it predates the diffusion-era work by several years — worth reading as a precedent for which physical variables (SST, inversion strength, subsidence) are known to actually control stratocumulus morphology, which could inform which HRRR fields to condition on beyond the obvious cloud-fraction/cloud-top-height ones. It targets scientific plausibility over visual fidelity, and no code was found, so it's a conceptual reference, not a fork target.

## GAN cloud-image augmentation (IGARSS) — 2021
- **Title:** Using GANs to Augment Data for Cloud Image Segmentation Task
- **Authors/venue:** Mayank Jain, Conor Meegan, Soumyabrata Dev. IEEE IGARSS 2021; arXiv 2106.03064.
- **Fetched:** https://arxiv.org/abs/2106.03064 (VERIFIED)
- **Input -> output:** Unconditional/latent-noise GAN -> synthetic ground-based sky/cloud images plus matching estimated binary segmentation masks, used to augment small training sets for cloud segmentation.
- **Code / weights / licence:** Not mentioned on the abstract page.
- **Data:** Ground-based sky-camera cloud images (ground-based, not satellite) with limited ground-truth segmentation masks.
- **For HRRR+GOES:** Low direct relevance — ground-based whole-sky-camera imagery at a single point, unconditional generation, aimed at segmentation-training augmentation rather than photoreal synthesis. Included mainly to confirm the "GAN generates plausible cloud texture" idea has been done at small scale, but this is not a field-conditioned or satellite-scale precedent.

## NASA SVS Earth Time-lapse (DSCOVR EPIC) — 2016
- **Title:** Earth Time-lapse (62 days in 60 seconds)
- **Authors/venue:** NASA Goddard Space Flight Center, Scientific Visualization Studio.
- **Fetched:** https://svs.gsfc.nasa.gov/12118/ (VERIFIED)
- **Input -> output:** Real DSCOVR EPIC camera imagery (sunlit-Earth full-disc captures roughly every 2 hours, visible/UV/near-IR) covering Nov 4 2015-Jan 4 2016, compiled into a public-outreach time-lapse animation — no synthesis or generative model involved, purely real satellite frames sequenced as a video.
- **Code / weights / licence:** N/A — not a model, a compiled animation; NASA imagery is generally public domain.
- **Data:** DSCOVR EPIC real-time full-disc Earth imagery.
- **For HRRR+GOES:** Not a generative model at all, but directly on-point as the "artistic use of real satellite cloud data" precedent Derek's project sits next to: it demonstrates that real cloud/weather satellite data, simply sequenced and color-graded, already reads as compelling motion art without any synthesis — useful as a fallback/comparison baseline ("how much does the generative layer actually add over just animating real GOES frames directly") and as evidence NASA/SVS-style projects are an easily verifiable, safe reference class to cite publicly.

## Summary for the decision
What exists splits cleanly into two camps that never meet: pure graphics/procedural cloud rendering (Nubis, Frostbite) that is physically-inspired but untrained and can't be "conditioned on HRRR" without hand-built mapping logic, and ML generative models of satellite/panorama imagery (Text2Light, DiffusionSat, MetaEarth, GeoSynth) that condition on text, metadata, or a rasterized map layer — but none of the ML papers found condition specifically on a dense physical weather field (cloud fraction, cloud-top height, brightness temperature) the way the target HRRR+GOES pipeline needs. GeoSynth is the closest reproducible template: a released, weight-available ControlNet bolted onto a satellite-image diffusion model that already accepts a rasterized conditioning layer (OpenStreetMap) in place of text — the fix needed for the target project is swapping that raster channel for an HRRR field and the training domain from land-cover tiles to GOES cloud frames, which is a fine-tuning/adapter-retraining job, not a from-scratch architecture. SkyGAN is the next-closest thing (a released, dataset-and-code sky generator conditioned on physical-ish scalars — sun position, cloud coverage) but its conditioning is far coarser than a full 2D field and its domain is ground-based fisheye, not satellite nadir view. Weights are confirmed released and usable for SkyGAN and GeoSynth; DiffusionSat, MetaEarth and Text2Light have code/project pages referenced but weight availability was not confirmed within this pass's fetch budget and should be checked directly (their GitHub READMEs) before committing to a fork. The biggest gap: nothing found trains directly on paired (physical NWP cloud field -> matching real satellite cloud image) data at the km-scale the brief wants — the CorrDiff pattern Derek names as the target has no published cloud-specific analogue in this sweep; AI-CD (2019) is the only item conditioning on physically meaningful atmospheric variables for cloud generation, but it's an older, lower-fidelity conditional GAN on MODIS reflectance fields, not a diffusion model, and no code was found for it.
