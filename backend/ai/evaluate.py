"""
CivicResolve model evaluation script.

This did NOT previously exist in the codebase - the numbers in
TRAINING_REPORT.md (58.3% test accuracy, per-category precision/recall/F1,
severity MAE, the confidence-gating before/after comparison) were produced
ad hoc and were not reproducible from any script checked into the repo.
That's a real gap: it means nobody could re-run the same evaluation after
a future retrain without rebuilding this logic from scratch, and the
severity-head collapse specifically (Part C of the last review) was missed
by the MAE metric alone - it was only caught by manually testing individual
images against the live endpoint. This script exists so that check is
automatic and repeatable going forward.

Usage (run against a real trained checkpoint + manifest.csv with a real
`test` split - both require a real training run in an environment with
network access and/or bundled images, which this sandbox does not have):

    cd backend
    python -m ai.evaluate

Prints:
- Overall test accuracy, before and after confidence gating
- Per-category precision/recall/F1 + support (fails loudly, not silently,
  if any category has 0 support or 0 recall - see check_zero_recall())
- Confusion matrix
- Severity MAE against the heuristic bootstrap labels (same caveat as
  TRAINING_REPORT.md: this is NOT human ground truth)
- Severity OUTPUT DISTRIBUTION (min/max/mean/std) - this is the check that
  was missing before. A collapsed head (like the one found in the first
  real training run, which output ~1.0 for every single input) will show
  up here as a near-zero std, even when the averaged MAE number looks
  passably reasonable on its own.
"""
import csv
import sys
import numpy as np
import torch
from collections import defaultdict

from ai.model_pipeline import (
    DualHeadResNet,
    CivicResolveDataset,
    CLASS_NAMES,
    MODEL_PATH,
    MANIFEST_PATH,
    INFERENCE_TRANSFORM,
    CONFIDENCE_THRESHOLD,
    SEVERITY_MIN,
    SEVERITY_MAX,
    DEVICE,
)


def load_trained_model(model_path: str = MODEL_PATH, device=DEVICE) -> DualHeadResNet:
    model = DualHeadResNet(num_classes=len(CLASS_NAMES), pretrained=False, freeze_backbone=False)
    # Matches the exact torch.load call already used in model_pipeline.py's
    # own load_weights()/resume_from paths, for consistency.
    state_dict = torch.load(model_path, map_location=device)
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model


@torch.no_grad()
def run_test_set(model: DualHeadResNet, manifest_path: str = MANIFEST_PATH, device=DEVICE):
    """
    Runs the model over the real `test` split and returns raw predictions -
    no metric computation here, so the same raw output can be reused for
    accuracy, confusion matrix, AND the severity distribution check without
    re-running inference three times.
    """
    dataset = CivicResolveDataset(manifest_path, split="test", transform=INFERENCE_TRANSFORM)
    loader = torch.utils.data.DataLoader(dataset, batch_size=16, shuffle=False)

    all_true_cls, all_pred_cls, all_confidence = [], [], []
    all_true_sev, all_pred_sev = [], []

    for images, cls_targets, sev_targets in loader:
        images = images.to(device)
        logits, severity = model(images)
        probs = torch.softmax(logits, dim=1)
        confidence, pred_cls = probs.max(dim=1)

        all_true_cls.extend(cls_targets.tolist())
        all_pred_cls.extend(pred_cls.cpu().tolist())
        all_confidence.extend((confidence * 100).cpu().tolist())

        sev_true_scaled = sev_targets * (SEVERITY_MAX - SEVERITY_MIN) + SEVERITY_MIN
        all_true_sev.extend(sev_true_scaled.tolist())
        all_pred_sev.extend(severity.squeeze(-1).cpu().tolist())

    return {
        "true_cls": all_true_cls,
        "pred_cls": all_pred_cls,
        "confidence": all_confidence,
        "true_sev": all_true_sev,
        "pred_sev": all_pred_sev,
    }


def compute_classification_metrics(true_cls, pred_cls, confidence, gate: bool):
    """
    If gate=True, applies the same CONFIDENCE_THRESHOLD used in the real
    predict() inference path, downgrading low-confidence predictions to
    "other" - matching production behavior exactly, so this reports the
    same before/after comparison TRAINING_REPORT.md described (58.3% ->
    56.5%), not an approximation of it.
    """
    other_idx = CLASS_NAMES.index("other")
    effective_pred = []
    for p, c in zip(pred_cls, confidence):
        if gate and c < CONFIDENCE_THRESHOLD:
            effective_pred.append(other_idx)
        else:
            effective_pred.append(p)

    correct = sum(1 for t, p in zip(true_cls, effective_pred) if t == p)
    accuracy = correct / len(true_cls)

    per_class = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "support": 0})
    for t, p in zip(true_cls, effective_pred):
        per_class[t]["support"] += 1
        if t == p:
            per_class[t]["tp"] += 1
        else:
            per_class[t]["fn"] += 1
            per_class[p]["fp"] += 1

    rows = []
    for idx, name in enumerate(CLASS_NAMES):
        d = per_class[idx]
        precision = d["tp"] / (d["tp"] + d["fp"]) if (d["tp"] + d["fp"]) > 0 else 0.0
        recall = d["tp"] / (d["tp"] + d["fn"]) if (d["tp"] + d["fn"]) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        rows.append({
            "category": name, "precision": precision, "recall": recall,
            "f1": f1, "support": d["support"],
        })

    n = len(CLASS_NAMES)
    matrix = [[0] * n for _ in range(n)]
    for t, p in zip(true_cls, effective_pred):
        matrix[t][p] += 1

    return accuracy, rows, matrix


