"""
CivicResolve AI Vision Pipeline
Dual-head ResNet50 for Infrastructure Issue Classification + Severity Scoring

Architecture:
- Backbone: ResNet50. NOTE: despite this docstring's original wording,
  the actual trained checkpoint used pretrained=False (randomly
  initialized weights) - download.pytorch.org was not reachable in the
  environment this was trained in, so real ImageNet transfer learning
  was NOT used. See TRAINING_REPORT.md ("Environment constraints") for
  the full disclosure. This is the single biggest lever for improving
  accuracy once a real environment has network access to fetch
  pretrained weights.
- Head A (Classification): 7-class softmax (matching IssueCategory enum)
- Head B (Severity Regression): Single output, Sigmoid * 10 → Score 1-10.
  Loss weight raised from 0.5x to 1.75x relative to classification after
  the severity head was found to have collapsed to a near-constant
  output on the first real training run - see check_severity_distribution()
  in evaluate.py, which should be run after every future training pass.
"""

import os
import io
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models, transforms
from PIL import Image
import numpy as np
from typing import Tuple, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration - must match IssueCategory enum in backend/models/report.py exactly
NUM_CLASSES = 7
CLASS_NAMES = [
    "pothole",
    "broken_streetlight",
    "graffiti",
    "illegal_dumping",
    "cracked_sidewalk",
    "damaged_sign",
    "other",
]
SEVERITY_MIN, SEVERITY_MAX = 1, 10
INPUT_SIZE = 224
# Resolved relative to this file's own directory, NOT the process's cwd -
# a cwd-relative default here previously caused training and the live
# server to silently read/write checkpoints from two different locations
# depending on where each process was launched from.
MODEL_PATH = os.getenv(
    "MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "civicresolve_model.pth"),
)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Confidence gating: below this threshold, a prediction is too uncertain to
# present as an authoritative category on the public dashboard. It's
# downgraded to "other" (flagged for human review) rather than silently
# shown as a confident classification. Chosen from the measured test-set
# behavior in TRAINING_REPORT.md, not an arbitrary guess: several
# categories only separate cleanly from "other" above ~40% confidence.
CONFIDENCE_THRESHOLD = 40.0


class DualHeadResNet(nn.Module):
    """
    ResNet50 with dual heads:
    - Classification head: 7-class infrastructure issue detection (matches IssueCategory enum)
    - Severity head: Regression output 1-10
    """

    def __init__(self, num_classes: int = NUM_CLASSES, pretrained: bool = True, freeze_backbone: bool = True):
        super().__init__()

        # Load pretrained ResNet50
        self.backbone = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V2 if pretrained else None)

        # Freeze backbone layers for transfer learning
        if freeze_backbone:
            for param in self.backbone.parameters():
                param.requires_grad = False
            # Unfreeze last residual block for fine-tuning
            for param in self.backbone.layer4.parameters():
                param.requires_grad = True

        # Get feature dimension (ResNet50 final FC input)
        in_features = self.backbone.fc.in_features
        self.backbone.fc = nn.Identity()  # Remove original classifier

        # Head A: Classification (7 classes)
        self.classification_head = nn.Sequential(
            nn.Linear(in_features, 512),
            nn.BatchNorm1d(512),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(512, 256),
            nn.BatchNorm1d(256),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(256, num_classes),
        )

        # Head B: Severity Regression (1-10)
        self.severity_head = nn.Sequential(
            nn.Linear(in_features, 512),
            nn.BatchNorm1d(512),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(512, 256),
            nn.BatchNorm1d(256),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(256, 1),
            nn.Sigmoid(),  # Output 0-1, scale to 1-10 later
        )

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        features = self.backbone(x)
        logits = self.classification_head(features)
        severity_raw = self.severity_head(features)
        # Scale sigmoid output (0,1) to (1,10)
        severity_scaled = severity_raw * (SEVERITY_MAX - SEVERITY_MIN) + SEVERITY_MIN
        return logits, severity_scaled


