"""
CivicResolve — Out-of-Distribution (OOD) Guard
================================================

WHY THIS EXISTS

The classifier in model_pipeline.py is a standard closed-set softmax
classifier trained on exactly 7 categories. This has a fundamental
limitation that no amount of additional training data removes on its
own: softmax always distributes 100% of its probability mass across the
classes it knows about, for ANY input whatsoever — including a photo of
a cat, a sunset, or random noise. There is no "none of the above" built
into the architecture itself.

CONFIDENCE_THRESHOLD in model_pipeline.py catches inputs the model is
genuinely torn between two of its known classes on. It does nothing for
inputs that are confidently, cleanly outside all 7 classes — which is
exactly what happened when a cat photo was classified as
"broken_streetlight" at high confidence (documented in the README's
Current Limitations section, and directly reproduced when a real user
submitted a cat photo through the live app).

THE FIX

Before running the specialized 7-class classifier, ask a general-purpose,
pretrained vision-language model (CLIP) a much simpler, more reliable
question first: "does this image look more like street infrastructure
damage, or more like something else entirely?"

CLIP was pretrained on hundreds of millions of real image-caption pairs
scraped from the open web, so — unlike our 1,448-image specialist model —
it already has a broad enough general understanding of "what a cat looks
like" vs. "what a pothole looks like" to catch this exact class of
mistake, with zero additional training required on our side.

This is a standard technique (zero-shot OOD gating via a foundation
model), not a hack. It does NOT fix the specialist model's actual
measured accuracy (58.3%) or its collapsed severity head — those still
need the fixes already on the README's roadmap (real transfer learning,
more/better training data, fixing the severity head). What it DOES fix
is the specific, demonstrated failure mode where a wildly unrelated photo
gets a confident, wrong, infrastructure-category label instead of being
correctly flagged for human review.

FAILURE MODE OF THIS GUARD ITSELF

CLIP zero-shot gating is a real improvement, not a perfect one. It can
still be wrong at the margins (e.g. a heavily graffiti'd wall photographed
very close-up might read as ambiguous). That's fine — its job is to catch
the *obvious* mismatches (animals, people, food, unrelated scenes), not
to replace human review entirely. Anything it's unsure about should still
fall through to the existing confidence-threshold path in
model_pipeline.py, not be silently trusted.
"""

import logging
from typing import Dict

from PIL import Image
import torch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Minimum similarity-score gap required before we trust an "in-domain"
# call. If the best in-domain and out-of-domain prompts score too close
# together, we don't have a confident signal either way — in that case
# this guard stays out of the way and lets the existing classifier +
# confidence threshold in model_pipeline.py make the call, rather than
# forcing a decision it isn't sure about.
OOD_MARGIN = 0.03

IN_DOMAIN_PROMPTS = [
    "a photo of a pothole in a paved road",
    "a photo of a broken or damaged streetlight",
    "a photo of graffiti spray-painted on a wall or surface",
    "a photo of illegally dumped trash or debris on a street",
    "a photo of a cracked or broken sidewalk",
    "a photo of a damaged, bent, or vandalized street sign",
    "a photo of damaged public infrastructure on a street",
]

# Concrete out-of-domain prompts, not just an implicit "everything else" -
# CLIP compares similarity scores, so it needs specific things to compare
# against rather than a vague negative class.
OUT_OF_DOMAIN_PROMPTS = [
    "a photo of a cat or other pet animal",
    "a photo of a person or group of people, not focused on infrastructure",
    "a photo of food or a meal",
    "a photo of nature, landscape, or scenery with no visible damage",
    "a random photo unrelated to street infrastructure",
]

_clip_model = None
_clip_preprocess = None
_text_features_cache = None
_load_failed = False  # sticky: don't retry a broken load on every request


def _load_clip() -> bool:
    """
    Lazy-loads CLIP once per process. Downloads ~350MB of pretrained
    weights on first call (requires network access at startup/first use).
    Returns True if the model is ready to use, False if loading failed
    for any reason (missing dependency, no network, etc).
    """
    global _clip_model, _clip_preprocess, _text_features_cache, _load_failed

    if _clip_model is not None:
        return True
    if _load_failed:
        return False

    try:
        import open_clip  # deferred import: only required if this guard runs
    except ImportError:
        logger.warning(
            "open_clip_torch is not installed - OOD guard disabled. "
            "Install with `pip install open_clip_torch` to enable it. "
            "Falling back to confidence-threshold-only gating."
        )
        _load_failed = True
        return False

    try:
        logger.info("Loading CLIP (ViT-B-32, openai weights) for OOD gating...")
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        tokenizer = open_clip.get_tokenizer("ViT-B-32")
        model.eval()

        all_prompts = IN_DOMAIN_PROMPTS + OUT_OF_DOMAIN_PROMPTS
        with torch.no_grad():
            text_tokens = tokenizer(all_prompts)
            text_features = model.encode_text(text_tokens)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        _clip_model = model
        _clip_preprocess = preprocess
        _text_features_cache = text_features
        logger.info("CLIP OOD guard ready.")
        return True

    except Exception as e:
        # Network failure, corrupt download, out of memory, etc. This
        # guard is a safety NET, not a required dependency - if it can't
        # load, we log clearly and fail open rather than blocking every
        # citizen report from being submitted.
        logger.error(f"CLIP OOD guard failed to load, disabling it: {e}")
        _load_failed = True
        return False


def check_in_domain(image: Image.Image) -> Dict:
    """
    Checks whether an image plausibly depicts street infrastructure
    damage at all, before the specialist 7-class model ever sees it.

    Returns:
        {
          "ood_check_available": bool,   # False if CLIP couldn't be loaded
          "is_in_domain": bool,          # only meaningful if available=True
          "best_in_domain_match": str,
          "best_out_of_domain_match": str,
          "in_domain_score": float,
          "out_of_domain_score": float,
        }
    """
    if not _load_clip():
        return {
            "ood_check_available": False,
            "is_in_domain": True,  # fail open: defer to the existing classifier
            "best_in_domain_match": None,
            "best_out_of_domain_match": None,
            "in_domain_score": None,
            "out_of_domain_score": None,
        }

    with torch.no_grad():
        image_input = _clip_preprocess(image.convert("RGB")).unsqueeze(0)
        image_features = _clip_model.encode_image(image_input)
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        similarities = (image_features @ _text_features_cache.T).squeeze(0)

    n_in = len(IN_DOMAIN_PROMPTS)
    in_domain_sims = similarities[:n_in]
    out_domain_sims = similarities[n_in:]

    best_in_idx = int(torch.argmax(in_domain_sims))
    best_out_idx = int(torch.argmax(out_domain_sims))
    in_score = float(in_domain_sims[best_in_idx])
    out_score = float(out_domain_sims[best_out_idx])

    is_in_domain = (in_score - out_score) > OOD_MARGIN

    return {
        "ood_check_available": True,
        "is_in_domain": is_in_domain,
        "best_in_domain_match": IN_DOMAIN_PROMPTS[best_in_idx],
        "best_out_of_domain_match": OUT_OF_DOMAIN_PROMPTS[best_out_idx],
        "in_domain_score": round(in_score, 4),
        "out_of_domain_score": round(out_score, 4),
    }
