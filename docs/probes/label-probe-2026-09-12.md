# Vision labeler probe — 2026-09-12

gemini-3.1-flash-lite, realtime, thinkingBudget 0, JSON schema. 72 tiles (54 clouds256
across six cloud-fraction buckets, 18 Landshapes z13), same tiles in every condition.
Prices from the price page that day: $0.25/M in, $1.50/M out; batch is half.

| condition | tok/tile in+out | $/1K tiles | 158K clouds set (batch) | cover~cloud_frac r | cloud_form agree vs single+meta | s/call |
|---|---|---|---|---|---|---|
| single | 1203+89 | 0.434 | $34 | 0.84 | 79% | 2.8 |
| single+meta | 1243+88 | 0.443 | $35 | 0.85 | — | 2.4 |
| grid2 (2x2) | 353+87 | 0.219 | $17 | 0.77 | 71% | 3.6 |
| grid3 (3x3) | 181+86 | 0.174 | $14 | 0.74 | 67% | 5.9 |
| grid4 (4x4) | 122+65 | 0.127 | $10 | 0.64 | 58% | 7.7 |

Findings
- A 256 px tile costs ~1,100 image tokens on 3.1 flash-lite, not the 258 the older
  docs imply. Measured, not assumed.
- Stitching cuts cost 2-3.5x and quality with it: coverage correlation falls from
  0.85 to 0.64 and form agreement to 58% at 4x4. At $35 for the whole clouds set the
  saving is ~$20 and not worth the loss. Single tile + metadata, batch.
- Swath-gap positive control (12 tiles with >3% black vs 12 clean): model recall
  7/12, false alarms 0/12; it missed gaps of 4-13% and scored a 12.8%-black tile
  usable 4. The pixel count is the better artifact detector. Ask the model only for
  what pixels cannot count: cloud organisation, landform, subject quality.
- Metadata costs ~40 tokens; its effect on accuracy is unmeasurable until human
  verdicts exist (79% form agreement with/without says it changes answers).
- Realtime at 4 workers is ~26 h for 158K; batch is the only sensible full run.

Next: when the triage pass is in, calibrate `usable` against keep/reject per band.
