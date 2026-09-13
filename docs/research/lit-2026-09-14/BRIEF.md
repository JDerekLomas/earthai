# Literature pass brief — physics-conditioned cloud generation (earthai, 14 Sep 2026)

## Who reads this and what it decides
Derek (a designer/researcher, not a meteorologist) is deciding whether to build a
PHYSICS-CONDITIONED cloud-image generator: a physics weather model's cloud fields go IN
(HRRR, 3 km, hourly, CONUS; fields like low/mid/high cloud fraction, cloud top height,
simulated satellite brightness temperature), and an image model paints km-scale cloud
TEXTURE out that looks like a GOES satellite frame (768 px web-mercator, ~1.9 km/px,
California coast). The NVIDIA CorrDiff pattern. The alternative is staying with cheap
latent-space controls in an existing StyleGAN2 trained on GOES frames. Goal: "beauty with
some truth" — an animated sky that moves like real weather, not a forecast product.

## Rules (binding)
- FETCH every paper and repo before citing it. Use the arXiv abstract page, the DOI page, or
  the publisher page; for code, fetch the GitHub README. If a fetch fails twice, mark the
  entry UNVERIFIED and say what you could not confirm. Never cite from memory alone.
- Record for each item: title; first author + year; venue; URL you actually fetched;
  input -> output (what fields, what resolution, what lead time); code URL or "none found";
  pretrained weights available (yes/no/unknown); licence; training data; ONE or TWO
  sentences on what it concretely means for building the HRRR+GOES generator above.
- Budget: at most ~25 web requests. Prefer arXiv abs pages (fast, reliable). Do not read
  full PDFs unless the abstract cannot answer the fields above.
- Output: write ONLY to the file named in your task, in the format below. When done,
  print to the console ONLY: the number of items written, the number verified, the
  number unverified. No other console output — the file is the deliverable.

## File format
```
# <topic>
_Agent run 2026-09-14. N items, V verified, U unverified._

## <Short name> — <year>
- **Title:** ...
- **Authors/venue:** ...
- **Fetched:** <url> (VERIFIED | UNVERIFIED: <why>)
- **Input -> output:** ...
- **Code / weights / licence:** ...
- **Data:** ...
- **For HRRR+GOES:** ...

(repeat)

## Summary for the decision
5-10 sentences: what exists, what is reproducible, the closest thing to our exact setup,
and the biggest gap.
```
