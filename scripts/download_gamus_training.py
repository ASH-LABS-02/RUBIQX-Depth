"""Fetch GAMUS RGB/AGL HDF5 pairs for fine-tuning.

Examples:
  python scripts/download_gamus_training.py --root D:\\DepthWizard\\GAMUS --per-city 4
  python scripts/download_gamus_training.py --root D:\\DepthWizard\\GAMUS

The second command fetches all train/validation RGB and AGL files. GAMUS test
files are deliberately excluded to preserve a held-out benchmark.
"""
from __future__ import annotations

import argparse
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download


REPO = "earthflow/GAMUS"


def sample_files(splits: list[str], per_city: int, seed: int) -> list[str]:
    paths = HfApi().list_repo_files(REPO, repo_type="dataset")
    selected = []
    for split in splits:
        images = {}
        heights = {}
        for path in paths:
            stem = Path(path).stem
            if path.startswith(f"images/{split}/") and path.endswith(".h5"):
                key = re.sub(r"_(rgb|img)$", "", stem.lower())
                images[key] = path
            elif path.startswith(f"heights/{split}/") and path.endswith(".h5"):
                key = re.sub(r"_agl$", "", stem.lower())
                heights[key] = path
        groups = {}
        for key in images.keys() & heights.keys():
            groups.setdefault(key.split("_")[0], []).append(key)
        for city, keys in sorted(groups.items()):
            keys = sorted(keys)
            chosen = random.Random(f"{seed}-{split}-{city}").sample(keys, min(per_city, len(keys)))
            selected.extend(path for key in chosen for path in (images[key], heights[key]))
            print(f"{split}: {city} {len(chosen)} RGB/AGL pairs", flush=True)
    return selected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True, help="dataset directory with enough free space")
    ap.add_argument("--splits", nargs="+", choices=("train", "val", "test"),
                    default=["train", "val"])
    ap.add_argument("--per-city", type=int, default=0,
                    help="limit each city to N pairs for a small pilot; 0 downloads all")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    if a.per_city:
        files = sample_files(a.splits, a.per_city, a.seed)
        if not files:
            raise SystemExit("No matched GAMUS RGB/AGL pairs found")
        print(f"Downloading {len(files)} files to {a.root}", flush=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            tasks = [pool.submit(hf_hub_download, REPO, path, repo_type="dataset",
                                 local_dir=a.root) for path in files]
            for n, future in enumerate(as_completed(tasks), 1):
                future.result()
                if n % 20 == 0 or n == len(tasks):
                    print(f"{n}/{len(tasks)} files ready", flush=True)
    else:
        patterns = [f"{kind}/{split}/*.h5" for split in a.splits
                    for kind in ("images", "heights")]
        print(f"Downloading {patterns} to {a.root}", flush=True)
        snapshot_download(repo_id=REPO, repo_type="dataset", local_dir=a.root,
                          allow_patterns=patterns, max_workers=8)
    print("GAMUS data ready in", a.root, flush=True)


if __name__ == "__main__":
    main()
