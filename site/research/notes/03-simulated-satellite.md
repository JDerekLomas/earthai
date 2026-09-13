# Simulated satellite imagery from NWP models — physics-based and learned
_Agent run 2026-09-14. 10 items, 5 verified, 5 unverified._

## Bikos et al. — 2012
- **Title:** Synthetic Satellite Imagery for Real-Time High-Resolution Model Evaluation
- **Authors/venue:** Bikos, D., et al.; *Weather and Forecasting* 27(6), AMS (CIRA/CIMSS-associated work)
- **Fetched:** https://rammb2.cira.colostate.edu/wp-content/uploads/2024/08/Bikos_waf-d-11-00130.1.pdf (UNVERIFIED: 403 Forbidden); retried at https://www.researchgate.net/publication/236028862_Synthetic_Satellite_Imagery_for_Real-Time_High-Resolution_Model_Evaluation (UNVERIFIED: 403 Forbidden). Two independent fetch attempts both blocked; content below is from WebSearch result snippets only, not a verified read of the paper.
- **Input -> output:** NSSL 4-km WRF-ARW forecast fields -> synthetic GOES infrared/water-vapor channel imagery (e.g. 6.95 µm) via a radiative transfer model, run in real time alongside forecasts for forecaster evaluation.
- **Code / weights / licence:** None found (pre-deep-learning era; this is a CRTM-style forward-model pipeline, not a trained network).
- **Data:** NSSL 4-km WRF-ARW output; compared against contemporaneous GOES imagery.
- **For HRRR+GOES:** This is the foundational "synthetic satellite for model evaluation" paper the modern CIMSS/CIRA/HRRR products descend from — establishes the CRTM-forward-model pattern (physics fields -> radiance -> brightness temperature) rather than a learned mapping. Could not confirm quantitative bias numbers; treat any figure from secondary sources as unverified until the PDF is accessible another way (e.g. via an institutional proxy).

## Otkin / DTC — HRRR satellite-IR brightness-temperature evaluation (2015-2016 study)
- **Title:** "Evaluating the Accuracy of the High Resolution Rapid Refresh (HRRR) Model Using Satellite Infrared Brightness Temperatures"
- **Authors/venue:** Jason Otkin (CIMSS/SSEC, University of Wisconsin-Madison); presented via the Developmental Testbed Center (DTC)
- **Fetched:** https://dtcenter.org/node/425 (VERIFIED)
- **Input -> output:** HRRR forecast fields -> synthetic GOES 10.7 µm infrared brightness temperature via CRTM, for two one-month evaluation periods (August 2015, January 2016), compared against real GOES observations using RMSE/bias, the Fractions Skill Score (neighborhood-based), and MODE object-based verification.
- **Code / weights / licence:** Not applicable — a physics forward-model + verification study, no ML model or repo.
- **Data:** HRRR model output vs. real GOES IR imagery, Aug 2015 and Jan 2016.
- **For HRRR+GOES:** Direct answer to "how good is HRRR's SBT against real GOES": the page reports the error is systematic and time-dependent rather than a single clean number — a **warm bias in forecast hour 1** (HRRR initialized with too few upper-level clouds), flipping to a **cold bias** a few hours later as the model **over-generates upper-level cloud**, with MODE showing too many small cloud objects early and too few objects by forecast hour 2. No single scalar RMSE/bias value was retrievable from the fetched content — this is the closest verified statement in this batch to a published HRRR-vs-GOES agreement number, and it says the disagreement is dominated by a fast bias-flip in the first couple of forecast hours, which matters if the CorrDiff-style generator is meant to look right at short lead time.

## NCEP/NCO — HRRR product documentation (SBT113/114/123/124 fields)
- **Title:** NCEP Data Products — HRRR (NCEP Central Operations)
- **Authors/venue:** NOAA/NWS/NCEP Central Operations
- **Fetched:** https://www.nco.ncep.noaa.gov/pmb/products/hrrr/ (VERIFIED for grid/cadence; the SBT field-level definitions below are from a WebSearch snippet of a third-party university course PDF, not independently fetched, so treat those definitions as UNVERIFIED)
- **Input -> output:** HRRR native/pressure-level fields -> GRIB2 output on a Lambert Conformal 3-km CONUS grid, standard cycles to forecast hour 18, extended cycles (00/06/12/18 UTC) to hour 48; hourly model runs.
- **Code / weights / licence:** N/A (operational NWP output, public domain US government data).
- **Data:** N/A.
- **For HRRR+GOES:** Confirms grid/cadence (3 km, hourly, CONUS) matching the target spec exactly. Per the (unverified) secondary source, the SBT fields are: SBT113/SBT114 = simulated GOES-11 channels 3/4, SBT123/SBT124 = simulated GOES-12 channels 3/4, all "at top of atmosphere" in Kelvin — i.e. the field-naming convention is legacy (GOES-11/12, retired satellites) carried forward as a variable-name convention rather than literally GOES-11/12; the CRTM computation itself is what NOAA/NCEP and CIMSS papers (Otkin, above) describe. Worth re-verifying field-by-field against the official HRRR GRIB2 table (rapidrefresh.noaa.gov) before hard-coding channel assumptions.