def check_zero_recall(rows, context: str):
    """
    Fails LOUDLY on stdout (not a silent pass) for any category with real
    support but zero recall - this is exactly the cracked_sidewalk failure
    mode from the first training run, and any category with zero support
    (damaged_sign, until Part D's data gap is resolved) is flagged
    separately so it isn't confused with a model failure.
    """
    problems = []
    for r in rows:
        if r["support"] == 0:
            print(f"  [{context}] NOTE: '{r['category']}' has 0 test examples "
                  f"- cannot evaluate, not a model failure, a data gap (see Part D).")
        elif r["recall"] == 0.0:
            problems.append(r["category"])
    if problems:
        print(f"  [{context}] *** ZERO RECALL WARNING ***: {', '.join(problems)} "
              f"- every real example of this category was misclassified. "
              f"Do not ship a model with this state without disclosing it.")
    return problems


def check_severity_distribution(pred_sev):
    """
    THE CHECK THAT WAS MISSING BEFORE. An averaged MAE can look reasonable
    even when every single prediction is the same constant value, if the
    ground-truth labels happen to average out near that constant. This
    function looks at the actual spread of predictions, which is what
    would have caught the severity head collapsing to ~1.0 for every
    input on the first real training run, instead of that only being found
    later by manually testing individual images against the live endpoint.
    """
    arr = np.array(pred_sev)
    stats = {
        "min": float(arr.min()), "max": float(arr.max()),
        "mean": float(arr.mean()), "std": float(arr.std()),
    }
    # A real, non-collapsed severity head predicting on a real, varied test
    # set should have meaningfully more spread than this. This threshold is
    # a deliberately loose sanity check, not a precise statistical test -
    # its only job is to make a collapse impossible to miss.
    COLLAPSE_STD_THRESHOLD = 0.5
    collapsed = stats["std"] < COLLAPSE_STD_THRESHOLD
    if collapsed:
        print(f"  *** SEVERITY HEAD COLLAPSE DETECTED ***: std={stats['std']:.4f} "
              f"across {len(arr)} test predictions (min={stats['min']:.2f}, "
              f"max={stats['max']:.2f}, mean={stats['mean']:.2f}). The model "
              f"is outputting nearly the same value for every input regardless "
              f"of what's in the photo. Do NOT ship this - severity_score in "
              f"every API response would be meaningless. See PART C notes in "
              f"model_pipeline.py for what was already tried.")
    else:
        print(f"  Severity distribution looks non-degenerate: "
              f"min={stats['min']:.2f} max={stats['max']:.2f} "
              f"mean={stats['mean']:.2f} std={stats['std']:.2f}")
    return stats, collapsed


def main():
    print(f"Loading model from {MODEL_PATH} ...")
    try:
        model = load_trained_model()
    except FileNotFoundError:
        print(f"ERROR: no trained checkpoint found at {MODEL_PATH}. "
              f"Run training first (python -m ai.model_pipeline).")
        sys.exit(1)

    print(f"Running inference over the real test split in {MANIFEST_PATH} ...")
    results = run_test_set(model)
    n = len(results["true_cls"])
    print(f"Evaluated {n} real test examples.\n")

    print("=== Classification accuracy: BEFORE confidence gating ===")
    acc_raw, rows_raw, matrix_raw = compute_classification_metrics(
        results["true_cls"], results["pred_cls"], results["confidence"], gate=False)
    print(f"  Overall accuracy: {acc_raw:.1%}")
    for r in rows_raw:
        print(f"  {r['category']:>20}: precision={r['precision']:.2f} "
              f"recall={r['recall']:.2f} f1={r['f1']:.2f} support={r['support']}")
    check_zero_recall(rows_raw, "before gating")

    print("\n=== Classification accuracy: AFTER confidence gating ===")
    acc_gated, rows_gated, matrix_gated = compute_classification_metrics(
        results["true_cls"], results["pred_cls"], results["confidence"], gate=True)
    print(f"  Overall accuracy: {acc_gated:.1%} "
          f"(change from ungated: {acc_gated - acc_raw:+.1%})")
    check_zero_recall(rows_gated, "after gating")

    print("\n=== Severity regression ===")
    mae = float(np.mean(np.abs(np.array(results["pred_sev"]) - np.array(results["true_sev"]))))
    print(f"  MAE vs heuristic bootstrap labels (NOT human ground truth): {mae:.2f}")
    stats, collapsed = check_severity_distribution(results["pred_sev"])

    print("\n=== Confusion matrix (rows=true, cols=predicted), ungated ===")
    header = "        " + " ".join(f"{c[:6]:>6}" for c in CLASS_NAMES)
    print(header)
    for i, row in enumerate(matrix_raw):
        print(f"{CLASS_NAMES[i][:8]:>8} " + " ".join(f"{v:>6}" for v in row))

    if collapsed:
        print("\nRESULT: severity head is still collapsed. The loss-weight "
              "change in model_pipeline.py (0.5x -> 1.75x) has NOT been "
              "verified to fix this - it needs a real retrain in an "
              "environment with actual training data, then re-run this "
              "script again before trusting severity_score anywhere.")
        sys.exit(2)


if __name__ == "__main__":
    main()