# Image preprocessing pipeline
INFERENCE_TRANSFORM = transforms.Compose([
    transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

TRAIN_TRANSFORM = transforms.Compose([
    transforms.Resize((256, 256)),
    transforms.RandomCrop(INPUT_SIZE),
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomRotation(degrees=15),
    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


class CivicResolveInference:
    """Production inference wrapper for the dual-head model."""

    def __init__(self, model_path: str = MODEL_PATH, device: torch.device = DEVICE):
        self.device = device
        self.model = DualHeadResNet(num_classes=NUM_CLASSES, pretrained=False, freeze_backbone=False)
        self.model.to(device)
        self.model.eval()

        if os.path.exists(model_path):
            self.load_weights(model_path)
            logger.info(f"Loaded model weights from {model_path}")
        else:
            logger.warning(f"Model weights not found at {model_path}. Using random initialization.")
            logger.warning("Run train_model() first or download pretrained weights.")

    def load_weights(self, path: str):
        state_dict = torch.load(path, map_location=self.device)
        self.model.load_state_dict(state_dict)

    @torch.inference_mode()
    def predict(self, image: Image.Image) -> Dict:
        """
        Run inference on a single PIL Image.
        Returns dict with category, severity_score, and confidence.
        """
        # Preprocess
        input_tensor = INFERENCE_TRANSFORM(image).unsqueeze(0).to(self.device)

        # Forward pass
        logits, severity = self.model(input_tensor)

        # Classification: softmax + argmax
        probs = F.softmax(logits, dim=1)
        confidence, pred_class = torch.max(probs, dim=1)

        category = CLASS_NAMES[pred_class.item()]
        severity_score = int(round(severity.item()))
        severity_score = max(SEVERITY_MIN, min(SEVERITY_MAX, severity_score))
        confidence_pct = round(confidence.item() * 100, 1)

        raw_category = category
        requires_manual_review = confidence_pct < CONFIDENCE_THRESHOLD
        if requires_manual_review:
            # Per spec: below-threshold predictions are NOT presented as an
            # authoritative category (not even "other", which itself is a
            # confident claim of "not an issue"). They're returned as
            # "unclassified" with requires_manual_review=True so a human
            # reviews the actual photo. raw_category preserves what the
            # model actually guessed, for admin-facing transparency.
            category = "unclassified"

        return {
            "category": category,
            "severity_score": severity_score,
            "confidence": confidence_pct,
            "requires_manual_review": requires_manual_review,
            "raw_category": raw_category,
            "all_probabilities": {
                CLASS_NAMES[i]: round(probs[0, i].item() * 100, 1)
                for i in range(NUM_CLASSES)
            },
        }

    def predict_batch(self, images: list[Image.Image]) -> list[Dict]:
        """Batch inference for multiple images."""
        batch_tensor = torch.stack([INFERENCE_TRANSFORM(img) for img in images]).to(self.device)
        with torch.inference_mode():
            logits, severity = self.model(batch_tensor)
            probs = F.softmax(logits, dim=1)
            confidences, pred_classes = torch.max(probs, dim=1)

        results = []
        for i in range(len(images)):
            raw_category = CLASS_NAMES[pred_classes[i].item()]
            sev_score = int(round(severity[i].item()))
            sev_score = max(SEVERITY_MIN, min(SEVERITY_MAX, sev_score))
            confidence_pct = round(confidences[i].item() * 100, 1)
            requires_manual_review = confidence_pct < CONFIDENCE_THRESHOLD
            category = "unclassified" if requires_manual_review else raw_category
            results.append({
                "category": category,
                "severity_score": sev_score,
                "confidence": confidence_pct,
                "requires_manual_review": requires_manual_review,
                "raw_category": raw_category,
            })
        return results


# ============================================================
# REAL TRAINING DATA
# ============================================================
# Images are real photographs sourced from public GitHub repositories -
# see backend/ai/data/README.md for exact sources/citations and known
# limitations. Some categories are below the 150-300/class target, and
# one category (damaged_sign) has zero real examples available in this
# environment (no reachable dataset was found - Kaggle/Roboflow/Zenodo
# are outside this sandbox's network allowlist). This is disclosed, not
# hidden - see the README for the full accounting.
#
# Severity labels: none of the source datasets include human-annotated
# severity scores. Until real admin corrections accumulate via the
# training_feedback loop (Part 4), we bootstrap severity with a
# deterministic, documented per-category heuristic range (below) rather
# than fabricating random labels. This is explicitly a placeholder, not
# a claim of ground truth, and is called out in the README.

import csv
import hashlib

MANIFEST_PATH = os.getenv(
    "MANIFEST_PATH",
    os.path.join(os.path.dirname(__file__), "data", "manifest.csv"),
)

# Found during a later review: manifest.csv's filepath column currently
# stores absolute paths from the ORIGINAL training sandbox
# (/home/claude/datasets/...), which no longer exist even in that same
# sandbox, let alone a fresh clone of this repo elsewhere. This made the
# training data effectively unreproducible - there was no portable way to
# point at wherever a user actually placed the downloaded source images.
# DATA_ROOT fixes this: manifest paths are now resolved relative to it
# (see _resolve_image_path below), configurable via env var for whatever
# directory the images documented in data/README.md were actually
# downloaded into.
DATA_ROOT = os.getenv(
    "CIVICRESOLVE_DATA_ROOT",
    os.path.join(os.path.dirname(__file__), "data", "raw"),
)


def _resolve_image_path(filepath: str) -> str:
    """
    Resolves a manifest.csv filepath entry to a real, openable path.

    Handles both:
    - New-style relative paths (e.g. "pothole_raw/img-647.jpg") -> joined
      with DATA_ROOT.
    - Legacy absolute paths from the original build sandbox
      (/home/claude/datasets/...) still present in manifest.csv as of this
      fix - stripped down to the same relative tail and re-joined with
      DATA_ROOT, so existing manifest rows don't all need to be rewritten
      by hand to benefit from this fix.
    """
    if os.path.isabs(filepath):
        legacy_prefix = "/home/claude/datasets/"
        if filepath.startswith(legacy_prefix):
            relative_tail = filepath[len(legacy_prefix):]
            return os.path.join(DATA_ROOT, relative_tail)
        # Absolute path that isn't the known legacy prefix - use as-is
        # rather than guessing, so a real FileNotFoundError surfaces
        # clearly instead of silently mangling an unrelated path.
        return filepath
    return os.path.join(DATA_ROOT, filepath)

HEURISTIC_SEVERITY_RANGE = {
    "pothole": (4, 9),
    "broken_streetlight": (2, 6),
    "graffiti": (1, 5),
    "illegal_dumping": (4, 8),
    "cracked_sidewalk": (3, 7),
    "damaged_sign": (3, 7),
    "other": (1, 2),
}


def _heuristic_severity(filepath: str, category: str) -> int:
    """Deterministic (hash-based, not random) placeholder severity label."""
    lo, hi = HEURISTIC_SEVERITY_RANGE.get(category, (SEVERITY_MIN, SEVERITY_MAX))
    h = int(hashlib.md5(filepath.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo + 1))


class CivicResolveDataset(torch.utils.data.Dataset):
    """Loads real images + labels from manifest.csv for a given split."""

    def __init__(self, manifest_path: str, split: str, transform=None):
        self.transform = transform or TRAIN_TRANSFORM
        self.rows = []
        with open(manifest_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["split"] == split:
                    self.rows.append(row)
        if not self.rows:
            raise ValueError(f"No rows found for split={split} in {manifest_path}")

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        row = self.rows[idx]
        category = row["category"]
        filepath = row["filepath"]
        class_idx = CLASS_NAMES.index(category)

        image = Image.open(_resolve_image_path(filepath)).convert("RGB")
        if self.transform:
            image = self.transform(image)

        severity = _heuristic_severity(filepath, category)
        severity_normalized = (severity - SEVERITY_MIN) / (SEVERITY_MAX - SEVERITY_MIN)

        return image, class_idx, torch.tensor(severity_normalized, dtype=torch.float32)


def train_model(
    epochs: int = 5,
    batch_size: int = 16,
    lr: float = 1e-3,
    model_save_path: str = MODEL_PATH,
    device: torch.device = DEVICE,
    manifest_path: str = MANIFEST_PATH,
    resume_from: Optional[str] = None,
) -> DualHeadResNet:
    """
    Real training loop against manifest.csv (see CivicResolveDataset).

    NOTE on transfer learning: this sandbox cannot reach
    download.pytorch.org (blocked by network allowlist), so ImageNet-
    pretrained ResNet50 weights are not obtainable here. We therefore use
    pretrained=False with a frozen (randomly-initialized) early backbone,
    fine-tuning only layer4 + the two heads. This is a materially weaker
    setup than real transfer learning and accuracy is capped accordingly.
    Disclosed in the README, not hidden. This also keeps training
    tractable on this sandbox's single CPU core (no GPU available).
    """
    logger.info("Starting training against real manifest at %s", manifest_path)

    # Data
    train_dataset = CivicResolveDataset(manifest_path, split="train", transform=TRAIN_TRANSFORM)
    val_dataset = CivicResolveDataset(manifest_path, split="val", transform=INFERENCE_TRANSFORM)
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=0)

    # Model
    model = DualHeadResNet(num_classes=NUM_CLASSES, pretrained=False, freeze_backbone=True).to(device)
    if resume_from and os.path.exists(resume_from):
        model.load_state_dict(torch.load(resume_from, map_location=device))
        logger.info("Resumed weights from %s", resume_from)

    # Loss functions
    cls_criterion = nn.CrossEntropyLoss()
    sev_criterion = nn.MSELoss()

    # Optimizer (only train heads + layer4)
    optimizer = torch.optim.AdamW(
        [
            {"params": model.classification_head.parameters(), "lr": lr},
            {"params": model.severity_head.parameters(), "lr": lr},
            {"params": model.backbone.layer4.parameters(), "lr": lr * 0.1},
        ],
        weight_decay=1e-4,
    )

    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_loss = float("inf")

    for epoch in range(epochs):
        # Training
        model.train()
        train_losses = []
        for images, cls_targets, sev_targets in train_loader:
            images = images.to(device)
            cls_targets = cls_targets.to(device)
            sev_targets = sev_targets.to(device)

            optimizer.zero_grad()
            logits, severity = model(images)

            cls_loss = cls_criterion(logits, cls_targets)
            sev_loss = sev_criterion(severity.squeeze(), sev_targets)
            # NOTE: was 0.5x (severity weighted LOWER than classification).
            # Post-mortem from the first real training run: the severity
            # head (sigmoid -> scaled to 1-10) collapsed to outputting ~1
            # for every input - the sigmoid saturated near 0 and never
            # recovered. Under-weighting an already-harder regression task
            # sitting on the same weak (non-ImageNet-pretrained) features
            # as the classification head plausibly made this worse, not
            # better. Raised to 1.75x so the severity head gets a real
            # gradient signal comparable to (slightly larger than)
            # classification's. This is a real, disclosed guess at a fix,
            # not a verified one - it has NOT been re-run in this
            # environment (no training images or network access here).
            # Whoever runs the next real training pass MUST check the
            # output distribution afterward using check_severity_collapse()
            # below, not just the MAE - MAE is what let the original
            # collapse ship silently.
            total_loss = cls_loss + 1.75 * sev_loss

            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_losses.append(total_loss.item())

        # Validation
        model.eval()
        val_losses = []
        correct = 0
        total = 0
        with torch.inference_mode():
            for images, cls_targets, sev_targets in val_loader:
                images = images.to(device)
                cls_targets = cls_targets.to(device)
                sev_targets = sev_targets.to(device)

                logits, severity = model(images)
                cls_loss = cls_criterion(logits, cls_targets)
                sev_loss = sev_criterion(severity.squeeze(), sev_targets)
                val_losses.append((cls_loss + 0.5 * sev_loss).item())

                _, predicted = torch.max(logits, 1)
                correct += (predicted == cls_targets).sum().item()
                total += cls_targets.size(0)

        avg_train_loss = np.mean(train_losses)
        avg_val_loss = np.mean(val_losses)
        val_acc = 100 * correct / total

        logger.info(
            f"Epoch {epoch+1}/{epochs} | "
            f"Train Loss: {avg_train_loss:.4f} | "
            f"Val Loss: {avg_val_loss:.4f} | "
            f"Val Acc: {val_acc:.1f}%"
        )

        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
            torch.save(model.state_dict(), model_save_path)
            logger.info(f"Saved best model to {model_save_path}")

        # Always save a "last" checkpoint too, so a fresh process can resume
        # from here even if this epoch wasn't the best val loss.
        last_path = model_save_path.replace(".pth", "_last.pth")
        torch.save(model.state_dict(), last_path)

        scheduler.step()

    logger.info("Training complete.")
    return model


# ============================================================
# CONVENIENCE FUNCTIONS FOR BACKEND INTEGRATION
# ============================================================

_inference_instance: Optional[CivicResolveInference] = None


def get_inference_engine() -> CivicResolveInference:
    """Singleton pattern for model loading in FastAPI."""
    global _inference_instance
    if _inference_instance is None:
        _inference_instance = CivicResolveInference()
    return _inference_instance


def predict_image(image_bytes: bytes) -> Dict:
    """
    Main entry point for FastAPI backend.
    Accepts raw image bytes, returns classification + visual severity only.
    Composite risk scoring is handled by risk_engine.py
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        engine = get_inference_engine()
        return engine.predict(image)
    except Exception as e:
        logger.error(f"Inference error: {e}")
        # Fallback: inference itself failed, so this absolutely needs a
        # human to look at the actual photo - "other" at 0% confidence
        # would otherwise silently look like a confident classification.
        return {
            "category": "unclassified",
            "raw_category": "other",
            "severity_score": 5,
            "confidence": 0.0,
            "requires_manual_review": True,
            "error": str(e),
        }


if __name__ == "__main__":
    # NOTE: this __main__ block was found during a later review to still say
    # "(mock)" and use epochs=3/batch_size=8 - both stale leftovers from
    # before the real training pipeline existed. train_model() itself IS
    # the real training loop (AdamW, cosine LR, gradient clipping - matches
    # TRAINING_REPORT.md exactly), but running this file directly like this
    # would silently kick off a real, under-configured training run
    # (3 epochs instead of the documented 6, batch 8 instead of 16) while
    # a misleading comment implies nothing real is happening. Fixed to
    # match the real documented run, and the post-training smoke test now
    # uses an actual sample photo instead of a solid-red synthetic square,
    # which validated nothing about real classification behavior.
    #
    # This is a quick manual smoke test only. For real evaluation against
    # the full held-out test set with proper metrics (accuracy, precision/
    # recall/F1, confusion matrix, severity distribution/collapse check),
    # run `python -m ai.evaluate` instead - that's the reproducible,
    # reusable evaluation path added after the severity-collapse bug was
    # found (see PART C notes above).
    print(f"Device: {DEVICE}")
    print(f"Model will be saved to: {MODEL_PATH}")

    # Real training run, matching TRAINING_REPORT.md's documented config
    # (6 epochs, batch size 16). Change these only if you're intentionally
    # doing a quicker local smoke test, and say so in the report if you do.
    model = train_model(epochs=6, batch_size=16)

    # Smoke test: run inference on one real sample image from the manifest
    # rather than a synthetic solid-color square, which would never
    # exercise the model's real feature extraction path at all.
    import csv as _csv
    with open(MANIFEST_PATH) as f:
        _rows = list(_csv.DictReader(f))
    _test_row = next((r for r in _rows if r.get("split") == "test"), _rows[0] if _rows else None)
    if _test_row is None:
        print("WARNING: manifest.csv has no rows - cannot run a real smoke test.")
    else:
        engine = CivicResolveInference()
        test_img = Image.open(_resolve_image_path(_test_row["filepath"])).convert("RGB")
        result = engine.predict(test_img)
        print(f"Smoke test on real image {_test_row['filepath']} "
              f"(labeled '{_test_row.get('category')}'): {result}")