## CIRA/CIMSS — RAMMB Synthetic (Satellite) Imagery product
- **Title:** RAMMB: Synthetic Imagery (GOES-R Proving Ground synthetic satellite imagery product page)
- **Authors/venue:** CIRA (Cooperative Institute for Research in the Atmosphere, Colorado State University) with NOAA/NESDIS; CIMSS (UW-Madison) runs a parallel product line
- **Fetched:** https://rammb.cira.colostate.edu/research/goes-r_studies/synthetic.asp (VERIFIED)
- **Input -> output:** RAMS (Regional Atmospheric Modeling System) cloud-resolving model fields, nested to 2 km / 400 m, initialized from ETA analysis -> synthetic GOES-R ABI brightness temperature via separate clear-sky/cloudy-sky radiative transfer models (cloud optics via modified anomalous diffraction theory), for bands including 10.35 µm and 3.9 µm; also derives CAPE, CIN, precipitable water as companion fields.
- **Code / weights / licence:** No code/data download found on the page itself; case-study imagery only.
- **Data:** RAMS model runs; validated qualitatively against real GOES imagery in case studies (e.g. lake-effect snow).
- **For HRRR+GOES:** This is the operational CIMSS/CIRA "synthetic satellite" product family the topic asked about — same CRTM-style forward-model pattern as the HRRR SBT fields but running a different host model (RAMS, not HRRR/WRF). Validation shown is case-study/qualitative (one image comparison), not a corpus-wide bias statistic — reinforces that a clean, single published HRRR-vs-GOES agreement number is hard to find; the field seems to report bias qualitatively (warm/cold, cloud-top height errors) more often than as one RMSE headline.

## DWD — MFASIS / LMSynSat synthetic satellite imagery (Lokal-Modell / ICON-D2)
- **Title:** "Synthetic satellite imagery in the Lokal-Modell" (LMSynSat), successor tool MFASIS (Method for Fast Satellite Image Synthesis) used with ICON-D2
- **Authors/venue:** Deutscher Wetterdienst (DWD); *Meteorological Applications* / journal article indexed via ScienceDirect
- **Fetched:** https://www.sciencedirect.com/science/article/abs/pii/S0169809506000238 (UNVERIFIED: 403 Forbidden, single attempt — content below from WebSearch snippets only)
- **Input -> output:** DWD's regional NWP (formerly Lokal-Modell, now ICON-D2) model state -> synthetic satellite imagery via an operational forward radiative-transfer diagnostic, used for forecast-quality verification against observed imagery.
- **Code / weights / licence:** Not found; DWD-internal operational tool, not obviously open-sourced.
- **Data:** ICON-D2 / ICON-D2-EPS model output vs. real satellite imagery, per a follow-on verification study covering July 2022-June 2023.
- **For HRRR+GOES:** A second, independent physics-based CRTM-style implementation (Europe/DWD rather than US/NOAA) confirming this is a standard, multi-institution pattern — not something built once for HRRR. Useful as a citation that the physics approach generalizes, but this entry needs a real fetch (paywalled ScienceDirect abstract) before quoting any DWD-specific accuracy numbers.

## Harris et al. — 2023
- **Title:** Multivariate Emulation of Kilometer-Scale Numerical Weather Predictions with Generative Adversarial Networks: A Proof of Concept
- **Authors/venue:** L. Harris et al.; *Artificial Intelligence for the Earth Systems* (AMS), Vol. 2, Issue 4
- **Fetched:** https://journals.ametsoc.org/view/journals/aies/2/4/AIES-D-23-0006.1.xml (UNVERIFIED: 403 Forbidden, single attempt — content below from WebSearch snippet only)
- **Input -> output:** Per the snippet: a GAN trained to emulate the joint (multivariate) distribution of km-scale NWP model output fields, evaluated on distribution-recovery and spectral (power-spectrum) fidelity rather than on satellite-image realism specifically — i.e. this looks like NWP-field emulation, not confirmed to target brightness temperature or satellite bands.
- **Code / weights / licence:** Unknown — not found in the snippet, not verified by a direct fetch.
- **Data:** Unknown/unverified (km-scale NWP training data, model unspecified in the snippet).
- **For HRRR+GOES:** Closest thing in this batch to "the CorrDiff pattern, but as a GAN, applied to raw km-scale model fields" — i.e. the general idea of a generative network conditioned on coarse/physics fields to produce fine-scale, realistic-looking output. It is NOT confirmed to touch satellite imagery at all, so treat it as evidence the *pattern* (physics fields in, generative texture out) has precedent in the AMS/AIES literature, not as a satellite-imagery-specific result. Needs a real fetch to confirm scope.

