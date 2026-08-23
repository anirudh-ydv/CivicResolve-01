# CivicResolve — AI Model Training Report

**This report describes a real training run against real data.** No numbers
here are fabricated or estimated — they come from an actual 6-epoch training
run and an actual evaluation pass against a held-out test set, both run in
this environment. See `backend/ai/data/README.md` for exact dataset sources.

## Environment constraints that shaped this run (disclosed up front)

1. **No Kaggle/Roboflow/HuggingFace/Zenodo access.** This sandbox's network
   is allowlisted to a small set of domains (GitHub, PyPI, npm, etc.).
   Dataset acquisition was limited to real images hosted directly in GitHub
   repos or GitHub release assets.
2. **No ImageNet-pretrained weights.** `download.pytorch.org` returns
   `403 host_not_allowed`. Training used `pretrained=False` — a randomly
   initialized backbone with only `layer4` + the two heads unfrozen and
   trained — instead of real transfer learning. This is a materially
   weaker starting point than the architecture's docstring originally
   implied ("ImageNet pretrained, frozen for transfer learning").
3. **1 CPU core, no GPU.** `torch.cuda.is_available()` is `False`. A single
   training batch of 16 images took ~2.4s even with a frozen backbone;
   the full unfrozen network was ~6.8s/batch, which was not tractable for
   a full run in this session.

Given these three constraints together, the honest expectation going in
was that this run would land well short of a production-grade model — and
it did. The value of what follows is that it's real and honestly reported,
not that it's high-performing.

## Data

1,448 real images, 6 of 7 categories represented (see dataset README for
full sourcing and the disclosed `damaged_sign` gap). Stratified 70/15/15
split: 1,014 train / 218 val / 216 test.

## Training

- 6 epochs, batch size 16, AdamW (lr 1e-3 for heads, 1e-4 for `layer4`),
  cosine LR schedule, gradient clipping at 1.0.
- Best checkpoint by validation loss: **epoch 3** (val_loss 1.4515).
- Per-epoch validation accuracy: 38.1% → 53.2% → **55.0%** → 56.9% → 58.7%
  → 54.1% (epoch 4-6 val loss stopped improving — early signs of
  overfitting on this small dataset, consistent with its size).

## Test-set results (held-out, never seen during training)

Overall accuracy: **58.3%** (126/216), before confidence gating.

| Category | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| pothole | 0.69 | 0.52 | 0.60 | 48 |
| broken_streetlight | 0.72 | 0.86 | 0.78 | 44 |
| graffiti | 0.83 | 0.40 | 0.54 | 48 |
| illegal_dumping | 0.51 | 0.90 | 0.65 | 20 |
| cracked_sidewalk | **0.00** | **0.00** | **0.00** | 11 |
| damaged_sign | — | — | — | **0 (no test data — see dataset README)** |
| other | 0.38 | 0.58 | 0.46 | 45 |

**`cracked_sidewalk` has near-zero recall — stated explicitly, as required.**
Every one of the 11 test examples was misclassified (6 as pothole, 2 as
illegal_dumping, 3 as other). This is consistent with it being the
smallest training class (54 train images) and visually similar to
`pothole` at low resolution — cracks and small potholes both present as
irregular dark linear/patchy regions to a backbone that never got
ImageNet pretraining to learn general edge/texture features from.

`damaged_sign` was excluded from training entirely (no real data found) —
it will never be predicted, which is arguably the "safe" failure mode
given the alternative is a confident guess with zero grounding.

Severity MAE: 2.91 (on a 1-10 scale) — **against the heuristic bootstrap
labels described in the dataset README, not human ground truth.** This
number says the severity head learned the heuristic's per-category
pattern reasonably but imperfectly; it says nothing about real-world
severity accuracy, which can only be measured after real admin
corrections accumulate via the Part 4 feedback loop.

### Severity head collapse (found during live endpoint testing, not caught by the MAE metric above)

Running the deployed model against 15 real test-set images through the
actual inference path revealed the severity head outputs **exactly 1
(the minimum) for every single image**, regardless of category or the
classification head's confidence. This is a real training failure, not a
code bug — the forward pass math was checked and is correct
(`sigmoid_output * 9 + 1`, so an output of exactly 1 means the sigmoid is
saturating at ~0 for every input). The MAE metric above didn't surface
this clearly on its own because it's an average, not a distribution
check — a lesson for how this report itself was validated.

**Likely cause:** the severity head is a 3-layer MLP with BatchNorm and
dropout sitting on top of the same frozen/randomly-initialized backbone
discussed above. With weak, uninformative features feeding it, 6 epochs,
and the severity loss weighted at only 0.5x the classification loss in
the combined objective, it plausibly collapsed to a degenerate low-output
solution rather than actually learning to predict the mean.

**Practical implication:** severity_score in every API response is
currently not meaningful and should not be trusted or surfaced as-is.
This needs a follow-up training pass (more epochs, a higher severity
loss weight, and ideally real ImageNet-pretrained features once network
access allows it) before the severity score can be relied on. Flagging
this clearly rather than letting a passing-looking MAE number hide it.

## Confidence gating

Implemented in `model_pipeline.py`: predictions below 40% confidence are
downgraded to `other` with the raw prediction preserved in the response
(`raw_category`, `low_confidence` fields) for admin visibility rather than
silently discarded.

**Measured effect on the test set:**
- 47/216 (21.8%) predictions were gated as low-confidence.
- Accuracy actually *dropped slightly* after gating: 58.3% → 56.5%.
  Some low-confidence predictions were correct guesses that got
  downgraded to `other` and thus became "wrong" by the strict category
  match. This is a real, disclosed trade-off, not a clear win — gating
  optimizes for not presenting an uncertain guess as authoritative, at a
  measured small cost to raw accuracy.

**A more important, separate finding from live endpoint testing:**
gating does **not** catch confidently-wrong predictions on out-of-domain
images. Of 5 real "unrelated" test photos (dog, cat, pug, pizza, burger —
none an infrastructure issue), the model correctly fell back to `other`
for 3, but:
- A tabby cat photo was classified as `broken_streetlight` at **75.1%
  confidence** — well above the gating threshold, so it was *not* caught.
- A pizza photo was classified as `graffiti` at 49.8% confidence — also
  not caught.

This is a real, unresolved limitation: a softmax classifier trained only
on the 6 in-domain classes has no genuine open-set/out-of-distribution
detection. It will confidently mis-file some out-of-domain photos.
Confidence thresholding alone cannot fix this; a proper fix would need
either an explicit "not an infrastructure issue" contrastive training
signal beyond the current `other` class, or a separate OOD detector.
Flagging this rather than tuning the threshold until it looks better on
this specific 5-image sample, which would just be overfitting to the demo.

## What would actually improve this model, in priority order

1. **ImageNet-pretrained weights** — the single biggest lever. Not
   available in this sandbox; would need network access to
   `download.pytorch.org` or a pre-downloaded weights file supplied by
   the user.
2. **More `cracked_sidewalk` and `damaged_sign` data** — both are
   currently unusable for real deployment.
3. **A real GPU** — would allow full-network fine-tuning instead of the
   frozen-backbone compromise, likely a large accuracy jump on its own.
4. **Real severity ground truth** from the Part 4 admin feedback loop,
   replacing the heuristic bootstrap labels.
