"""
Build a real, non-synthetic manifest.csv for CivicResolve training.
Sources (all real photographs from public GitHub repos, no Kaggle/Roboflow
access available in this sandbox's network allowlist):

  pothole             <- jaygala24/pothole-detection (GitHub release, 1243 real photos)
  graffiti            <- mitwapalkhiwala/Graffiti-Detection (805 real photos)
  broken_streetlight  <- Team16Project/Street-Light-Dataset, "Not Working" class (294 real photos)
  cracked_sidewalk    <- khanhha/crack_segmentation test images (77 real photos - BELOW 150 target)
  illegal_dumping     <- garythung/trashnet "trash" class (137 real photos - DOMAIN MISMATCH:
                          studio photos of loose trash items, not in-situ street dumping scenes)
  damaged_sign        <- NO REAL DATASET FOUND reachable from this sandbox (0 images - see README)
  other (negative)    <- EliSchwartz/imagenet-sample-images, 300 random real photos
                          (animals, food, objects - used as the "not an infra issue" class)

No synthetic/generated images are used anywhere in this manifest.
"""
import os, csv, random, hashlib

random.seed(42)

# FIXED during later review: these used to be hardcoded to
# "/home/claude/datasets/..." - a path specific to the original build
# sandbox that no longer exists (confirmed: gone even from that same
# sandbox by the time of this fix). That made this script, and the
# manifest.csv it produces, non-reproducible anywhere else. Now
# configurable via CIVICRESOLVE_DATA_ROOT (same env var model_pipeline.py
# reads), defaulting to backend/ai/data/raw/ alongside this file.
#
# To actually regenerate manifest.csv for real:
#   1. Clone/download the 6 source repos listed in README.md's table into
#      DATA_ROOT, matching the subfolder names below (pothole_raw/,
#      graffiti_raw/, etc.) - e.g.:
#        git clone https://github.com/jaygala24/pothole-detection $DATA_ROOT/pothole_raw
#   2. Run: CIVICRESOLVE_DATA_ROOT=/path/to/data python build_manifest.py
#   3. manifest.csv will contain paths RELATIVE to DATA_ROOT, resolved at
#      load time by model_pipeline.py's _resolve_image_path() - this
#      script no longer needs to know or care where DATA_ROOT actually is
#      once it's done writing.
DATA_ROOT = os.getenv(
    "CIVICRESOLVE_DATA_ROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw"),
)

SOURCES = {
    "pothole": os.path.join(DATA_ROOT, "pothole_raw"),
    "graffiti": os.path.join(DATA_ROOT, "graffiti_raw"),
    "broken_streetlight": os.path.join(DATA_ROOT, "broken_streetlight_raw"),
    "cracked_sidewalk": os.path.join(DATA_ROOT, "cracked_sidewalk_raw"),
    "illegal_dumping": os.path.join(DATA_ROOT, "illegal_dumping_raw"),
    "other": os.path.join(DATA_ROOT, "other_pool"),
}

# Cap oversized classes so no single class dominates the (already CPU-limited)
# training run, and so epoch time stays tractable on this sandbox's 1 CPU core.
CAPS = {
    "pothole": 320,
    "graffiti": 320,
    "other": 300,
}

IMG_EXT = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}

rows = []
for category, root in SOURCES.items():
    files = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if os.path.splitext(f)[1] in IMG_EXT:
                files.append(os.path.join(dirpath, f))
    files.sort()  # deterministic order before shuffling
    random.shuffle(files)
    cap = CAPS.get(category)
    if cap:
        files = files[:cap]
    for fpath in files:
        # Store the path RELATIVE to DATA_ROOT, not absolute - this is the
        # actual portability fix. model_pipeline.py's _resolve_image_path()
        # re-joins this with whatever DATA_ROOT is set to at load time, on
        # whatever machine that happens to be.
        rel_fpath = os.path.relpath(fpath, DATA_ROOT)
        rows.append({"filepath": rel_fpath, "category": category})

print("Per-category counts:")
from collections import Counter
counts = Counter(r["category"] for r in rows)
for c, n in counts.items():
    print(f"  {c}: {n}")
print(f"TOTAL: {len(rows)}")

# damaged_sign explicitly has 0 real examples available - document, don't fake
if "damaged_sign" not in counts:
    print("\nWARNING: 'damaged_sign' has 0 real training images available "
          "(no reachable real dataset found). Model will never confidently "
          "predict this class - documented in README limitations.")

# 70/15/15 split, stratified per category
random.shuffle(rows)
by_cat = {}
for r in rows:
    by_cat.setdefault(r["category"], []).append(r)

splits = {"train": [], "val": [], "test": []}
for cat, items in by_cat.items():
    n = len(items)
    n_train = int(round(n * 0.70))
    n_val = int(round(n * 0.15))
    train = items[:n_train]
    val = items[n_train:n_train + n_val]
    test = items[n_train + n_val:]
    splits["train"].extend(train)
    splits["val"].extend(val)
    splits["test"].extend(test)

for split_name, items in splits.items():
    random.shuffle(items)
    for r in items:
        r["split"] = split_name

all_rows = splits["train"] + splits["val"] + splits["test"]

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.csv")
with open(out_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["filepath", "category", "split"])
    w.writeheader()
    for r in all_rows:
        w.writerow(r)

print(f"\nWrote {len(all_rows)} rows to {out_path}")
print(f"train={len(splits['train'])} val={len(splits['val'])} test={len(splits['test'])}")