## Cheng — 2021/2022
- **Title:** Creating synthetic night-time visible-light meteorological satellite images using the GAN method
- **Authors/venue:** Wencong Cheng (Beijing Aviation Meteorological Institute); arXiv preprint (submitted Jul 2021, revised May 2022)
- **Fetched:** https://arxiv.org/abs/2108.04330 (VERIFIED)
- **Input -> output:** Satellite infrared channel imagery + NWP products (ECMWF) -> synthetic night-time visible-light satellite imagery, using a GAN with an SEBlock channel-attention mechanism to weight the relative importance of input channels/fields. Resolution/domain not stated in the abstract-level fetch.
- **Code / weights / licence:** Not found — no GitHub link or weights-availability statement surfaced.
- **Data:** ECMWF NWP products + FY-4A (Chinese geostationary satellite) visible and infrared channel imagery.
- **For HRRR+GOES:** The single closest verified example of "NWP fields + physics fields go in, a GAN paints a realistic-looking satellite image out" — structurally the same problem shape as the proposed HRRR+GOES generator, just solving the specific night-time-visible-band gap (there is no real visible reflectance at night) rather than general cloud texture. No code released, so it is not directly reusable, but the architecture description (conditional GAN + channel-attention over multiple input fields) is a concrete, citable precedent for the input-fusion step of a CorrDiff-style HRRR generator.

## Chase et al. — 2025
- **Title:** How to use score-based diffusion in earth system science: A satellite nowcasting example (posted to arXiv also as "Score-based diffusion nowcasting of GOES imagery")
- **Authors/venue:** Randy J. Chase, Katherine Haynes, Lander Ver Hoef, Imme Ebert-Uphoff; arXiv:2505.10432 (submitted May 2025, revised Dec 2025)
- **Fetched:** https://arxiv.org/abs/2505.10432 (VERIFIED)
- **Input -> output:** Past 20 minutes of geostationary infrared satellite imagery -> 0-3 hour nowcast of future IR imagery (cloud/precipitation evolution, including convective initiation and decay — not just advection). Three architectures compared: a standard score-based diffusion model ("Diff"), a residual-correction diffusion model ("CorrDiff" — the same NVIDIA-originated pattern named in the brief), and a latent diffusion model ("LDM"); CorrDiff was the best performer, beating the other diffusion variants, a conventional U-Net, and persistence by roughly 1-2 K RMSE per the abstract-level summary (exact table not confirmed from the abstract alone).
- **Code / weights / licence:** Not found in the fetched abstract page — no GitHub link surfaced; treat as unknown/not confirmed rather than "none," since a code link may exist in the paper body.
- **Data:** Geostationary (GOES) infrared brightness-temperature imagery; specific channel(s)/satellite generation not stated in the abstract.
- **For HRRR+GOES:** Directly validates that CorrDiff, specifically, already outperforms plain diffusion and U-Net baselines on GOES-IR-shaped imagery in a closely related (satellite-to-satellite, not NWP-to-satellite) task — this is the strongest piece of evidence in this batch that the CorrDiff pattern named in the brief is a good technical bet for painting realistic km-scale cloud texture, even though the conditioning field here is prior satellite imagery rather than HRRR physics fields.

