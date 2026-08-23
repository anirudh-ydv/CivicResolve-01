"""
Exports training_feedback rows into a manifest.csv-compatible format for
a future retraining pass (Part 1's train_model()/CivicResolveDataset
expect exactly this shape: filepath,category,split columns).

Usage:
    python export_feedback_for_retraining.py [--min-rows N]

By default, only rows where `was_correction=True` are exported (the AI
was wrong and an admin fixed it) since those are the highest-value
signal for improving the model. Pass --include-confirmations to also
export confirmed-correct rows (still useful as additional real labeled
data, just lower priority).

This is meant to be run periodically (e.g. weekly, or whenever the
feedback count crosses a threshold - see README note at the bottom) and
the output merged into a new training run's manifest, NOT auto-applied.
A human should look at what's being exported before kicking off a
retrain, especially early on when feedback volume is small and a few
bad corrections could skew things.
"""
import argparse
import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import SessionLocal
from models.report import TrainingFeedback

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "ai", "data", "feedback_manifest.csv")

# Minimum feedback rows before we consider a retrain worthwhile. Below
# this, a handful of corrections would have outsized (likely noisy)
# influence relative to the ~1,448 images in the original bootstrap set.
MIN_ROWS_FOR_RETRAIN = 50


def export(include_confirmations: bool = False, min_rows: int = MIN_ROWS_FOR_RETRAIN):
    db = SessionLocal()
    query = db.query(TrainingFeedback)
    if not include_confirmations:
        query = query.filter(TrainingFeedback.was_correction == True)  # noqa: E712

    rows = query.all()
    db.close()

    if not rows:
        print("No training_feedback rows found matching the filter. Nothing to export.")
        return

    # Only export rows whose image file still actually exists on disk -
    # uploads can be cleaned up/rotated independently of the DB, and a
    # manifest row pointing at a missing file would break training.
    valid_rows = []
    missing = 0
    for r in rows:
        # image_path in the DB is a URL-style path like /uploads/xyz.jpg;
        # resolve it against the actual uploads directory.
        rel = r.image_path.lstrip("/")
        abs_path = os.path.join(os.path.dirname(__file__), "..", rel)
        if os.path.exists(abs_path):
            valid_rows.append((os.path.abspath(abs_path), r.admin_confirmed_category.value))
        else:
            missing += 1

    print(f"Found {len(rows)} feedback rows ({missing} with missing image files, skipped).")
    print(f"{len(valid_rows)} usable rows.")

    if len(valid_rows) < min_rows:
        print(
            f"\nBelow the {min_rows}-row threshold for a worthwhile retrain "
            f"(see MIN_ROWS_FOR_RETRAIN in this script). Exporting anyway "
            f"for inspection, but you probably shouldn't kick off training "
            f"on this yet - real accuracy improvement needs more signal "
            f"than a handful of corrections can provide."
        )

    # Stratified 70/15/15 split per category, same approach as
    # build_manifest.py, for consistency if this gets merged into a new
    # combined manifest.
    random.seed(42)
    by_cat = {}
    for filepath, category in valid_rows:
        by_cat.setdefault(category, []).append(filepath)

    out_rows = []
    for category, paths in by_cat.items():
        random.shuffle(paths)
        n = len(paths)
        n_train = int(round(n * 0.70))
        n_val = int(round(n * 0.15))
        for i, p in enumerate(paths):
            if i < n_train:
                split = "train"
            elif i < n_train + n_val:
                split = "val"
            else:
                split = "test"
            out_rows.append({"filepath": p, "category": category, "split": split})

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["filepath", "category", "split"])
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    print(f"\nWrote {len(out_rows)} rows to {OUTPUT_PATH}")
    print("Per-category counts:")
    from collections import Counter
    for cat, n in Counter(r["category"] for r in out_rows).items():
        print(f"  {cat}: {n}")
    print(
        "\nTo actually retrain: manually merge this file's rows into "
        "ai/data/manifest.csv (or point MANIFEST_PATH at a combined "
        "file) and re-run the training driver. Not automated on purpose - "
        "see the module docstring."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-confirmations", action="store_true")
    parser.add_argument("--min-rows", type=int, default=MIN_ROWS_FOR_RETRAIN)
    args = parser.parse_args()
    export(include_confirmations=args.include_confirmations, min_rows=args.min_rows)