## Weather4cast challenge
- **Title:** Weather4cast Challenge (IARAI); code repository
- **Authors/venue:** IARAI (Institute of Advanced Research in Artificial Intelligence); NeurIPS competition series (2021-2025 editions)
- **Fetched:** https://github.com/iarai/weather4cast (VERIFIED)
- **Input -> output:** Four consecutive 15-minute frames of multi-channel satellite-derived fields (temperature, convective rainfall, tropopause-folding probability, cloud mask; ~3 km resolution, 256x256 px regions) -> the same four fields for the next 32 fifteen-minute steps (8-hour nowcast). This is satellite-derived-fields-to-future-fields, not NWP-to-satellite-image or satellite-to-radar as the brief's example phrasing suggested — flagging that mismatch explicitly.
- **Code / weights / licence:** Training code and a U-Net-3D baseline are in the repo; pretrained weights for the baseline and several competitor solutions (e.g. a 2022 Stage-2 solution's weights on Zenodo, DOI 10.5281/zenodo.7339193) are downloadable, generally after competition registration. No single unified license statement found for the core repo.
- **Data:** Satellite/radiance-derived fields over 11 European regions (5 with train+val, 6 test-only for transfer-learning evaluation), sourced with AEMet/NWC SAF, spanning multiple competition years.
- **For HRRR+GOES:** The one item in this batch with a real, runnable, weights-available codebase — useful as an engineering reference (dataloader patterns, U-Net-3D baseline, competition-grade evaluation harness) even though the task it solves (short-range nowcasting of satellite-derived fields) is not the HRRR-physics-to-image translation problem. If code reuse matters more than task match, this is the most practical starting point found.

## Yang et al. — 2026
- **Title:** CNN-Based Retrieval of 3D Cloud Structures Solely From Geostationary Satellite Imagery
- **Authors/venue:** Yang et al.; *Geophysical Research Letters* (AGU), 2026 (also posted to ESS Open Archive)
- **Fetched:** https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2025GL121014 (UNVERIFIED: 403 Forbidden); retried at https://essopenarchive.org/doi/full/10.22541/essoar.175762256.63942432/v1 (UNVERIFIED: 403 Forbidden). Two independent fetch attempts both blocked; content below from WebSearch snippets only.
- **Input -> output:** Geostationary satellite imagery (multispectral) alone -> 3D cloud structure, described in adjacent snippet coverage as reconstructing volumetric cloud masks at ~500 m vertical resolution with reported cloud-top-height bias around 450 m (that number is from a related-but-possibly-different lightweight retrieval model in the same search result set, not confirmed to be this exact paper — flagging the attribution risk).
- **Code / weights / licence:** Unknown/not confirmed.
- **Data:** Likely trained against active-sensor truth (CloudSat/CALIPSO-style vertical profiles are standard for this retrieval task) but not confirmed from the snippet for this specific paper.
- **For HRRR+GOES:** This is the "inverse direction" the brief asked for — deriving cloud vertical structure/height from satellite imagery with a CNN, playing the same role NOAA's operational ACHA algorithm plays but learned rather than physically retrieved (NOAA's Enterprise ACHA algorithm ATBD, at star.nesdis.noaa.gov, is the physics-based baseline this class of paper is implicitly compared against). Relevant to the decision only indirectly: if the pipeline ever needs to go satellite-image -> cloud-height-field (e.g. to build training targets or do a round-trip consistency check), this is the literature to start from, but every number above needs a clean re-fetch before it's load-bearing.

## Summary for the decision
The physics side is well-documented but its accuracy is reported qualitatively more often than as one clean number: the best-verified statement here (Otkin/DTC) is that HRRR's simulated brightness temperature has a **warm bias in the first forecast hour flipping to a cold bias within a few hours**, driven by upper-level cloud initialization and over-generation errors — not a single "X Kelvin RMSE" figure. Multiple independent institutions (NOAA/CIMSS for HRRR, CIRA/RAMMB for RAMS, DWD for ICON-D2/Lokal-Modell) run essentially the same CRTM-forward-model pattern, so the mechanism (physics fields -> radiative transfer -> brightness temperature) is mature and well precedented, but none of the fetched sources gave a single scalar bias/RMSE the pipeline could cite as "HRRR SBT vs GOES is accurate to within N K." On the learned side, the closest exact match to the proposed HRRR+GOES generator is Cheng (2021/2022) — NWP fields + IR channels in, GAN-painted visible-light satellite image out — but it has no released code or weights. The strongest technical validation of the CorrDiff pattern specifically is Chase et al. (2025), which shows CorrDiff beating plain diffusion, a U-Net, and persistence on GOES-IR-shaped imagery, though its conditioning field is prior satellite imagery, not HRRR physics fields — the exact HRRR-fields-to-GOES-texture pairing does not appear to have been published as a learned model yet. The only item with a genuinely reusable, weights-available codebase is Weather4cast, but it solves a different task (satellite-derived-field nowcasting, not NWP-to-image translation). No learned NWP-to-satellite-image model surfaced in this pass with both code AND pretrained weights openly released for that exact task — that is the biggest gap: the CorrDiff-for-HRRR+GOES idea appears to be genuinely novel rather than an existing model this project could fine-tune or adapt directly.
